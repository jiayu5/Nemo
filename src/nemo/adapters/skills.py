"""Bounded local skill discovery and version-checked, on-demand text reads."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import tempfile

from nemo.core.contracts.errors import ToolFailure
from nemo.core.skills import SkillEntry, skill_directory
from nemo.redaction import redact

MAX_SKILLS = 200
MAX_DIRECTORY_CHARS = 16000
MAX_FILE_BYTES = 128 * 1024
MAX_BODY_CHARS = 16000
MAX_RESOURCES = 100
MAX_IGNORED_FIELDS = 12
MAX_IGNORED_FIELD_CHARS = 40


def _revision(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_path(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative)
    if not relative or parts.is_absolute() or ".." in parts.parts or "\\" in relative:
        raise ToolFailure("Skill resource path must be a relative path without traversal")
    path = root
    for part in parts.parts:
        path = path / part
        if path.is_symlink():
            raise ToolFailure("Skill symlinks are not supported")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ToolFailure("Skill path is outside its root")
    return path


def _text(path: Path) -> tuple[str, str]:
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ToolFailure("Skill file exceeds the size limit")
        text = data.decode("utf-8")
        if any(ord(char) < 32 and char not in "\n\r\t" for char in text):
            raise ToolFailure("Skill file is not supported text")
        return text, _revision(data)
    except (OSError, UnicodeError, RuntimeError):
        raise ToolFailure("Skill file is missing, unreadable or not UTF-8") from None


@dataclass(frozen=True)
class SkillDocument:
    """Restricted Nemo metadata plus the workflow body of one skill document."""

    name: str
    description: str
    body: str
    ignored_fields: tuple[str, ...] = ()


def parse_skill_document(text: str) -> SkillDocument:
    """Read name, description and body; other hosts' fields are ignored, never copied."""
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise ToolFailure("Skill metadata must start with front matter")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise ToolFailure("Skill metadata is not closed") from None
    metadata: dict[str, str] = {}
    ignored_fields: list[str] = []
    previous = ""
    for index, line in enumerate(lines[1:end], start=2):
        if not line.strip():
            continue
        if line[:1] in (" ", "\t"):
            # Indented lines continue the previous field; only single lines are read.
            if previous in ("name", "description"):
                raise ToolFailure(f"Skill metadata field {previous} must stay on one line")
            continue
        key, separator, value = line.partition(":")
        key = key.strip()
        if not separator or not key:
            raise ToolFailure(f"Skill metadata line {index} must use 'key: value'")
        if key not in ("name", "description"):
            # Other hosts read fields such as license, allowed-tools or metadata.
            if key not in ignored_fields and len(key) <= MAX_IGNORED_FIELD_CHARS:
                if len(ignored_fields) < MAX_IGNORED_FIELDS:
                    ignored_fields.append(key)
            previous = key
            continue
        if key in metadata:
            raise ToolFailure(f"Skill metadata repeats the {key} field")
        value = value.strip()
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ToolFailure("Skill metadata has invalid quoting")
            value = value[1:-1]
        metadata[key] = value
        previous = key
    if "name" not in metadata:
        raise ToolFailure("Skill metadata is missing the name field")
    if "description" not in metadata:
        raise ToolFailure("Skill metadata is missing the description field")
    name = metadata["name"]
    description = metadata["description"]
    body = "\n".join(lines[end + 1:]).strip()
    if (not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
            or len(name) > 64 or redact(name) != name):
        raise ToolFailure("Skill name must use lowercase letters, numbers and hyphens")
    if not description or len(description) > 500:
        raise ToolFailure("Skill description must contain 1 to 500 characters")
    if not body or len(body) > MAX_BODY_CHARS:
        raise ToolFailure("Skill body must contain 1 to 16000 characters")
    return SkillDocument(name, description, body, tuple(ignored_fields))


def _parse(text: str) -> tuple[str, str, str]:
    """Kept for the reused helper scripts; prefer parse_skill_document."""
    document = parse_skill_document(text)
    return document.name, document.description, document.body


class FileSkillCatalog:
    def __init__(self, workspace: Path, user_root: Path, *, include_project: bool = True, enforce_context_limit: bool = True,
                 builtin_root: Path | None = None):
        self.workspace = workspace.resolve()
        self._roots = {"user": user_root.absolute(),
                       "project": self.workspace / ".nemo" / "skills"}
        if not include_project:
            self._roots.pop("project")
        if builtin_root is not None:
            self._roots["builtin"] = builtin_root.absolute()
        self._locations: dict[str, tuple[Path, str]] = {}
        self._resources: dict[str, dict[str, str]] = {}
        entries: dict[str, SkillEntry] = {}
        conflicts = set()
        diagnostics = []
        checked = 0
        for scope, root in self._roots.items():
            # Never follow a symlink in the configured root chain.
            if any(folder.is_symlink() for folder in (root, *root.parents)):
                diagnostics.append(f"{scope}: skill root contains a symlink")
                continue
            try:
                if not root.exists():
                    continue
                with os.scandir(root) as iterator:
                    candidates = []
                    for item in iterator:
                        checked += 1
                        if checked > MAX_SKILLS:
                            raise ToolFailure("Too many skill candidates; reduce the skill directory")
                        candidates.append(item.name)
                for directory in sorted(candidates):
                    # Raw directory names may contain credentials or control text.
                    label = f"{scope}: candidate {len(diagnostics) + len(entries) + 1}"
                    try:
                        folder = _safe_path(root, directory)
                        if not folder.is_dir():
                            continue
                        path = _safe_path(root, f"{directory}/SKILL.md")
                        text, revision = _text(path)
                        name, description, _ = _parse(text)
                        skill_id = f"{scope}:{name}"
                        if skill_id in entries or skill_id in conflicts:
                            conflicts.add(skill_id)
                            entries.pop(skill_id, None)
                            self._locations.pop(skill_id, None)
                            diagnostics.append(f"{skill_id}: duplicate skill name")
                            continue
                        entries[skill_id] = SkillEntry(skill_id, name, redact(description), revision)
                        self._locations[skill_id] = (root, directory)
                    except ToolFailure as exc:
                        diagnostics.append(f"{label}: {exc.public_message}")
            except OSError:
                diagnostics.append(f"{scope}: skill root is unreadable")
        self.candidate_count = checked
        self.entries = tuple(entries[key] for key in sorted(entries))
        self.diagnostics = tuple(diagnostics)
        self._entries = {entry.id: entry for entry in self.entries}
        if enforce_context_limit and len(skill_directory(self)) > MAX_DIRECTORY_CHARS:
            raise ToolFailure("Skill directory exceeds the context limit; reduce installed skills")

    def _folder(self, skill_id: str) -> Path:
        if skill_id not in self._entries:
            raise ToolFailure("Skill is not in the loaded directory; reload the session")
        root, directory = self._locations[skill_id]
        if any(folder.is_symlink() for folder in (root, *root.parents)):
            raise ToolFailure("Skill root contains a symlink")
        return _safe_path(root, directory)

    def _body(self, skill_id: str) -> tuple[Path, str]:
        folder = self._folder(skill_id)
        text, revision = _text(_safe_path(folder, "SKILL.md"))
        if revision != self._entries[skill_id].revision:
            raise ToolFailure("Skill changed; reload the session before using it")
        _, _, body = _parse(text)
        return folder, body

    @staticmethod
    def _walk_error(error: OSError) -> None:
        raise ToolFailure("Skill resources are unreadable") from None

    def read(self, skill_id: str) -> dict:
        folder, body = self._body(skill_id)
        resources = {}
        checked = 0
        try:
            for current, directories, files in os.walk(folder, followlinks=False, onerror=self._walk_error):
                directories.sort()
                files.sort()
                checked += len(directories) + len(files)
                if checked > MAX_RESOURCES:
                    raise ToolFailure("Skill has too many resource entries")
                directories[:] = [name for name in directories
                                   if not (Path(current) / name).is_symlink()]
                for name in files:
                    relative = (Path(current) / name).relative_to(folder).as_posix()
                    if relative == "SKILL.md" or redact(relative) != relative:
                        continue
                    try:
                        _, revision = _text(_safe_path(folder, relative))
                    except ToolFailure:
                        continue
                    resources[relative] = revision
        except OSError:
            raise ToolFailure("Skill resources are unreadable") from None
        self._resources[skill_id] = resources
        return {"id": skill_id, "revision": self._entries[skill_id].revision,
                "body": redact(body), "resources": sorted(resources)}

    def read_resource(self, skill_id: str, path: str, offset: int, limit: int) -> dict:
        folder, _ = self._body(skill_id)
        target = _safe_path(folder, path)
        if skill_id not in self._resources:
            raise ToolFailure("Read the skill before reading its resources")
        if path not in self._resources[skill_id]:
            raise ToolFailure("Resource is not in the loaded skill resource list")
        text, revision = _text(target)
        if revision != self._resources[skill_id][path]:
            raise ToolFailure("Skill resource changed; read the skill again")
        if offset > len(text):
            raise ToolFailure("Resource offset is beyond the text")
        end = min(len(text), offset + limit)
        return {"id": skill_id, "path": redact(path), "revision": revision,
                "text": redact(text[offset:end]), "next_offset": end if end < len(text) else None}


class FileSkillManagementStore:
    """Manual imports use the same parser but never modify session catalogs."""

    def __init__(self, user_root: Path, *, builtin_root: Path | None = None):
        self.user_root = user_root.absolute()
        self.builtin_root = builtin_root

    def _catalog(self, workspace: Path | None) -> FileSkillCatalog:
        return FileSkillCatalog(workspace or self.user_root, self.user_root,
                                include_project=workspace is not None, enforce_context_limit=False,
                                builtin_root=self.builtin_root)

    @staticmethod
    def _entry(entry: SkillEntry) -> dict:
        return {"id": entry.id, "name": entry.name, "description": entry.description,
                "scope": entry.id.split(":", 1)[0]}

    def list_installed(self, workspace: Path | None) -> dict:
        catalog = self._catalog(workspace)
        return {"entries": [self._entry(entry) for entry in catalog.entries],
                "diagnostics": list(catalog.diagnostics)}

    def detail(self, workspace: Path | None, skill_id: str) -> dict:
        catalog = self._catalog(workspace)
        entry = next((entry for entry in catalog.entries if entry.id == skill_id), None)
        if entry is None:
            raise ToolFailure("Skill is not installed or has invalid metadata")
        _, body = catalog._body(skill_id)
        return {**self._entry(entry), "body": redact(body)}

    def _root(self, workspace: Path | None, scope: str) -> Path:
        if scope == "user":
            root = self.user_root
        elif scope == "project" and workspace is not None:
            root = workspace.resolve() / ".nemo" / "skills"
        else:
            raise ToolFailure("Select a session to import a project skill")
        if any(folder.is_symlink() for folder in (root, *root.parents)):
            raise ToolFailure("Skill root contains a symlink")
        return root

    def preview(self, workspace: Path | None, scope: str, content: str) -> dict:
        try:
            data = content.encode("utf-8")
        except UnicodeError:
            raise ToolFailure("Skill document must be UTF-8") from None
        if len(data) > MAX_FILE_BYTES:
            raise ToolFailure("Skill file exceeds the size limit")
        if any(ord(char) < 32 and char not in "\n\r\t" for char in content):
            raise ToolFailure("Skill file is not supported text")
        if redact(content) != content:
            raise ToolFailure("Skill document contains credential-shaped text; remove it before importing")
        document = parse_skill_document(content)
        name, description, body = document.name, document.description, document.body
        root = self._root(workspace, scope)
        catalog = self._catalog(workspace)
        skill_id = f"{scope}:{name}"
        if (any(entry.id == skill_id for entry in catalog.entries)
                or any(item.startswith(f"{skill_id}: duplicate") for item in catalog.diagnostics)
                or (root / name).exists() or (root / name).is_symlink()):
            raise ToolFailure("A skill with this name or target directory already exists; choose a different name")
        if catalog.candidate_count >= MAX_SKILLS:
            raise ToolFailure("Too many skill candidates; reduce the skill directory")
        addition = SkillEntry(skill_id, name, description, _revision(data))
        # Use the real serializer so an accepted import can also be loaded later.
        catalog.entries = (*catalog.entries, addition)
        if len(skill_directory(catalog)) > MAX_DIRECTORY_CHARS:
            raise ToolFailure("Skill directory would exceed the context limit")
        return {"id": skill_id, "name": name, "description": description, "scope": scope,
                "body": body, "ignored_fields": list(document.ignored_fields)}

    def install(self, workspace: Path | None, scope: str, content: str) -> dict:
        preview = self.preview(workspace, scope, content)
        root = self._root(workspace, scope)
        target = root / preview["name"]
        created = False
        try:
            root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._root(workspace, scope)
            # Stage on the same filesystem, outside the discoverable skill root.
            with tempfile.TemporaryDirectory(prefix=".nemo-skill-import-", dir=root.parent) as temporary:
                staged = Path(temporary) / "SKILL.md"
                with staged.open("xb") as handle:
                    os.chmod(staged, 0o600)
                    handle.write(content.encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
                target.mkdir(mode=0o700)
                created = True
                os.link(staged, target / "SKILL.md")
            return preview
        except FileExistsError:
            raise ToolFailure("Skill target already exists; no file was overwritten") from None
        except OSError:
            raise ToolFailure("Could not import skill into storage") from None
        finally:
            if created and not (target / "SKILL.md").exists():
                # Only remove the empty directory created by this failed import.
                try:
                    target.rmdir()
                except OSError:
                    pass
