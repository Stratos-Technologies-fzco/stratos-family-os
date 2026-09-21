#!/usr/bin/env node
"use strict";
// Launcher: runs the Python CLI from the private environment created at install time and
// passes through arguments, input/output and the exit code unchanged.

const { spawn } = require("child_process");
const { install, isInstalled, venvPython } = require("../scripts/setup");

if (!isInstalled() && !install()) {
  process.exit(9); // Stratos exit code 9: missing dependency
}

const child = spawn(venvPython, ["-m", "stratos", ...process.argv.slice(2)], { stdio: "inherit" });
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => child.kill(signal));
}
child.on("error", (error) => {
  console.error(`Could not start Stratos: ${error.message}`);
  process.exit(1);
});
child.on("exit", (code, signal) => process.exit(code === null ? (signal ? 130 : 1) : code));
