"""Build the macOS Python Server binary that Tauri embeds as a sidecar."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    rustc = shutil.which("rustc") or str(Path.home() / ".cargo" / "bin" / "rustc")
    target = subprocess.check_output((rustc, "--print", "host-tuple"), text=True).strip()
    if target not in {"aarch64-apple-darwin", "x86_64-apple-darwin"}:
        raise RuntimeError(f"Unsupported desktop target: {target}")

    name = f"nemo-server-{target}"
    destination = root / "apps" / "ui" / "src-tauri" / "binaries" / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nemo-sidecar-") as temporary:
        build_dir = Path(temporary)
        subprocess.run((
            sys.executable, "-m", "PyInstaller", "--onefile", "--noconfirm",
            "--name", name,
            "--distpath", str(build_dir / "dist"),
            "--workpath", str(build_dir / "work"),
            "--specpath", str(build_dir / "spec"),
            "--collect-submodules", "nemo",
            str(root / "src" / "nemo" / "server" / "desktop.py"),
        ), cwd=root, check=True)
        shutil.copy2(build_dir / "dist" / name, destination)
    print(f"Built {destination}")


if __name__ == "__main__":
    main()
