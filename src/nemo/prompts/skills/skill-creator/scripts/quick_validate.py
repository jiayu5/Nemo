#!/usr/bin/env python3
# Derived from the Codex skill-creator; adapted for Nemo. See ../license.txt.
"""
Quick validation script for skills - minimal version
"""

import re
import sys
from pathlib import Path

from nemo.adapters.skills import FileSkillCatalog, _parse, _text
from nemo.core.contracts.errors import ToolFailure

MAX_SKILL_NAME_LENGTH = 64


def validate_skill(skill_path):
    """Basic validation of a skill"""
    skill_path = Path(skill_path)

    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        return False, "SKILL.md not found"

    content = skill_md.read_text()
    if not content.startswith("---"):
        return False, "No YAML frontmatter found"

    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return False, "Invalid frontmatter format"

    try:
        _, description, _ = _parse(content)
        _text(skill_md)
    except ToolFailure as exc:
        return False, exc.public_message
    if description.startswith("[TODO:"):
        return False, "Description contains an unfinished TODO placeholder"
    # Reuse discovery and resource validation, including the real directory limits.
    if any(path.is_symlink() for path in (skill_path, *skill_path.parents)):
        return False, "Skill symlinks are not supported"
    if any(path.is_symlink() for path in skill_path.rglob("*")):
        return False, "Skill symlinks are not supported"
    try:
        catalog = FileSkillCatalog(skill_path.parent, skill_path.parent, include_project=False)
        name, _, _ = _parse(content)
        if f"user:{name}" not in {entry.id for entry in catalog.entries}:
            return False, "Skill cannot be discovered; check duplicate names and directory limits"
        catalog.read(f"user:{name}")
    except ToolFailure as exc:
        return False, exc.public_message

    body = content[match.end() :]
    fence_marker = None
    fence_length = 0
    for line in body.splitlines():
        fence = re.match(r"^[ \t]*(?:(?:[-+*]|\d+[.)])[ \t]+)?(`{3,}|~{3,})(.*)$", line)
        if fence:
            marker = fence.group(1)
            if fence_marker is None:
                fence_marker = marker[0]
                fence_length = len(marker)
            elif (
                marker[0] == fence_marker
                and len(marker) >= fence_length
                and not fence.group(2).strip()
            ):
                fence_marker = None
                fence_length = 0
            continue

        if fence_marker is None and re.fullmatch(
            r"[ ]{0,3}\[TODO:[^\n]*\][ \t]*", line
        ):
            return False, "Skill instructions contain an unfinished TODO placeholder"

    return True, "Skill is valid!"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python quick_validate.py <skill_directory>")
        sys.exit(1)

    valid, message = validate_skill(sys.argv[1])
    print(message)
    sys.exit(0 if valid else 1)
