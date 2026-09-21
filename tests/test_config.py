import os
import tempfile
import unittest
from pathlib import Path

from nemo.config.loader import load_config
from nemo.config.secrets import SecretLoader
from nemo.core.contracts.errors import ConfigError, MissingSecretError, UnknownReferenceError
from nemo.core.contracts.secrets import SecretValue

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
