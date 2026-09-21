# Publishing the npm package

The npm installer (`npm/`) is published to **GitHub Packages** as
`@stratos-technologies-fzco/stratos-cli`. The scope must equal the GitHub organisation name in
lower case. Publishing is a deliberate, manual step: nothing in CI publishes automatically.

## Who can publish
A person who is a **member of the `Stratos-Technologies-fzco` organisation with write access to the
`stratos-family-os` repository** (packages are linked to that repository through `repository` in
`npm/package.json`).

## One-time preparation
1. Create a GitHub personal access token (classic) with the scopes **`write:packages`** and
   **`read:packages`** (add `repo` if the repository is private). If the organisation enforces SSO,
   authorise the token for it.
2. Tell npm about it (never commit this file; it lives in your home folder):
   ```
   npm config set @stratos-technologies-fzco:registry https://npm.pkg.github.com
   npm config set //npm.pkg.github.com/:_authToken YOUR_TOKEN
   ```

## Every release
1. Bump the version in `pyproject.toml` and `src/stratos/__init__.py` (semantic versioning), update
   `CHANGELOG.md`.
2. Run the full gate: `make check` then `make verify-package`.
3. Build the package: `make npm-pack` (syncs `npm/package.json`'s version, bundles the wheel,
   creates `npm/*.tgz`).
4. Look before you publish: `cd npm && npm publish --dry-run` (lists the files that would be
   published; there must be exactly one `wheels/*.whl`, no `.env`, no keys).
5. Test the tarball in a throw-away location:
   `npm install -g --prefix <empty folder> ./stratos-technologies-fzco-stratos-cli-X.Y.Z.tgz`
   then `<empty folder>/stratos --version`.
6. Publish: `cd npm && npm publish`
7. Verify from a second computer or account: follow the install steps in the User Manual, section
   3.7.
8. Tag the release: `git tag vX.Y.Z` and push the tag.

## Things to know
- **A version can never be re-used.** To fix a mistake publish a new patch version. A wrongly
  published version can be deleted in GitHub (Organisation, Packages, package settings), but
  anyone who already downloaded it keeps their copy.
- **Visibility.** The package inherits the visibility of the linked repository. While
  `stratos-family-os` is public, the package is public, but **GitHub still requires every user to
  authenticate with a token that has `read:packages`** to install it.
- **Public npm instead.** To publish on the public registry rename the package to
  `@<your-npm-org>/stratos-cli`, remove `publishConfig`, run `npm login`, then
  `npm publish --access public`. Anyone in the world could then install it without signing in.
- **What is inside.** Only `bin/`, `scripts/`, the wheel and the README. The wheel contains the
  CLI source; there are no secrets in it (checked by `npm publish --dry-run`).
- **User requirements** are Node.js 18+ and Python 3.12+; they are listed in the README and the
  manual.
