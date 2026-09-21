"""Print how long `stratos --help` takes and how many command modules it imports."""

import subprocess
import sys
import time

CODE = (
    "import sys\nfrom typer.testing import CliRunner\nfrom stratos.cli.app import app\n"
    "CliRunner().invoke(app, ['--help'])\n"
    "print(len([m for m in sys.modules if m.startswith('stratos.cli.commands.')]))"
)
start = time.perf_counter()
out = subprocess.run([sys.executable, "-c", CODE], capture_output=True, text=True)
print(f"--help: {time.perf_counter() - start:.2f}s, command modules imported: {out.stdout.strip()}")
