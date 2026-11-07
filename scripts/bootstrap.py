"""Set up and start SocialAll. The double-click launcher runs this with whatever Python it finds.

First run: makes .venv and installs SocialAll into it (about a minute). Later runs: reinstalls only when
pyproject.toml has changed, then hands over to `python -m socialall.desktop` (by default `launch`).
Uses only the standard library, since it runs before anything is installed.
"""
from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
PROJECT = ROOT / "pyproject.toml"
STAMP = VENV / ".socialall-installed"  # hash of the pyproject.toml last installed


def venv_python(venv: Path = VENV) -> Path:
    return venv / ("Scripts/python.exe" if platform.system() == "Windows" else "bin/python")


def project_hash() -> str:
    return hashlib.sha256(PROJECT.read_bytes()).hexdigest()


def needs_install(stamp: Path = STAMP) -> bool:
    try:
        return stamp.read_text().strip() != project_hash()
    except OSError:
        return True


def fail(message: str) -> None:
    print(f"\n{message}\n", file=sys.stderr)
    sys.exit(1)


def ensure_venv() -> Path:
    if sys.version_info < (3, 11):
        fail(f"SocialAll needs Python 3.11 or newer, and this is {platform.python_version()}. "
             "Get a newer one from https://www.python.org/downloads/")
    py = venv_python()
    if not py.exists():
        print("Setting up SocialAll for the first time. This takes about a minute.", flush=True)
        r = subprocess.run([sys.executable, "-m", "venv", str(VENV)])
        if r.returncode != 0 or not py.exists():
            fail("Couldn't create the .venv folder: Python's venv module is missing or failed.")
    ok = subprocess.run([str(py), "-c", "import sys; sys.exit(sys.version_info < (3, 11))"]).returncode == 0
    if not ok:
        fail(f"{VENV} was made with a Python older than 3.11. Delete that folder and start SocialAll again.")
    if needs_install():
        print("Installing SocialAll's Python packages…", flush=True)
        # Editable, so a git pull is picked up without installing again.
        r = subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-e", str(ROOT)])
        if r.returncode != 0:
            fail("Installing packages failed (see above). Check your internet connection and try again.")
        STAMP.write_text(project_hash())
    return py


def main() -> int:
    py = ensure_venv()
    args = sys.argv[1:]
    if not args or args[0].startswith("-"):  # just options, e.g. --port 8010: they're for launch
        args = ["launch", *args]
    return subprocess.run([str(py), "-m", "socialall.desktop", *args], cwd=ROOT).returncode


if __name__ == "__main__":
    sys.exit(main())
