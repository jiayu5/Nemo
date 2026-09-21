import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nemo.cli.approver import parse_answer
from nemo.cli.main import main
from nemo.cli.render import redact
from nemo.cli.streams import Streams
from nemo.cli.transcript import SessionHeader, append_messages, load_transcript, write_header
from nemo.core.contracts.model_config import ResolvedModel
from nemo.core.contracts.types import Message, ModelResponse, ToolCall
from nemo.core.session import Session
from nemo.core.tools.approval import ApprovalOutcome
from nemo.testing.fakes import FakeModel

SECRET = "sk-live-0123456789abcdef"


def resolved() -> ResolvedModel:
    return ResolvedModel(
        selection="default",
        source="default",
        model_name="test-model",
        model_id="test-model-1",
        protocol="openai_compatible",
        base_url="http://127.0.0.1:1",
        api_key_env="NEMO_TEST_KEY",
        capabilities=frozenset({"tool_calling"}),
    )


class FakeClient:
    """A model client with no transport, for wiring tests."""

    def __init__(self, responses: list[ModelResponse]):
        self.resolved = resolved()
        self.model = FakeModel(responses)

    async def generate(self, request):
        return await self.model.generate(request)


def shell_call(command, id="c1"):
    return ToolCall(id=id, name="run_shell", arguments={"command": command})


class AnswerParsingTests(unittest.TestCase):
    def test_only_clear_yes_answers_allow(self):
        self.assertEqual(parse_answer("y"), ApprovalOutcome.ALLOW_ONCE)
        self.assertEqual(parse_answer("Yes"), ApprovalOutcome.ALLOW_ONCE)
        self.assertEqual(parse_answer("a"), ApprovalOutcome.ALLOW_SESSION)
        self.assertEqual(parse_answer("always"), ApprovalOutcome.ALLOW_SESSION)
        for answer in ("n", "no", "", "  ", "maybe", None, "1"):
            with self.subTest(answer=answer):
                self.assertEqual(parse_answer(answer), ApprovalOutcome.DENY)


class RedactionTests(unittest.TestCase):
    def test_credentials_are_masked(self):
        cases = (
            f"Authorization: Bearer {SECRET}",
            f"curl -H 'x-api-key: {SECRET}'",
            f"api_key={SECRET}",
            f"password: {SECRET}",
            f"echo {SECRET}",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertNotIn(SECRET, redact(text))
                self.assertIn("[redacted]", redact(text))

    def test_the_mask_is_a_word_not_punctuation(self):
        # On resume the model reads this; "***" reads like a value, this reads
        # like a withheld one. The shape stays, only the value goes.
        self.assertEqual(redact(f"echo {SECRET}"), "echo sk-[redacted]")
        self.assertEqual(redact(f"api_key={SECRET}"), "api_key=[redacted]")
        self.assertEqual(
            redact(f"Authorization: Bearer {SECRET}"), "Authorization: Bearer [redacted]"
        )

    def test_ordinary_text_is_untouched(self):
        text = "rm -rf build && ls -la /Users/jiayuli/Documents/Nemo"
        self.assertEqual(redact(text), text)


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "sessions" / "s1.jsonl"

    def header(self, **overrides):
        values = dict(
            session_id="s1", workspace="/tmp/ws", model="m -> m", approval_mode="ask"
        )
        values.update(overrides)
        return SessionHeader(**values)

    def test_round_trip_keeps_messages_exactly(self):
        write_header(self.path, self.header())
        messages = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="hi"),
        ]
        self.assertEqual(append_messages(self.path, messages), 0)

        loaded = load_transcript(self.path)

        self.assertEqual(loaded.header.session_id, "s1")
        self.assertEqual(loaded.header.approval_mode, "ask")
        self.assertEqual([m.model_dump() for m in loaded.messages],
                         [m.model_dump() for m in messages])
        self.assertEqual(loaded.redacted, 0)

    def test_header_is_written_once(self):
        write_header(self.path, self.header(model="first"))
        write_header(self.path, self.header(model="second"))
        self.assertEqual(load_transcript(self.path).header.model, "first")

    def test_credentials_never_reach_disk(self):
        write_header(self.path, self.header())
        changed = append_messages(
            self.path, [Message(role="assistant", content=f"the key is {SECRET}")]
        )

        self.assertEqual(changed, 1)
        self.assertNotIn(SECRET, self.path.read_text(encoding="utf-8"))
        self.assertEqual(load_transcript(self.path).redacted, 1)

    def test_file_is_owner_only(self):
        write_header(self.path, self.header())
        mode = stat.S_IMODE(self.path.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_a_torn_last_line_does_not_cost_the_session(self):
        write_header(self.path, self.header())
        append_messages(self.path, [Message(role="user", content="kept")])
        # Exactly what a killed process leaves behind: half a JSON object.
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write('{"type": "message", "message": {"role": "assis')

        loaded = load_transcript(self.path)

        self.assertEqual([m.content for m in loaded.messages], ["kept"])
        self.assertEqual(loaded.unreadable, 1)
        self.assertEqual(loaded.header.session_id, "s1")

    def test_unreadable_lines_in_the_middle_do_not_lose_the_rest(self):
        write_header(self.path, self.header())
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write("not json at all\n")
        append_messages(self.path, [Message(role="user", content="after the damage")])

        loaded = load_transcript(self.path)

        self.assertEqual([m.content for m in loaded.messages], ["after the damage"])
        self.assertEqual(loaded.unreadable, 1)

    def test_payloads_that_no_longer_validate_are_skipped(self):
        write_header(self.path, self.header())
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write('{"type": "message", "message": {"role": "nonsense"}}\n')
            handle.write('{"type": "something-from-the-future"}\n')
        append_messages(self.path, [Message(role="assistant", content="still here")])

        loaded = load_transcript(self.path)

        self.assertEqual([m.content for m in loaded.messages], ["still here"])
        self.assertEqual(loaded.unreadable, 2)

    def test_a_damaged_header_still_yields_its_messages(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write('{"type": "session", "session_id": "s1"\n')
        append_messages(self.path, [Message(role="user", content="kept")])

        loaded = load_transcript(self.path)

        self.assertIsNone(loaded.header)
        self.assertEqual(len(loaded.messages), 1)
        self.assertEqual(loaded.unreadable, 1)


class SessionTests(unittest.TestCase):
    def result(self, messages):
        class Result:
            pass

        result = Result()
        result.state = Result()
        result.state.messages = messages
        return result

    def test_record_returns_only_new_messages(self):
        session = Session(session_id="s")
        first = [Message(role="user", content="a"), Message(role="assistant", content="b")]
        self.assertEqual(len(session.record(self.result(first))), 2)
        second = first + [Message(role="user", content="c")]
        self.assertEqual([m.content for m in session.record(self.result(second))], ["c"])
        self.assertEqual(len(session.history()), 3)

    def test_record_rejects_a_foreign_run(self):
        session = Session(session_id="s")
        session.record(self.result([Message(role="user", content="a"),
                                    Message(role="assistant", content="b")]))
        with self.assertRaises(ValueError):
            session.record(self.result([Message(role="user", content="unrelated")]))


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.workspace = Path(self._tmp.name).resolve()
        self.sessions = Path(self._tmp.name) / "sessions"

    def run_cli(self, argv, responses, lines=None, is_tty=False):
        written: list[str] = []
        client = FakeClient(responses)
        streams = Streams.scripted(lines or [], written=written, is_tty=is_tty)
        code = main(
            argv,
            client_factory=lambda **_: client,
            streams=streams,
            session_dir=self.sessions,
        )
        return code, "\n".join(written), client

    def base_args(self, *extra):
        return ["--workspace", str(self.workspace), *extra]

    def transcripts(self):
        return sorted(self.sessions.glob("*.jsonl"))

    def test_one_shot_turn_writes_a_transcript(self):
        code, output, _ = self.run_cli(
            self.base_args("hi"), [ModelResponse(content="hello")], ["hi"]
        )

        self.assertEqual(code, 0)
        self.assertIn("hello", output)
        self.assertIn("status  : completed", output)
        loaded = load_transcript(self.transcripts()[0])
        self.assertEqual([m.role for m in loaded.messages], ["user", "assistant"])

    def test_denied_shell_call_is_reported_and_the_turn_finishes(self):
        code, output, client = self.run_cli(
            self.base_args("clean up"),
            [ModelResponse(tool_calls=(shell_call("mkdir build"),)),
             ModelResponse(content="skipped")],
            ["n"],
            is_tty=True,
        )

        self.assertEqual(code, 0)
        self.assertIn("approval needed: $ mkdir build", output)
        self.assertIn("tool.failed", output)
        self.assertIn("[denied]", output)
        self.assertFalse((self.workspace / "build").exists())
        backfilled = next(m for m in client.model.requests[1].messages if m.role == "tool")
        self.assertEqual(backfilled.tool_result.error.code, "denied")

    def test_approved_shell_call_runs(self):
        code, _, _ = self.run_cli(
            self.base_args("make a directory"),
            [ModelResponse(tool_calls=(shell_call("mkdir build"),)),
             ModelResponse(content="done")],
            ["y"],
            is_tty=True,
        )

        self.assertEqual(code, 0)
        self.assertTrue((self.workspace / "build").is_dir())

    def test_read_only_command_is_never_confirmed(self):
        code, output, _ = self.run_cli(
            self.base_args("look around"),
            [ModelResponse(tool_calls=(shell_call("ls -la"),)),
             ModelResponse(content="done")],
            is_tty=True,
        )

        self.assertEqual(code, 0)
        self.assertNotIn("approval needed", output)

    def test_without_a_terminal_approval_is_denied(self):
        code, output, _ = self.run_cli(
            self.base_args("clean up"),
            [ModelResponse(tool_calls=(shell_call("mkdir build"),)),
             ModelResponse(content="skipped")],
            [],
            is_tty=False,
        )

        self.assertEqual(code, 0)
        self.assertNotIn("approval needed", output)
        self.assertFalse((self.workspace / "build").exists())

    def test_interactive_turns_accumulate_history(self):
        code, _, client = self.run_cli(
            self.base_args(),
            [ModelResponse(content="one"), ModelResponse(content="two")],
            ["first", "second", ":exit"],
        )

        self.assertEqual(code, 0)
        self.assertEqual(len(client.model.requests), 2)
        self.assertEqual([m.content for m in client.model.requests[1].messages
                          if m.role in ("user", "assistant")],
                         ["first", "one", "second"])

    def test_mode_can_be_switched_mid_session(self):
        code, output, _ = self.run_cli(
            self.base_args(),
            [ModelResponse(content="ok")],
            [":mode", ":mode full", ":mode sideways", ":exit"],
        )

        self.assertEqual(code, 0)
        self.assertIn("mode     : ask (Codex: Read Only)", output)
        self.assertIn("mode     : full (Codex: Full Access)", output)
        self.assertIn("mode must be one of", output)

    def test_session_can_be_resumed(self):
        self.run_cli(self.base_args("first"), [ModelResponse(content="one")], ["first"])
        session_id = self.transcripts()[0].stem

        code, output, client = self.run_cli(
            self.base_args("second", "--session", session_id),
            [ModelResponse(content="two")],
            ["second"],
        )

        self.assertEqual(code, 0)
        self.assertIn("(resumed, 2 messages)", output)
        history = [m.content for m in client.model.requests[0].messages
                   if m.role in ("user", "assistant")]
        self.assertEqual(history, ["first", "one", "second"])

    def test_resuming_a_damaged_transcript_reports_and_continues(self):
        self.run_cli(self.base_args("first"), [ModelResponse(content="one")], ["first"])
        path = self.transcripts()[0]
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"type": "message", "message": {"role": "assis')

        code, output, client = self.run_cli(
            self.base_args("second", "--session", path.stem),
            [ModelResponse(content="two")],
            ["second"],
        )

        self.assertEqual(code, 0)
        self.assertIn("1 transcript line(s) could not be read", output)
        history = [m.content for m in client.model.requests[0].messages
                   if m.role in ("user", "assistant")]
        self.assertEqual(history, ["first", "one", "second"])

    def test_unknown_session_is_an_error(self):
        code, output, _ = self.run_cli(self.base_args("--session", "nope"), [], [])
        self.assertEqual(code, 2)
        self.assertIn("unknown session: nope", output)

    def test_secrets_never_appear_in_output_or_transcript(self):
        code, output, _ = self.run_cli(
            self.base_args("what is the key"),
            [ModelResponse(tool_calls=(shell_call(f"echo {SECRET}"),)),
             ModelResponse(content=f"the key is {SECRET}")],
            ["y"],
            is_tty=True,
        )

        self.assertEqual(code, 0)
        self.assertNotIn(SECRET, output)
        self.assertNotIn(SECRET, self.transcripts()[0].read_text(encoding="utf-8"))

    def test_list_sessions(self):
        self.run_cli(self.base_args("hi"), [ModelResponse(content="hello")], ["hi"])
        code, output, _ = self.run_cli(self.base_args("--sessions"), [], [])

        self.assertEqual(code, 0)
        self.assertIn(self.transcripts()[0].stem, output)

    def test_instructions_line_reports_the_project_file(self):
        (self.workspace / "AGENTS.md").write_text("Use tabs.", encoding="utf-8")
        with patch("nemo.cli.main.load_user_instructions", return_value=None):
            code, output, _ = self.run_cli(
                self.base_args("hi"), [ModelResponse(content="ok")], ["hi"]
            )

        self.assertEqual(code, 0)
        self.assertIn("instructions: project AGENTS.md (9 chars)", output)

    def test_instructions_line_says_none_when_nothing_is_loaded(self):
        with patch("nemo.cli.main.load_user_instructions", return_value=None):
            _, output, _ = self.run_cli(
                self.base_args("hi"), [ModelResponse(content="ok")], ["hi"]
            )

        self.assertIn("instructions: none", output)

    def test_user_instructions_reach_the_prompt_and_the_startup_line(self):
        with patch("nemo.cli.main.load_user_instructions", return_value="Be terse."):
            _, output, client = self.run_cli(
                self.base_args("hi"), [ModelResponse(content="ok")], ["hi"]
            )

        self.assertIn("instructions: user ~/.nemo/AGENTS.md (9 chars)", output)
        system = client.model.requests[0].messages[0].content
        self.assertIn("# User instructions (from ~/.nemo/AGENTS.md)", system)
        self.assertIn("Be terse.", system)

    def test_transcript_records_the_header_once_across_resumes(self):
        self.run_cli(self.base_args("first"), [ModelResponse(content="one")], ["first"])
        path = self.transcripts()[0]
        self.run_cli(
            self.base_args("second", "--session", path.stem),
            [ModelResponse(content="two")],
            ["second"],
        )

        headers = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                   if json.loads(line)["type"] == "session"]
        self.assertEqual(len(headers), 1)
