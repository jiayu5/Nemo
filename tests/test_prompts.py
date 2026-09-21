import unittest
from datetime import date
from pathlib import Path

from nemo.prompts.local_agent import (
    CLAUSES,
    IDENTITY,
    build_system_prompt,
    clause_names,
)


class PromptShapeTests(unittest.TestCase):
    def test_clause_names_are_unique_and_descriptive(self):
        names = clause_names()
        self.assertEqual(len(names), len(set(names)))
        for name in names:
            with self.subTest(name=name):
                self.assertRegex(name, r"^[a-z][a-z0-9_]+$")

    def test_each_clause_is_short_and_handles_one_thing(self):
        for clause in CLAUSES:
            with self.subTest(clause=clause.name):
                self.assertLess(len(clause.body), 400)
                self.assertGreater(len(clause.body), 60)
                self.assertEqual(clause.body, clause.body.strip())

    def test_clause_set_is_locked(self):
        # Adding or removing a rule is a deliberate act: it changes agent behaviour
        # and should show up in review, not slip in unnoticed.
        self.assertEqual(
            clause_names(),
            (
                "act_when_ready",
                "investigate_first",
                "smallest_change",
                "verify_before_claiming",
                "read_errors",
                "respect_refusals",
                "confirm_irreversible",
                "report_faithfully",
                "stay_in_scope",
                "communication",
                "instruction_files",
            ),
        )


class PromptRenderingTests(unittest.TestCase):
    def build(self, **overrides):
        arguments = {
            "workspace": self.workspace,
            "tools": ("read_file", "run_shell"),
            "os_name": "Darwin",
            "today": date(2026, 9, 17),
        }
        arguments.update(overrides)
        return build_system_prompt(**arguments)

    def setUp(self):
        self.workspace = Path("/tmp/nemo-workspace")

    def test_render_is_byte_identical_for_identical_inputs(self):
        # Prompt caching depends on this: any drift in the stable prefix throws
        # away every cached token behind it.
        self.assertEqual(self.build(), self.build())

    def test_identity_and_environment_come_first(self):
        prompt = self.build()
        self.assertTrue(prompt.startswith(IDENTITY))
        self.assertLess(prompt.index("Workspace root:"), prompt.index("# How you work"))
        # The workspace is printed resolved, matching what the tools actually use
        # (on macOS /tmp resolves to /private/tmp).
        self.assertIn(f"- Workspace root: {self.workspace.resolve()}", prompt)
        self.assertIn("- Platform: Darwin", prompt)
        self.assertIn("- Today: 2026-09-17", prompt)
        self.assertIn("- Tools: read_file, run_shell", prompt)

    def test_tools_list_handles_an_empty_registry(self):
        self.assertIn("- Tools: (none)", self.build(tools=()))

    def test_reply_language_is_a_separate_knob(self):
        self.assertIn("Reply in Chinese", self.build())
        self.assertIn("Reply in English", self.build(reply_language="English"))

    def test_no_vendor_or_volatile_content_leaks_in(self):
        prompt = self.build()
        for forbidden in ("deepseek", "openai", "anthropic", "api key", "httpx"):
            with self.subTest(token=forbidden):
                self.assertNotIn(forbidden, prompt.lower())
        # A time of day would invalidate the cache every single turn.
        self.assertNotRegex(prompt, r"\d{2}:\d{2}")

    def test_environment_rules_stay_out_of_the_behavioural_clauses(self):
        prompt = self.build()
        environment, _, clauses = prompt.partition("# How you work")
        self.assertIn("run_shell starts a fresh process", environment)
        self.assertNotIn("workspace root", clauses.lower())

    def test_prompt_stays_within_budget(self):
        # Rough proxy: ~4 characters per token, so this caps the stable prefix
        # near 1.5k tokens. Anything beyond that belongs in per-turn reminders.
        self.assertLess(len(self.build()), 6000)


class PromptContractTests(unittest.TestCase):
    def test_clauses_do_not_restate_what_the_code_already_enforces(self):
        text = " ".join(clause.body.lower() for clause in CLAUSES)
        # Overwrite safety and workspace confinement are enforced in code; asking
        # the model for them again would weaken the fact that they are guarantees.
        self.assertNotIn("overwrite", text)
        self.assertNotIn("escapes the workspace", text)

    def test_key_behavioural_promises_are_present(self):
        text = " ".join(clause.body for clause in CLAUSES)
        for promise in (
            "Verify before you claim",
            "non-zero exit code is information",
            "do not look for another way to do it",
            "Never invent file contents",
            "Lead with the result",
        ):
            with self.subTest(promise=promise):
                self.assertIn(promise, text)
