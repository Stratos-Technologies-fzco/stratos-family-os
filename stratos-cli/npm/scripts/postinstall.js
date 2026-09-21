"use strict";
// Runs after `npm install`. A failure here never aborts the npm install: the launcher retries
// the setup on first use and explains what is missing.

const { install, isInstalled } = require("./setup");

try {
  if (!isInstalled() && !install()) {
    console.error("\nStratos CLI was downloaded but is not ready yet. Follow the message above; the");
    console.error("first run of `stratos` will retry the setup.\n");
  } else {
    console.error("Stratos CLI is ready. Try:  stratos --version");
  }
} catch (error) {
  console.error(`Stratos CLI setup did not complete: ${error.message}`);
}
process.exit(0);
