"""One-shot developer setup: creates .venv, installs dependencies, checks the toolchain.

Run: python scripts/bootstrap.py      (or `make setup`)
"""

import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
PACKAGES = [
    "pytest",
    "pytest-cov",
    "ruff",
    "mypy",
    "types-PyYAML",
    "build",
    "pip-audit",
    "cyclonedx-bom",
]


def main() -> None:
    if sys.version_info < (3, 12):  # noqa: UP036 - friendly message for older interpreters
        sys.exit("Python 3.12 or newer is required.")
    if not VENV.exists():
        print("Creating .venv ...")
        venv.create(VENV, with_pip=True)
    python = VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.check_call(
        [str(python), "-m", "pip", "install", "-q", "-e", ".[openai,pdf]", *PACKAGES], cwd=ROOT
    )
    subprocess.check_call([str(python), "-m", "stratos", "--version"], cwd=ROOT)
    print("\nReady. Activate the environment, then run `make check`.")
    print("Offline stand-ins for external services: `make mock-services`.")


if __name__ == "__main__":
    main()
