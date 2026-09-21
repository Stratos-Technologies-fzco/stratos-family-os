# Stratos CLI (npm installer)

One-time setup (GitHub Packages requires a sign-in even for public packages). Create a GitHub
personal access token (classic) with the `read:packages` scope, then:

```
npm config set @stratos-technologies-fzco:registry https://npm.pkg.github.com
npm config set //npm.pkg.github.com/:_authToken YOUR_GITHUB_TOKEN
```

Then install:

```
npm install -g @stratos-technologies-fzco/stratos-cli
stratos --version
```

This package installs the Python-based Stratos CLI so it can be installed the way most developers
install tools. It bundles the CLI and, on install, creates a private Python environment inside the
package, so nothing is added to your system Python.

**Requirements:** Node.js 18+, **Python 3.12+** on PATH, and internet access during install (Python
dependencies are downloaded).

**Trouble?**
- "Python 3.12 or newer was not found": install Python, then `npm rebuild -g @stratos-technologies-fzco/stratos-cli`.
- Behind a proxy: set `HTTPS_PROXY` before installing.
- `stratos` not found after install: open a new terminal; check `npm prefix -g` is on your PATH.

After installing, follow the User Manual (`docs/Stratos_CLI_User_Manual.pdf`) from "First-time setup".
Uninstall: `npm uninstall -g @stratos-technologies-fzco/stratos-cli`.
