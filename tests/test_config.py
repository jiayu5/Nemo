import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from nemo.config.editor import ConfigEditor, render_toml
from nemo.config.loader import load_config
from nemo.config.secrets import SecretLoader
from nemo.core.contracts.errors import ConfigError, MissingSecretError, UnknownReferenceError
from nemo.core.contracts.secrets import SecretValue
from nemo.core.contracts.model_config import ModelConfig, ProviderConfig

VALID_CONFIG = """
default = "chat"

[providers.deepseek]
protocol = "openai_compatible"
base_url = "https://api.deepseek.com/v1/"
api_key_env = "DEMO_API_KEY"

[models.chat-model]
provider = "deepseek"
model_id = "chat-model"
capabilities = ["tool_calling"]

[models.plain-model]
provider = "deepseek"
model_id = "plain-model"

[aliases]
fast = "chat-model"

[profiles.chat]
model = "chat-model"

[profiles.chat.parameters]
temperature = 0.5
"""


class ConfigTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def write(self, text, name="config.toml"):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path


class LoaderTests(ConfigTestCase):
    def test_valid_config_loads_and_normalizes(self):
        config = load_config(self.write(VALID_CONFIG))
        self.assertEqual(config.default, "chat")
        self.assertEqual(sorted(config.providers), ["deepseek"])
        self.assertEqual(config.profiles["chat"].parameters, {"temperature": 0.5})
        # Trailing slashes are normalized so URL joining cannot double them up.
        self.assertEqual(config.providers["deepseek"].base_url, "https://api.deepseek.com/v1")

    def test_missing_file(self):
        with self.assertRaises(ConfigError) as caught:
            load_config(self.dir / "absent.toml")
        self.assertIn("not found", str(caught.exception))

    def test_invalid_toml_syntax(self):
        with self.assertRaises(ConfigError) as caught:
            load_config(self.write('default = "chat\n'))
        self.assertIn("Invalid TOML", str(caught.exception))

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(ConfigError) as caught:
            load_config(self.write(VALID_CONFIG + "\n[extra]\nkey = 1\n"))
        self.assertIn("extra", str(caught.exception))

    def test_unknown_protocol_value(self):
        broken = VALID_CONFIG.replace(
            'protocol = "openai_compatible"', 'protocol = "openai_compatible_v2"'
        )
        with self.assertRaises(ConfigError) as caught:
            load_config(self.write(broken))
        self.assertIn("providers.deepseek.protocol", str(caught.exception))

    def test_base_url_must_be_http(self):
        broken = VALID_CONFIG.replace("https://api.deepseek.com/v1/", "api.deepseek.com")
        with self.assertRaises(ConfigError) as caught:
            load_config(self.write(broken))
        self.assertIn("base_url", str(caught.exception))

    def test_unknown_provider_reference(self):
        broken = VALID_CONFIG.replace('provider = "deepseek"', 'provider = "missing"')
        with self.assertRaises(UnknownReferenceError) as caught:
            load_config(self.write(broken))
        self.assertIn("unknown provider: missing", str(caught.exception))

    def test_unknown_alias_target(self):
        broken = VALID_CONFIG.replace('fast = "chat-model"', 'fast = "nope"')
        with self.assertRaises(UnknownReferenceError):
            load_config(self.write(broken))

    def test_alias_and_profile_name_collision_is_rejected(self):
        broken = VALID_CONFIG.replace('fast = "chat-model"', 'chat = "chat-model"')
        with self.assertRaises(UnknownReferenceError) as caught:
            load_config(self.write(broken))
        self.assertIn("ambiguous", str(caught.exception))

    def test_unknown_default(self):
        broken = VALID_CONFIG.replace('default = "chat"', 'default = "ghost"')
        with self.assertRaises(UnknownReferenceError):
            load_config(self.write(broken))

    def test_reserved_parameters_rejected(self):
        broken = VALID_CONFIG.replace("temperature = 0.5", 'stream = true')
        with self.assertRaises(ConfigError) as caught:
            load_config(self.write(broken))
        self.assertIn("adapter-owned", str(caught.exception))


class SecretLoaderTests(ConfigTestCase):
    def test_environment_wins_over_file(self):
        env_file = self.write("DEMO_API_KEY=from-file\n", name=".env")
        loader = SecretLoader(env_file=env_file, environ={"DEMO_API_KEY": "from-env"})
        self.assertEqual(loader.load("DEMO_API_KEY").reveal(), "from-env")
        self.assertEqual(loader.source("DEMO_API_KEY"), "environment")

    def test_file_fallback_and_parsing(self):
        env_file = self.write(
            "# comment\n"
            "\n"
            "export QUOTED='single-value'\n"
            'DOUBLE="double-value"\n'
            "NOT_AN_ENTRY\n"
            "EMPTY=\n",
            name=".env",
        )
        loader = SecretLoader(env_file=env_file, environ={})
        self.assertEqual(loader.load("QUOTED").reveal(), "single-value")
        self.assertEqual(loader.load("DOUBLE").reveal(), "double-value")
        self.assertEqual(loader.source("EMPTY"), None)

    def test_missing_secret_names_the_variable_and_file(self):
        env_file = self.dir / ".env"
        loader = SecretLoader(env_file=env_file, environ={})
        with self.assertRaises(MissingSecretError) as caught:
            loader.load("DEMO_API_KEY")
        message = str(caught.exception)
        self.assertIn("DEMO_API_KEY", message)
        self.assertIn(str(env_file), message)

    def test_secret_never_prints_itself(self):
        secret = SecretValue("sk-abcdefghijklmnop")
        self.assertNotIn("abcdefghijklmnop", repr(secret))
        self.assertNotIn("abcdefghijklmnop", str(secret))
        self.assertEqual(secret.reveal(), "sk-abcdefghijklmnop")

    def test_redaction_rewrites_matches_and_skips_short_values(self):
        secret = SecretValue("sk-abcdefghijklmnop")
        self.assertEqual(
            secret.redact("failed with sk-abcdefghijklmnop"), "failed with ***"
        )
        self.assertEqual(SecretValue("abc").redact("abc def"), "abc def")

    def test_permission_warning_tracks_file_mode(self):
        env_file = self.write("DEMO_API_KEY=value\n", name=".env")
        loader = SecretLoader(env_file=env_file, environ={})
        os.chmod(env_file, 0o644)
        self.assertIn("chmod 600", loader.permission_warning())
        os.chmod(env_file, 0o600)
        self.assertIsNone(loader.permission_warning())
        env_file.unlink()
        self.assertIsNone(loader.permission_warning())


class ConfigEditorTests(ConfigTestCase):
    def editor(self, *, environ=None):
        return ConfigEditor(
            config_path=self.dir / "config.toml",
            secret_path=self.dir / ".env",
            environ=environ or {},
        )

    def test_candidate_preserves_advanced_fields_and_round_trips(self):
        config_path = self.write(
            VALID_CONFIG.replace(
                'api_key_env = "DEMO_API_KEY"',
                'api_key_env = "DEMO_API_KEY"\nheaders = { "X-Tenant" = "nemo" }',
            ).replace(
                'capabilities = ["tool_calling"]',
                'capabilities = ["tool_calling"]\n\n[models.chat-model.parameters]\ntop_p = 0.8',
            )
        )
        editor = self.editor()
        candidate = editor.candidate(
            provider_id="deepseek",
            provider=ProviderConfig(
                protocol="openai_compatible",
                base_url="https://new.example/v1",
                api_key_env="DEMO_API_KEY",
                proxy_env="NEMO_PROXY",
                timeout_seconds=30,
            ),
            model_name="chat-model",
            model=ModelConfig(
                provider="deepseek",
                model_id="new-chat",
                capabilities=frozenset({"tool_calling"}),
            ),
            make_default=False,
        )
        rendered = render_toml(candidate)
        config_path.write_text(rendered, encoding="utf-8")
        reloaded = load_config(config_path)
        self.assertEqual(reloaded.providers["deepseek"].headers, {"X-Tenant": "nemo"})
        self.assertEqual(reloaded.providers["deepseek"].proxy_env, "NEMO_PROXY")
        self.assertEqual(reloaded.models["chat-model"].parameters, {"top_p": 0.8})
        self.assertEqual(reloaded.profiles["chat"].model, "chat-model")

    def test_candidate_rejects_moving_a_model_between_providers(self):
        self.write(VALID_CONFIG)
        with self.assertRaises(ConfigError) as caught:
            self.editor().candidate(
                provider_id="other",
                provider=ProviderConfig(
                    protocol="openai_compatible",
                    base_url="https://other.example/v1",
                    api_key_env="OTHER_API_KEY",
                ),
                model_name="chat-model",
                model=ModelConfig(provider="other", model_id="other-chat"),
                make_default=False,
            )
        self.assertIn("already belongs to provider", str(caught.exception))

    def test_first_provider_and_secrets_are_written_atomically(self):
        editor = self.editor()
        candidate = editor.candidate(
            provider_id="demo",
            provider=ProviderConfig(
                protocol="openai_compatible",
                base_url="https://example.test/v1",
                api_key_env="DEMO_KEY",
                proxy_env="DEMO_PROXY",
            ),
            model_name="demo-model",
            model=ModelConfig(
                provider="demo",
                model_id="demo-v1",
                capabilities=frozenset({"tool_calling"}),
            ),
            make_default=True,
        )
        editor.save(
            candidate,
            {
                "DEMO_KEY": ("replace", 'key "with quotes"'),
                "DEMO_PROXY": ("replace", "http://127.0.0.1:7890"),
            },
        )
        self.assertEqual(load_config(editor.config_path).default, "demo-model")
        loader = SecretLoader(env_file=editor.secret_path, environ={})
        self.assertEqual(loader.load("DEMO_KEY").reveal(), 'key "with quotes"')
        self.assertEqual(loader.load("DEMO_PROXY").reveal(), "http://127.0.0.1:7890")
        self.assertEqual(editor.secret_path.stat().st_mode & 0o777, 0o600)

    def test_env_edit_preserves_comments_and_unrelated_values(self):
        env_path = self.write("# keep\nOTHER=value\nDEMO_KEY=old\n", name=".env")
        editor = self.editor()
        candidate = editor.candidate(
            provider_id="demo",
            provider=ProviderConfig(
                protocol="openai_compatible",
                base_url="https://example.test/v1",
                api_key_env="DEMO_KEY",
            ),
            model_name="demo",
            model=ModelConfig(provider="demo", model_id="demo"),
            make_default=True,
        )
        editor.save(candidate, {"DEMO_KEY": ("delete", None)})
        self.assertEqual(env_path.read_text(), "# keep\nOTHER=value\n")

    def test_process_environment_is_read_only(self):
        editor = self.editor(environ={"DEMO_KEY": "from-process"})
        candidate = editor.candidate(
            provider_id="demo",
            provider=ProviderConfig(
                protocol="openai_compatible",
                base_url="https://example.test/v1",
                api_key_env="DEMO_KEY",
            ),
            model_name="demo",
            model=ModelConfig(provider="demo", model_id="demo"),
            make_default=True,
        )
        with self.assertRaises(ConfigError) as caught:
            editor.save(candidate, {"DEMO_KEY": ("delete", None)})
        self.assertIn("read-only", str(caught.exception))
        self.assertFalse(editor.config_path.exists())

    def test_delete_provider_removes_models_and_repoints_default(self):
        self.write(
            VALID_CONFIG
            + """
[providers.backup]
protocol = "openai_compatible"
base_url = "https://backup.example/v1"
api_key_env = "BACKUP_API_KEY"

[models.backup-model]
provider = "backup"
model_id = "backup-v1"
"""
        )
        editor = self.editor()
        candidate, removed = editor.without_provider("deepseek")
        self.assertEqual(removed, ["chat-model", "plain-model"])
        self.assertEqual(list(candidate.providers), ["backup"])
        self.assertEqual(list(candidate.models), ["backup-model"])
        self.assertEqual(candidate.aliases, {})
        self.assertEqual(candidate.profiles, {})
        self.assertEqual(candidate.default, "backup-model")

    def test_delete_last_provider_is_rejected(self):
        self.write(VALID_CONFIG)
        with self.assertRaises(ConfigError) as caught:
            self.editor().without_provider("deepseek")
        self.assertIn("last configured model", str(caught.exception))

    def test_failed_secret_write_restores_the_previous_config(self):
        config_path = self.write(VALID_CONFIG)
        env_path = self.write("DEMO_API_KEY=old\n", name=".env")
        old_config = config_path.read_text()
        old_env = env_path.read_text()
        editor = self.editor()
        candidate = editor.candidate(
            provider_id="deepseek",
            provider=ProviderConfig(
                protocol="openai_compatible",
                base_url="https://changed.example/v1",
                api_key_env="DEMO_API_KEY",
            ),
            model_name="chat-model",
            model=ModelConfig(provider="deepseek", model_id="changed"),
            make_default=False,
        )
        from nemo.config import editor as editor_module

        real_write = editor_module._atomic_write
        calls = 0

        def fail_second(path, content, mode):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("simulated secret write failure")
            return real_write(path, content, mode)

        with patch("nemo.config.editor._atomic_write", side_effect=fail_second):
            with self.assertRaises(OSError):
                editor.save(candidate, {"DEMO_API_KEY": ("replace", "new")})
        self.assertEqual(config_path.read_text(), old_config)
        self.assertEqual(env_path.read_text(), old_env)
