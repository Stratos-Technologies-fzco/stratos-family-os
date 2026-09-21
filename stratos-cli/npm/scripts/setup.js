"use strict";
// Shared by postinstall and the launcher: finds Python 3.12+, creates a private virtual
// environment inside this package and installs the bundled wheel into it.

const { spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
const venvDir = path.join(root, "runtime", "venv");
const isWin = process.platform === "win32";
const venvPython = path.join(venvDir, isWin ? "Scripts" : "bin", isWin ? "python.exe" : "python");

const CANDIDATES = isWin
  ? [["py", ["-3.13"]], ["py", ["-3.12"]], ["py", ["-3"]], ["python", []], ["python3", []]]
  : [["python3.13", []], ["python3.12", []], ["python3", []], ["python", []]];

function run(cmd, args, options = {}) {
  return spawnSync(cmd, args, { encoding: "utf8", ...options });
}

function findPython() {
  const probe = "import sys; print(int(sys.version_info >= (3, 12)))";
  for (const [cmd, prefix] of CANDIDATES) {
    const result = run(cmd, [...prefix, "-c", probe]);
    if (result.status === 0 && result.stdout.trim() === "1") return [cmd, prefix];
  }
  return null;
}

function findWheel() {
  const dir = path.join(root, "wheels");
  const wheels = fs.existsSync(dir) ? fs.readdirSync(dir).filter((f) => f.endsWith(".whl")) : [];
  return wheels.length ? path.join(dir, wheels.sort().pop()) : null;
}

function isInstalled() {
  return fs.existsSync(venvPython) && run(venvPython, ["-m", "stratos", "--version"]).status === 0;
}

function install(log = console.error) {
  const python = findPython();
  if (!python) {
    log(
      "Stratos CLI needs Python 3.12 or newer, which was not found on this computer.\n" +
        "Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH' on Windows),\n" +
        "then run:  npm rebuild -g @stratos-technologies-fzco/stratos-cli   (or reinstall)."
    );
    return false;
  }
  const wheel = findWheel();
  if (!wheel) {
    log("The Stratos CLI package is missing its bundled wheel; reinstall the npm package.");
    return false;
  }
  const [cmd, prefix] = python;
  log("Stratos CLI: creating a private Python environment ...");
  let step = run(cmd, [...prefix, "-m", "venv", venvDir], { stdio: "inherit" });
  if (step.status !== 0) {
    log("Could not create a Python virtual environment (is the 'venv' module installed?).");
    return false;
  }
  log("Stratos CLI: installing ...");
  step = run(venvPython, ["-m", "pip", "install", "--disable-pip-version-check", "-q", `${wheel}[openai,pdf]`], {
    stdio: "inherit",
  });
  if (step.status !== 0) {
    log(
      "Installing the Python dependencies failed. Check your internet connection and proxy\n" +
        "(set HTTPS_PROXY if needed), then run:  npm rebuild -g @stratos-technologies-fzco/stratos-cli"
    );
    return false;
  }
  return isInstalled();
}

module.exports = { install, isInstalled, venvPython };
