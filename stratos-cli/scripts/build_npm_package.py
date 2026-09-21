"""Build the npm package (a tarball that installs the CLI with `npm install`).

Steps: build the Python wheel, copy it into npm/wheels, set npm/package.json's version to the
Python version, and run `npm pack`. Publishing is a separate, deliberate step (`npm publish`).
Run: python scripts/build_npm_package.py
"""

import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NPM = ROOT / "npm"


def run(*cmd: str, cwd: Path = ROOT) -> str:
    npm_shell = sys.platform == "win32" and cmd[0] == "npm"
    result = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", shell=npm_shell
    )
    if result.returncode != 0:
        sys.exit(f"FAILED: {' '.join(cmd)}\n{result.stdout}\n{result.stderr}")
    return result.stdout


def main() -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    wheels = NPM / "wheels"
    shutil.rmtree(wheels, ignore_errors=True)
    wheels.mkdir(parents=True)
    run(sys.executable, "-m", "build", "--wheel", "--outdir", str(wheels))

    manifest_path = NPM / "package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = version
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    for old in NPM.glob("*.tgz"):
        old.unlink()
    run("npm", "pack", cwd=NPM)
    tarball = next(NPM.glob("*.tgz"))
    print(f"Built {tarball}\nInstall with: npm install -g {tarball}")


if __name__ == "__main__":
    main()
