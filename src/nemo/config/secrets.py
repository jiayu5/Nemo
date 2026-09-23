"""Secret loading from the process environment, falling back to ``~/.nemo/.env``."""

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path

from nemo.core.contracts.errors import MissingSecretError
from nemo.core.contracts.secrets import SecretValue

DEFAULT_SECRET_PATH = Path.home() / ".nemo" / ".env"

_ENTRY = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
class SecretLoader:
    """Resolves secret *names* to :class:`SecretValue` instances.

    The process environment wins over the file so that a one-off override (tests,
    CI, a temporary key) never requires editing the user's file.
    """

    def __init__(
        self,
        *,
        env_file: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.env_file = Path(env_file) if env_file is not None else DEFAULT_SECRET_PATH
        self._environ = dict(os.environ if environ is None else environ)

    def load(self, name: str) -> SecretValue:
        value = self._environ.get(name, "").strip()
        if not value:
            value = self._file_entries().get(name, "").strip()
        if not value:
            raise MissingSecretError(
                f"{name} is not set; add it to {self.env_file} or export it in the environment"
            )
        return SecretValue(value)

    def source(self, name: str) -> str | None:
        """Return ``"environment"`` or ``"file"`` without revealing any value."""

        if self._environ.get(name, "").strip():
            return "environment"
        if self._file_entries().get(name, "").strip():
            return "file"
        return None

    def permission_warning(self) -> str | None:
        """Report a secret file that is readable beyond its owner."""

        if not self.env_file.exists():
            return None
        mode = self.env_file.stat().st_mode & 0o777
        if mode & 0o077:
            return f"{self.env_file} mode is {mode:o}; tighten it with: chmod 600 {self.env_file}"
        return None

    def _file_entries(self) -> dict[str, str]:
        if not self.env_file.exists():
            return {}
        entries: dict[str, str] = {}
        for line in self.env_file.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            match = _ENTRY.match(line)
            if match is None:
                continue
            key, raw = match.group(1), match.group(2)
            if len(raw) >= 2 and raw[0] == raw[-1] == '"':
                try:
                    raw = json.loads(raw)
                except json.JSONDecodeError:
                    raw = raw[1:-1]
            elif len(raw) >= 2 and raw[0] == raw[-1] == "'":
                raw = raw[1:-1]
            entries[key] = raw
        return entries
