import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nemo.adapters.skills import FileSkillCatalog, FileSkillManagementStore, MAX_FILE_BYTES
from nemo.bootstrap import BUILTIN_SKILLS_ROOT, build_skill_catalog, build_skill_tools
from nemo.core.context.builder import ContextBuilder
from nemo.core.contracts.errors import ToolFailure
from nemo.core.contracts.tools import ExecutionContext
from nemo.core.contracts.types import AgentState, ModelResponse, ToolCall
from nemo.core.runtime.agent import AgentRuntime
from nemo.core.skills import skill_directory
from nemo.core.tools.approval import ApprovalMode, ApprovalPolicy
from nemo.core.tools.registry import ToolRegistry
from nemo.testing.fakes import FakeModel


class SkillTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.user = self.root / "user"

    def write_skill(self, name="review", *, scope="project", directory=None,
                    description="Review Python changes", body="Read the diff first."):
        root = self.user if scope == "user" else self.workspace / ".nemo" / "skills"
        folder = root / (directory or name)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "SKILL.md"
        path.write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}\n")
        return path

    def catalog(self):
        return FileSkillCatalog(self.workspace, self.user)

    def registry(self, catalog, *, limit=8000, workspace=None):
        return ToolRegistry(build_skill_tools(catalog),
                            context=ExecutionContext(workspace=workspace or self.workspace,
                                                     max_output_chars=limit),
                            policy=ApprovalPolicy(ApprovalMode.ASK))

    def test_discovery_scopes_and_prefix_exclude_bodies_and_paths(self):
        self.write_skill(scope="user")
        self.write_skill()
        catalog = self.catalog()
        self.assertEqual([e.id for e in catalog.entries], ["project:review", "user:review"])
        prefix = ContextBuilder(skills_directory=skill_directory(catalog)).prefix
        self.assertIn("Review Python changes", prefix)
        self.assertNotIn("Read the diff first", prefix)
        self.assertNotIn(str(self.root), prefix)
        self.assertEqual(prefix, ContextBuilder(skills_directory=skill_directory(catalog)).prefix)

    def test_same_scope_conflicts_have_no_winner(self):
        self.write_skill(directory="one")
        self.write_skill(directory="two")
        catalog = self.catalog()
        self.assertEqual(catalog.entries, ())
        self.assertIn("duplicate", " ".join(catalog.diagnostics))
        with self.assertRaises(ToolFailure):
            catalog.read("project:review")

    def test_invalid_metadata_and_encoding_are_diagnostics(self):
        path = self.write_skill()
        cases = [b"no front matter", b"---\nname: review\nname: other\n---\nx",
                 b"---\nname: review\ndescription: x\ndescription: y\n---\nx",
                 b"---\nname: review\ndescription: >\n  x\n---\nx",
                 b"---\nname: review\n---\nx",
                 b"---\ndescription: x\n---\nx",
                 b"---\nname: Bad_Name\ndescription: x\n---\nx",
                 b"---\nname: review\ndescription: x\n---\n", b"\xff"]
        for data in cases:
            with self.subTest(data=data):
                path.write_bytes(data)
                catalog = self.catalog()
                self.assertFalse(catalog.entries)
                self.assertTrue(catalog.diagnostics)

    def test_other_hosts_metadata_fields_are_ignored(self):
        path = self.write_skill()
        path.write_text("---\nname: review\ndescription: Review Python changes\n"
                        "license: Complete terms in LICENSE.txt\n"
                        "allowed-tools: Bash(git:*) Read\n"
                        "metadata:\n  author: someone\n---\nRead the diff first.\n")
        catalog = self.catalog()
        self.assertEqual([entry.id for entry in catalog.entries], ["project:review"])
        self.assertEqual(list(catalog.diagnostics), [])
        self.assertEqual(catalog.read("project:review")["body"], "Read the diff first.")
        store = FileSkillManagementStore(self.user)
        preview = store.preview(None, "user", path.read_text())
        self.assertEqual(preview["name"], "review")
        self.assertEqual(preview["ignored_fields"], ["license", "allowed-tools", "metadata"])

    def test_unsupported_metadata_messages_name_the_field(self):
        path = self.write_skill()
        messages = {}
        for label, data in (
            ("multiline", "---\nname: review\ndescription: x\n  continued\n---\nx"),
            ("duplicate", "---\nname: review\ndescription: x\nname: other\n---\nx"),
            ("missing", "---\nname: review\n---\nx"),
        ):
            path.write_text(data)
            catalog = self.catalog()
            messages[label] = " ".join(catalog.diagnostics)
        self.assertIn("description must stay on one line", messages["multiline"])
        self.assertIn("repeats the name field", messages["duplicate"])
        self.assertIn("missing the description field", messages["missing"])

    def test_scan_bounds_and_context_budget(self):
        self.write_skill()
        with patch("nemo.adapters.skills.MAX_SKILLS", 0):
            with self.assertRaisesRegex(ToolFailure, "Too many"):
                self.catalog()
        with patch("nemo.adapters.skills.MAX_DIRECTORY_CHARS", 5):
            with self.assertRaisesRegex(ToolFailure, "context limit"):
                self.catalog()

    def test_missing_roots_are_normal(self):
        self.assertEqual(skill_directory(self.catalog()), "")

    def test_symlinks_are_rejected_at_discovery_and_read(self):
        outside = self.root / "outside"
        outside.mkdir()
        path = self.write_skill()
        skill_root = path.parent.parent
        (skill_root / "linked").symlink_to(outside, target_is_directory=True)
        catalog = self.catalog()
        self.assertEqual(len(catalog.entries), 1)
        self.assertIn("symlink", " ".join(catalog.diagnostics))
        resource = path.parent / "escape.txt"
        resource.symlink_to(outside / "secret.txt")
        with self.assertRaises(ToolFailure):
            catalog.read_resource("project:review", "escape.txt", 0, 100)

    def test_root_replaced_by_symlink_cannot_be_used(self):
        path = self.write_skill()
        catalog = self.catalog()
        root = path.parent.parent
        root.rename(root.with_name("old-skills"))
        root.symlink_to(root.with_name("old-skills"), target_is_directory=True)
        with self.assertRaisesRegex(ToolFailure, "symlink"):
            catalog.read("project:review")

    def test_directory_is_frozen_until_reloaded(self):
        path = self.write_skill()
        catalog = self.catalog()
        directory = skill_directory(catalog)
        self.write_skill("new-skill")
        self.assertEqual(directory, skill_directory(catalog))
        self.assertNotIn("new-skill", directory)
        path.write_text(path.read_text().replace("Read the diff first.", "New workflow"))
        with self.assertRaisesRegex(ToolFailure, "changed"):
            catalog.read("project:review")
        loaded = self.catalog()
        self.assertEqual(len(loaded.entries), 2)
        self.assertEqual(loaded.read("project:review")["body"], "New workflow")

    def test_resource_paging_and_version_checks(self):
        path = self.write_skill()
        resource = path.parent / "checklist.md"
        resource.write_text("0123456789")
        catalog = self.catalog()
        with self.assertRaisesRegex(ToolFailure, "Read the skill"):
            catalog.read_resource("project:review", "checklist.md", 0, 4)
        self.assertEqual(catalog.read("project:review")["resources"], ["checklist.md"])
        page = catalog.read_resource("project:review", "checklist.md", 2, 4)
        self.assertEqual((page["text"], page["next_offset"]), ("2345", 6))
        resource.write_text("updated")
        with self.assertRaisesRegex(ToolFailure, "changed"):
            catalog.read_resource("project:review", "checklist.md", 0, 4)
        catalog.read("project:review")
        self.assertEqual(catalog.read_resource("project:review", "checklist.md", 0, 100)["text"], "updated")

    def test_resource_traversal_binary_and_size_limits(self):
        path = self.write_skill()
        (path.parent / "binary").write_bytes(b"\x00secret")
        (path.parent / "large").write_bytes(b"a" * (MAX_FILE_BYTES + 1))
        catalog = self.catalog()
        self.assertEqual(catalog.read("project:review")["resources"], [])
        for relative in ("../SKILL.md", str(path), "binary", "large", "missing"):
            with self.subTest(relative=relative), self.assertRaises(ToolFailure):
                catalog.read_resource("project:review", relative, 0, 10)
        with patch("nemo.adapters.skills.MAX_RESOURCES", 1):
            with self.assertRaisesRegex(ToolFailure, "too many"):
                catalog.read("project:review")

    async def test_tools_are_read_only_but_do_not_grant_mutation(self):
        self.write_skill()
        registry = self.registry(self.catalog())
        schemas = registry.schemas()
        result = await registry.execute(ToolCall(id="load", name="read_skill",
                                                arguments={"skill_id": "project:review"}))
        self.assertIsNone(result.error)
        self.assertEqual(registry.schemas(), schemas)
        self.assertEqual(registry.policy.session_grants, frozenset())
        self.assertTrue(all(registry.is_read_only(s.name) for s in schemas))

    async def test_context_workspace_and_output_limits_are_enforced(self):
        self.write_skill()
        catalog = self.catalog()
        call = ToolCall(id="load", name="read_skill", arguments={"skill_id": "project:review"})
        for registry in (self.registry(catalog, limit=10), self.registry(catalog, workspace=self.root)):
            result = await registry.execute(call)
            self.assertEqual(result.error.code, "execution_error")
        bad = await self.registry(catalog).execute(ToolCall(
            id="bad", name="read_skill_resource", arguments={"skill_id": "project:review", "path": "x", "offset": -1}))
        self.assertEqual(bad.error.code, "invalid_arguments")

    async def test_paging_directory_and_safe_errors(self):
        self.write_skill("alpha")
        self.write_skill("beta")
        registry = self.registry(self.catalog())
        result = await registry.execute(ToolCall(id="list", name="list_skills", arguments={"offset": 1, "limit": 1}))
        self.assertEqual(result.output["skills"][0]["id"], "project:beta")
        with patch.object(registry._tools["read_skill"].catalog, "read", side_effect=RuntimeError("password=private")):
            failed = await registry.execute(ToolCall(id="load", name="read_skill", arguments={"skill_id": "project:alpha"}))
        self.assertEqual(failed.error.message, "Tool execution failed")

    async def test_fake_model_workflow_and_history_reload(self):
        path = self.write_skill()
        (path.parent / "check.md").write_text("Check cancellation")
        catalog = self.catalog()
        registry = self.registry(catalog)
        model = FakeModel([
            ModelResponse(tool_calls=(ToolCall(id="read", name="read_skill", arguments={"skill_id": "project:review"}),)),
            ModelResponse(tool_calls=(ToolCall(id="resource", name="read_skill_resource", arguments={"skill_id": "project:review", "path": "check.md"}),)),
            ModelResponse(content="Checked"),
            ModelResponse(tool_calls=(ToolCall(id="again", name="read_skill", arguments={"skill_id": "project:review"}),)),
            ModelResponse(content="Rechecked"),
        ])
        runtime = AgentRuntime(model, registry, ContextBuilder(skills_directory=skill_directory(catalog)))
        result = await runtime.run("Review", max_steps=5)
        self.assertEqual(result.state.output, "Checked")
        self.assertIn("Check cancellation", str(model.requests[2]))
        next_result = await runtime.run("Review again", history=result.state.messages, max_steps=5)
        self.assertEqual(next_result.state.output, "Rechecked")
        self.assertEqual(model.requests[0].messages[0], model.requests[-1].messages[0])
        self.assertNotIn("Read the diff first", model.requests[0].messages[0].content)

    async def test_skill_body_can_be_reread_after_m7_compaction(self):
        from nemo.core.context.compaction import ContextWindow
        self.write_skill(body="Check cancellation propagation.")
        catalog = self.catalog()
        registry = self.registry(catalog)
        read = ToolCall(id="load", name="read_skill", arguments={"skill_id": "project:review"})
        model = FakeModel([ModelResponse(tool_calls=(read,)), ModelResponse(content="Read workflow"),
            ModelResponse(content="Earlier workflow checks cancellation"),
            ModelResponse(tool_calls=(read.model_copy(update={"id": "again"}),)),
            ModelResponse(content="Rechecked workflow")])
        builder = ContextBuilder(skills_directory=skill_directory(catalog))
        first = await AgentRuntime(model, registry, builder, context_window=ContextWindow(10000)).run("A" * 3000)
        second = await AgentRuntime(model, registry, builder, context_window=ContextWindow(10000)).run(
            "B" * 3000, history=first.state.messages)
        self.assertEqual(second.state.status.value, "completed", second.state.error)
        self.assertIn("context.compacted", [event.type for event in second.events])
        self.assertIn("Check cancellation propagation", str(model.requests[-1]))
        self.assertIn("project:review", model.requests[-1].messages[0].content)

    def test_builtin_available_in_bootstrap_and_management_without_session(self):
        with patch("nemo.bootstrap.Path.home", return_value=self.root):
            catalog = build_skill_catalog(self.workspace)
        self.assertEqual([entry.id for entry in catalog.entries], ["builtin:skill-creator", "builtin:skill-installer"])
        prefix = skill_directory(catalog)
        self.assertNotIn(str(BUILTIN_SKILLS_ROOT), prefix)
        store = FileSkillManagementStore(self.user, builtin_root=BUILTIN_SKILLS_ROOT)
        entry = store.list_installed(None)["entries"][0]
        self.assertEqual(entry["scope"], "builtin")
        self.assertTrue(store.detail(None, entry["id"])["body"])
        self.assertFalse(self.user.exists())

    def test_builtin_names_do_not_shadow_user_or_project_skills(self):
        self.write_skill("skill-creator", scope="user")
        self.write_skill("skill-creator")
        catalog = FileSkillCatalog(self.workspace, self.user, builtin_root=BUILTIN_SKILLS_ROOT)
        self.assertEqual([entry.id for entry in catalog.entries],
                         ["builtin:skill-creator", "builtin:skill-installer", "project:skill-creator", "user:skill-creator"])
        self.assertEqual(catalog.diagnostics, ())
        self.assertEqual(catalog.read("project:skill-creator")["body"], "Read the diff first.")

    async def test_builtin_creator_load_then_write_complete_project_skill(self):
        from nemo.adapters.tools.filesystem import WriteFileTool
        catalog = FileSkillCatalog(self.workspace, self.user, builtin_root=BUILTIN_SKILLS_ROOT)
        registry = ToolRegistry((*build_skill_tools(catalog), WriteFileTool()),
                                context=ExecutionContext(workspace=self.workspace),
                                policy=ApprovalPolicy(ApprovalMode.FULL))
        files = {
            "SKILL.md": "---\nname: example\ndescription: Process a text file\n---\nUse scripts/process.py; see references/format.md.",
            "scripts/process.py": "print('ok')\n",
            "references/format.md": "Input is UTF-8 text.\n",
            "assets/template.txt": "Result: \n",
        }
        creator_body = catalog.read("builtin:skill-creator")["body"]
        responses = [ModelResponse(tool_calls=(ToolCall(id=f"load-creator-{offset}", name="read_skill",
                      arguments={"skill_id": "builtin:skill-creator", "offset": offset}),))
                     for offset in range(0, len(creator_body), 4000)]
        for index, (path, content) in enumerate(files.items()):
            responses.append(ModelResponse(tool_calls=(ToolCall(id=f"write-{index}", name="write_file",
                arguments={"path": f".nemo/skills/example/{path}", "content": content}),)))
        responses.append(ModelResponse(content="Created; reopen the session to load it."))
        model = FakeModel(responses)
        result = await AgentRuntime(model, registry, ContextBuilder(
            skills_directory=skill_directory(catalog))).run("Create a reusable text skill", max_steps=12)
        self.assertEqual(result.state.status.value, "completed", result.state.error)
        for path, content in files.items():
            self.assertEqual((self.workspace / ".nemo/skills/example" / path).read_text(), content)
        self.assertNotIn("project:example", [entry.id for entry in catalog.entries])
        refreshed = FileSkillCatalog(self.workspace, self.user, builtin_root=BUILTIN_SKILLS_ROOT)
        skill = refreshed.read("project:example")
        self.assertEqual(skill["resources"], sorted(set(files) - {"SKILL.md"}))
        loaded = next(message.tool_result for message in model.requests[1].messages
                      if message.role == "tool")
        self.assertIsNone(loaded.error)
        self.assertEqual(loaded.output["body"], creator_body[:4000])
        pages = [message.tool_result.output["body"] for message in model.requests[-1].messages
                 if message.role == "tool" and message.tool_result.tool_call_id.startswith("load-creator-")]
        self.assertEqual("".join(pages), creator_body)
        self.assertNotIn(catalog.read("builtin:skill-creator")["body"], str(model.requests[0]))

    async def test_long_skill_body_pages_are_complete_and_version_checked(self):
        path = self.write_skill(body="0123456789" * 1000)
        catalog = self.catalog()
        registry = self.registry(catalog)
        body = ""
        offset = 0
        while offset is not None:
            result = await registry.execute(ToolCall(id="page", name="read_skill",
                arguments={"skill_id": "project:review", "offset": offset}))
            self.assertIsNone(result.error)
            body += result.output["body"]
            offset = result.output["next_offset"]
        self.assertEqual(body, "0123456789" * 1000)
        path.write_text(path.read_text().replace("0123456789", "abcdefghij"))
        stale = await registry.execute(ToolCall(id="stale", name="read_skill",
            arguments={"skill_id": "project:review", "offset": 4000}))
        self.assertEqual(stale.error.code, "execution_error")
        self.assertIn("changed", stale.error.message)

    def test_reused_creator_scripts_initialize_generate_and_validate(self):
        import subprocess
        import sys
        scripts = BUILTIN_SKILLS_ROOT / "skill-creator/scripts"
        def run(script, *arguments):
            return subprocess.run([sys.executable, str(scripts / script), *map(str, arguments)],
                                  capture_output=True, text=True, cwd=self.workspace)
        destination = self.workspace / ".nemo/skills"
        initialized = run("init_skill.py", "workflow", "--path", destination,
                          "--resources", "scripts,references,assets")
        self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
        folder = destination / "workflow"
        placeholder = run("quick_validate.py", folder)
        self.assertNotEqual(placeholder.returncode, 0)
        (folder / "SKILL.md").write_text("---\nname: workflow\ndescription: Process data\n---\nProcess the requested input.")
        generated = run("generate_openai_yaml.py", folder)
        self.assertEqual(generated.returncode, 0, generated.stdout + generated.stderr)
        self.assertTrue((folder / "agents/openai.yaml").exists())
        validated = run("quick_validate.py", folder)
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
        duplicate = run("init_skill.py", "workflow", "--path", destination)
        self.assertNotEqual(duplicate.returncode, 0)
        (folder / "SKILL.md").write_text("---\nname: workflow\ndescription: Process data\n"
                                        "license: Complete terms in LICENSE.txt\n"
                                        "metadata:\n  author: someone\n---\nProcess input.")
        validated_extra = run("quick_validate.py", folder)
        self.assertEqual(validated_extra.returncode, 0, validated_extra.stdout + validated_extra.stderr)
        (folder / "SKILL.md").write_text("---\nname: Workflow\ndescription: Process data\n---\nProcess input.")
        incompatible = run("quick_validate.py", folder)
        self.assertNotEqual(incompatible.returncode, 0)

    def test_builtin_installer_full_directory_scopes_conflicts_and_compatibility(self):
        import importlib.util
        import sys
        scripts = BUILTIN_SKILLS_ROOT / "skill-installer/scripts"
        sys.path.insert(0, str(scripts))
        self.addCleanup(sys.path.remove, str(scripts))
        spec = importlib.util.spec_from_file_location("nemo_installer_test", scripts / "install-skill-from-github.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        self.addCleanup(sys.modules.pop, spec.name, None)
        spec.loader.exec_module(module)
        repo = self.root / "repo"
        skill = repo / "skills/external"
        (skill / "scripts").mkdir(parents=True)
        (skill / "assets").mkdir()
        document = "---\nname: external\ndescription: Process input\n---\nRun scripts/process.py."
        (skill / "SKILL.md").write_text(document)
        (skill / "scripts/process.py").write_text("raise RuntimeError('must not execute during installation')")
        (skill / "assets/data.bin").write_bytes(b"\x00\x01")
        arguments = ["--workspace", str(self.workspace), "--repo", "example/repo", "--path", "skills/external"]
        with patch.object(module, "_prepare_repo", return_value=str(repo)), \
             patch.object(module, "_nemo_home", return_value=str(self.user.parent / "nemo-home")):
            user_root = self.user.parent / "nemo-home/skills"
            self.assertEqual(module.main(arguments), 0)
            self.assertEqual((user_root / "external/assets/data.bin").read_bytes(), b"\x00\x01")
            self.assertEqual((user_root / "external/SKILL.md").read_text(), document)
            self.assertEqual(module.main(arguments), 1)
            self.assertEqual(module.main(arguments + ["--scope", "project"]), 0)
            catalog = FileSkillCatalog(self.workspace, user_root, builtin_root=BUILTIN_SKILLS_ROOT)
            self.assertIn("project:external", [entry.id for entry in catalog.entries])
            self.assertIn("user:external", [entry.id for entry in catalog.entries])
            self.assertEqual(module.main(arguments + ["--dest", str(self.root / "other")]), 1)
            (skill / "SKILL.md").write_text(document.replace("description: Process input", "description: Process input\ndescription: other"))
            self.assertEqual(module.main(arguments + ["--name", "incompatible"]), 1)
            self.assertFalse((user_root / "incompatible").exists())
            licensed = document.replace(
                "name: external", "name: licensed\nlicense: Complete terms in LICENSE.txt")
            (skill / "SKILL.md").write_text(licensed)
            self.assertEqual(module.main(arguments + ["--name", "licensed"]), 0)
            self.assertEqual((user_root / "licensed/SKILL.md").read_text(), licensed)
            (skill / "SKILL.md").write_text(document)
            (skill / "scripts/link").symlink_to(skill / "scripts/process.py")
            self.assertEqual(module.main(arguments + ["--name", "linked"]), 1)
            self.assertFalse((user_root / "linked").exists())

    def test_installer_lists_remote_skills_and_marks_valid_installed_scopes(self):
        import runpy
        import sys
        import json
        scripts = BUILTIN_SKILLS_ROOT / "skill-installer/scripts"
        sys.path.insert(0, str(scripts))
        self.addCleanup(sys.path.remove, str(scripts))
        namespace = runpy.run_path(str(scripts / "list-skills.py"))
        self.write_skill("review")
        function = namespace["_installed_skills"]
        with patch.dict(function.__globals__, {"_nemo_home": lambda: str(self.root / "nemo-home")}):
            self.assertEqual(function(str(self.workspace)), {"review"})
        listing = namespace["_list_skills"]
        response = json.dumps([{"type": "dir", "name": "review"},
                               {"type": "dir", "name": "other"},
                               {"type": "file", "name": "README.md"}]).encode()
        with patch.dict(listing.__globals__, {"_request": lambda url: response}):
            self.assertEqual(listing("example/repo", "skills", "main"), ["other", "review"])

    def test_management_previews_and_imports_without_overwriting(self):
        store = FileSkillManagementStore(self.user)
        content = "---\nname: imported\ndescription: Imported skill\n---\nReview the code"
        preview = store.preview(None, "user", content)
        self.assertEqual(preview["name"], "imported")
        self.assertFalse(self.user.exists())
        saved = store.install(None, "user", content)
        path = self.user / "imported" / "SKILL.md"
        self.assertEqual(path.read_text(), content)
        self.assertEqual(store.detail(None, saved["id"])["body"], "Review the code")
        with self.assertRaisesRegex(ToolFailure, "already exists"):
            store.install(None, "user", content)
        self.assertEqual(path.read_text(), content)
        self.assertEqual([e["id"] for e in store.list_installed(None)["entries"]], ["user:imported"])

    def test_management_conflicts_in_other_directories_and_scopes(self):
        store = FileSkillManagementStore(self.user)
        self.write_skill("review", scope="user", directory="different-directory")
        content = "---\nname: review\ndescription: Imported skill\n---\nReview the code"
        with self.assertRaisesRegex(ToolFailure, "already exists"):
            store.install(None, "user", content)
        with self.assertRaisesRegex(ToolFailure, "Select a session"):
            store.install(None, "project", content)
        store.install(self.workspace, "project", content)
        self.assertEqual(len(store.list_installed(self.workspace)["entries"]), 2)
        self.assertEqual(len(store.list_installed(None)["entries"]), 1)

    def test_management_rejects_bad_documents_roots_and_target_symlinks(self):
        store = FileSkillManagementStore(self.user)
        for content in ("invalid", "---\nname: ../bad\ndescription: x\n---\nx",
                        "---\nname: bad\ndescription: x\n---\npassword=sk-live-0123456789abcdef",
                        "x" * (MAX_FILE_BYTES + 1), "\x00"):
            with self.subTest(content=content[:25]), self.assertRaises(ToolFailure):
                store.install(None, "user", content)
        self.assertFalse(self.user.exists())
        self.user.mkdir()
        (self.user / "review").symlink_to(self.root / "absent")
        content = "---\nname: review\ndescription: Imported skill\n---\nReview the code"
        with self.assertRaises(ToolFailure):
            store.install(None, "user", content)
        project_root = self.workspace / ".nemo"
        project_root.symlink_to(self.user, target_is_directory=True)
        with self.assertRaisesRegex(ToolFailure, "symlink"):
            store.install(self.workspace, "project", content)

    def test_import_failure_cleans_only_its_own_incomplete_target(self):
        store = FileSkillManagementStore(self.user)
        content = "---\nname: review\ndescription: Imported skill\n---\nReview the code"
        with patch("nemo.adapters.skills.os.link", side_effect=OSError("private path")):
            with self.assertRaisesRegex(ToolFailure, "Could not import"):
                store.install(None, "user", content)
        self.assertFalse((self.user / "review").exists())
        self.assertEqual(list(self.user.iterdir()), [])
        self.assertFalse(list(self.user.parent.glob(".nemo-skill-import-*")))

    def test_concurrent_imports_have_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        store = FileSkillManagementStore(self.user)
        content = "---\nname: review\ndescription: Imported skill\n---\nReview the code"
        def attempt():
            try:
                store.install(None, "user", content)
                return True
            except ToolFailure:
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: attempt(), range(2)))
        self.assertEqual(sorted(outcomes), [False, True])
        self.assertEqual((self.user / "review" / "SKILL.md").read_text(), content)
