"""Build the wheel, install it into a throw-away venv and smoke-test the installed CLI.

Also checks that pyproject.toml and `stratos.__version__` agree (semantic version).
Run: python scripts/verify_package.py
"""

import re
import subprocess
import sys
import tempfile
import tomllib
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def run(*cmd: str, cwd: Path = ROOT) -> str:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        sys.exit(f"FAILED: {' '.join(cmd)}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def main() -> None:
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    in_code = re.search(
        r'__version__ = "([^"]+)"', (ROOT / "src/stratos/__init__.py").read_text(encoding="utf-8")
    )
    assert in_code and in_code[1] == declared, f"version mismatch: {declared} vs {in_code}"
    assert SEMVER.match(declared), f"{declared} is not a semantic version"

    with tempfile.TemporaryDirectory() as tmp:
        dist = Path(tmp) / "dist"
        run(sys.executable, "-m", "build", "--wheel", "--outdir", str(dist))
        wheel = next(dist.glob("stratos_cli-*.whl"))
        env = Path(tmp) / "venv"
        venv.create(env, with_pip=True)
        bin_dir = env / ("Scripts" if sys.platform == "win32" else "bin")
        exe = ".exe" if sys.platform == "win32" else ""
        run(str(bin_dir / f"python{exe}"), "-m", "pip", "install", "-q", str(wheel))
        out = run(str(bin_dir / f"stratos{exe}"), "--version", cwd=Path(tmp))
        assert declared in out, out
        run(str(bin_dir / f"stratos{exe}"), "--help", cwd=Path(tmp))
    print(f"OK: stratos-cli {declared} builds, installs and starts.")


if __name__ == "__main__":
    main()
