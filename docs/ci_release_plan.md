# From "it works" to "someone else can install it"

## Context

The integration works — 890 host tests pass, ruff `check` and `format` are already
clean, and every hardware question the protocol had is answered. What is missing is
everything around the code: no license, no installation or configuration section in
the README, no `hacs.json`, nothing running the tests on a push, no release tagged,
no pre-commit tooling, and none of the repo hygiene that
`/mnt/c/njs/gpg-windows-relay` already established as this author's standard.

The intended outcome: a stranger can add the repo to HACS or copy the directory by
hand, follow the config flow, and every push is validated by the same checks a HACS
submission would run — with the version derived from commit messages rather than
typed into three files by hand, and with the working copy enforcing locally what CI
enforces remotely.

**gpg-windows-relay is the reference.** Where a decision has already been made
there, it is copied rather than reinvented — hook set, workflow hardening, test
reporting, badge publishing — translated to Python where the tool differs
(commitlint → `cz check`, eslint → ruff check, prettier → ruff format, c8/lcov →
pytest-cov). The one place the translation does not carry over is the commit
`type-enum`: commitlint takes an arbitrary list, commitizen's is fixed unless a
deprecated config block is used, so this repo uses the stock conventional-commits
types and drops the reference's `security` and `deprecate`.

### How commits, versions, tags and releases connect

Four mechanisms, each with one owner:

| | Owner | What it does |
| - | ----- | ------------ |
| Commit message format | prek `commit-msg` hook running `cz check` | Rejects a subject that is not `type(scope): summary` with a type from the permitted list. Enforcement only — it never touches a version |
| Version number | `cz bump`, run by hand | Reads the conventional commits since the last tag, computes the semver step, writes it into `pyproject.toml`, `uv.lock` and `manifest.json`, updates `CHANGELOG.md`, commits, and creates the annotated tag `v<version>` |
| Git tag | `cz bump` | What the *next* bump measures from, and what the release workflow triggers on |
| GitHub Release | `release.yaml`, on the pushed tag | HACS requires a **release**, not merely a tag, and shows users the version from `manifest.json` — so the workflow refuses to publish when tag and manifest disagree |

Nothing in CI decides a version. `cz bump` does, locally, from the history; pushing
the tag it creates is what publishes. This differs from gpg-windows-relay only in
the last row: there, publishing is a local script, because a marketplace is the
consumer. Here the GitHub Release *is* the distribution channel.

### What already exists (do not redo)

- `custom_components/stiebel_eltron_ir/brand/` holds `icon.png`, `icon@2x.png`,
  `logo.png`, `logo@2x.png` and four `dark_` variants. HACS's brands check accepts a
  `brand/` directory inside the integration with at least `icon.png`, falling back
  to the home-assistant/brands repository only when absent — so **brands passes
  as-is**, needs no `ignore`, and no brands PR is required.
- `manifest.json` carries every key HACS requires: `domain`, `name`,
  `documentation`, `issue_tracker`, `codeowners`, `version`.
- Layout already matches HACS's one-integration rule: root `custom_components/`
  holds only `stiebel_eltron_ir/`; the dev-only fixtures under
  `tests/custom_components/` are not scanned.
- `ruff check` and `ruff format --check` (verified with `uvx ruff@0.16.3`) pass on
  all 55 files; the tree has no CRLF, no missing final newlines, and no trailing
  whitespace outside one file (below).
- The history already uses conventional commits (`test:`, `fix:`, `docs:`).

### The one file that must not be reformatted

134 lines of `docs/Stiebel Eltron air conditioner ACP 35.md` end in **exactly one**
trailing space. They are verbatim `[I][remote.pronto:233]` capture logs — the
evidence the protocol was derived from. `--markdown-linebreak-ext=md` preserves a
two-space markdown line break and would strip these, so both a hook `exclude` and an
`.editorconfig` override are required. The exclude pattern is anchored on the
filename only, not on `docs/`, so moving the file does not silently disarm it.

### Every phase ends the same way

1. Exercise what the phase added — not just "the file exists", but the behaviour:
   run the hook, trigger the workflow, open the coverage report.
2. Run the full local suite, both halves:

   ```bash
   uv run pytest -q                      # 890 passed, 166 deselected
   npx --yes @devcontainers/cli exec --workspace-folder ~/src/ha-core -- \
     bash -lc 'cd /workspaces/ha-core && python -m pytest tests/components/stiebel_eltron_ir --no-cov -q'
   ```

3. **Stop.** Dale reviews and commits. No phase begins before the previous one is
   committed.

---

## Phase 0 — Save the plan, create the repository

1. Save this document **unchanged** to `docs/ci_release_plan.md`, the same way
   `docs/ha_ir_platform/plan.md` records the protocol work. From then on it is the
   reference; corrections go in as amendments rather than silent rewrites.
2. Dale creates an **empty, private** repository on GitHub —
   `diablodale/stiebel-eltron-climate-ir`, **no** auto-created README, `.gitignore`
   or LICENSE, since all three arrive from here. While there:
   - set the repository **description** (HACS's `description` check reads it),
   - confirm **Issues** are enabled (HACS's `issues` check; `manifest.json` already
     points `issue_tracker` there).

   **Amendment, found in practice:** topics cannot be added to an empty repository —
   GitHub offers no topic field until the repository has content. They move to phase
   1, immediately after the first push.
3. Wire the remote, but **do not push yet**:

   ```bash
   git remote add origin https://github.com/diablodale/stiebel-eltron-climate-ir.git
   ```

**Nothing leaves this machine until the license is in place.** Private, and unpushed,
means no licence-less code is published even briefly. The first push is the last step
of phase 1, once `LICENSE` is committed and therefore present at `HEAD` of the very
first push. The repository then **stays private through phase 5** and goes public at
the start of phase 6, the first point at which anything depends on it. It is created
now, rather than at the end, because everything later — CI runs, the badges branch,
HACS validation — needs it to exist.

## Phase 1 — License and package metadata

1. `LICENSE` at the root: verbatim Apache License 2.0 from
   <https://www.apache.org/licenses/LICENSE-2.0.txt>, appendix boilerplate filled in
   as `Copyright 2026 Dale Phurrough`.
2. `pyproject.toml` `[project]`, after `description`:

   ```toml
   license = "Apache-2.0"
   license-files = ["LICENSE"]
   readme = "README.md"
   authors = [{ name = "Dale Phurrough" }]
   keywords = ["home-assistant", "hacs", "infrared", "stiebel-eltron", "climate", "acp35"]

   [project.urls]
   Homepage = "https://github.com/diablodale/stiebel-eltron-climate-ir"
   Issues = "https://github.com/diablodale/stiebel-eltron-climate-ir/issues"
   ```

   SPDX string form (PEP 639), not the deprecated table form. No email in public
   metadata; the git history already carries it.
3. Leave the README's trademark notice as it stands — Apache-2.0 §6 grants no
   trademark rights, so the two do not conflict. No `NOTICE` file and no per-file
   license headers either: both are optional under Apache-2.0, and headers across 55
   files are churn with no consumer.
4. **First push.** After Dale commits the license — and not before — push, so the
   very first thing GitHub receives carries `LICENSE` at `HEAD`:

   ```bash
   git push -u origin main
   ```

   Then add the **topics** phase 0 could not: `home-assistant`, `hacs`,
   `custom-component`, `infrared`, `stiebel-eltron`, `climate`, `acp35`. The field
   appears once the repository has content, and HACS's `topics` check needs them.

5. **Dale requires signed commits on `main`.** GitHub → Settings → Rules → Rulesets →
   New branch ruleset, target **Default branch**, enable **Require signed commits**,
   status Active.

   This works while the repository is still private because the account is **GitHub
   Pro**: rulesets are available "in public repositories with GitHub Free … and in
   public and private repositories with GitHub Pro, GitHub Team, and GitHub
   Enterprise Cloud". On Free it would have had to wait for phase 6; it does not, so
   server-side enforcement starts with the first push rather than five phases later.

   **Target `main`, not "All branches".** Phase 5's `update-badges` job pushes
   `chore: update test badges` to the orphan `badges` branch as
   `github-actions[bot]` over HTTPS, and those commits are unsigned — a ruleset
   covering all branches rejects them and the badges silently stop updating. That
   branch holds two CI-written JSON files and no source, so leaving it outside the
   rule costs nothing.

   Nothing is needed on this machine: `commit.gpgsign` and `tag.gpgsign` are already
   `true` and every commit carries a `gpgsig` header. The ruleset makes that a
   property of the repository rather than of one working copy.

   **Leave the two rules the UI pre-selects checked** — *Restrict deletions* and
   *Block force pushes*. Nothing here deletes `main` or rewrites its history: `cz
   bump` makes an ordinary commit and tag, and the badges job pushes to a branch this
   ruleset does not target. The one consequence, for a sole committer working
   directly on `main`, is that amending an already-pushed commit and force-pushing is
   refused. Do not answer that with a bypass entry: **a bypass list applies to the
   whole ruleset**, so admitting force pushes would admit unsigned commits too. Set
   the ruleset to Disabled for the rewrite and re-enable it, or split it in two —
   signed commits with no bypass, deletions and force pushes with one.

**Amendment: going public moves to phase 6.** It was written here; it is deferred to
the last moment at which something actually depends on it, so the repository stays
private as long as it can. The signing ruleset above is *not* deferred with it —
GitHub Pro allows rulesets on private repositories, so the two are independent.

- Phases 2–5 work private. Actions run well inside the plan's monthly minutes and
  these workflows take one to two; hassfest is a container over the checked-out
  workspace; `EnricoMi/publish-unit-test-result-action` writes checks and comments
  in-repo; `hacs/action` authenticates with `${{ github.token }}` rather than reading
  the repository anonymously.
- **Badges are the first hard dependency**, in phase 6. shields.io fetches
  `raw.githubusercontent.com` from its own servers with no credentials and gets a
  404 on private content — GitHub returns 404 rather than 403 so as not to reveal
  that a repository exists — and GitHub's own workflow-status SVGs are equally
  unavailable anonymously.
- One thing to watch in phase 5: if `hacs/action` turns out to need anonymous read
  after all, that is where it surfaces, and the flip moves up to meet it.

**Exercise:** `uv sync` accepts the PEP 639 metadata; after the push, GitHub's
repository sidebar shows **Apache-2.0** — its licence detector reading the file is
the confirmation that the text was pasted intact.

## Phase 2 — Editor and line-ending hygiene

`.gitattributes` — this repo lives on a Windows volume exposed over 9p, so
normalization matters more here than in the reference repo:

```gitattributes
* text=auto eol=lf
*.png binary
```

`.editorconfig`, following the reference repo's but with Python widths:

```ini
root = true

[*]
charset = utf-8
end_of_line = lf
indent_style = space
insert_final_newline = true
trim_trailing_whitespace = true

[*.py]
indent_size = 4
max_line_length = 88

[*.{json,yaml,yml,md}]
indent_size = 2

[*.md]
trim_trailing_whitespace = false
```

The `[*.md]` override keeps an editor from silently destroying the capture logs; the
hook `exclude` in phase 4 is the second line of defence.

**Exercise:** `git add --renormalize .` produces no diff; open the capture document
in VS Code, save it, and confirm `git diff` is empty.

## Phase 3 — README installation and configuration

Insert two sections into [README.md](README.md) between **Supported devices** and
**What is here**, pointing at the existing emitter-placement section rather than
repeating it. Badges come later, in phase 6, once the workflows they link to exist.

**Requirements**

- **Home Assistant 2026.8 or later.** The infrared entity platform arrived in
  2026.6; 2026.8 is the floor this integration is developed and tested against, and
  the number `hacs.json` declares.
- An integration providing an infrared **emitter** entity (`infrared` domain, device
  class emitter). Reference hardware: a KC868-AG running **ESPHome 2026.7.4**. Any
  emitter entity works.
- Optionally an infrared **receiver** entity, without which the integration is
  complete but cannot follow the handset.

**Installation** — *HACS*: three-dot menu → Custom repositories → the repo URL, type
**Integration** → Add → install → restart. *Manual*: download the release, copy
`custom_components/stiebel_eltron_ir/` into `<config>/custom_components/` so
`<config>/custom_components/stiebel_eltron_ir/manifest.json` exists → restart.

**Configuration** — Settings → Devices & services → Add integration → **Stiebel
Eltron (infrared)**. Name each field exactly as
[strings.json](custom_components/stiebel_eltron_ir/strings.json) labels it:
**Infrared emitter** (required), **Infrared receiver (optional)**, **Name**
(defaults to the model's title; it names the device and derives entity ids). Then
the uniqueness rule already implemented in
[config_flow.py:57-65](custom_components/stiebel_eltron_ir/config_flow.py#L57-L65):
one entry per emitter *and model*, because the frame carries no device address, so
two identical appliances on one emitter cannot be driven apart; two different models
sharing one emitter is fine.

**Exercise:** follow the manual-installation steps verbatim against the devcontainer
Home Assistant and complete the config flow from them, correcting any step that does
not match what the UI actually shows.

## Phase 4 — prek, commitizen, coverage and JUnit

### Dev dependencies

```toml
[dependency-groups]
dev = ["pytest>=8", "pytest-cov>=6", "commitizen>=4.18", "prek>=0.4"]
```

`prek` belongs here rather than being invoked purely through `uvx` — see **Why
`prek install` is still a step** below.

### Commit types — stock conventional commits

```toml
[tool.commitizen]
name = "cz_conventional_commits"
# Writes the version to pyproject.toml AND uv.lock's entry for this package, which
# `pep621` does not. Without it, `uv sync --locked` in CI fails after every bump.
version_provider = "uv"
# The version Home Assistant and HACS actually read.
version_files = ["custom_components/stiebel_eltron_ir/manifest.json:version"]
tag_format = "v$version"
# Pre-release: a breaking change moves 0.5 → 0.6, never to 1.0.
major_version_zero = true
update_changelog_on_bump = true
annotated_tag = true
# Signed release tags declared by the repository, not left to one working copy.
gpg_sign = true
```

No `cz_customize` block. Restricting the type list is possible only through
`schema_pattern`, which lives under `customize` — and `cz_customize` carries an
upstream notice that it is "likely to be removed or renamed in the next major
release" (commitizen #1385). Dropping `security` and `deprecate` removes the reason
to touch it.

What that config gives, read from commitizen's source rather than assumed:

- **Accepted types** (`ConventionalCommitsCz.schema_pattern`): `build`, `bump`,
  `chore`, `ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `revert`, `style`,
  `test`, each with optional `(scope)` and optional `!`. That is a superset of the
  reference repo's list minus `security` and `deprecate`; it also permits `style`
  and `revert`, which the reference excludes. Narrowing that is not worth a
  deprecated config block.
- **`bump` being in the list matters.** `cz bump` commits
  `bump: version 0.1.0 → 0.5.0`, and the commit-msg hook validates that commit like
  any other. A hand-written pattern that omitted `bump` would make `cz bump` fail its
  own hook.
- **Version steps** (`defaults.BUMP_MAP`): `!` or `BREAKING CHANGE` → MAJOR, capped
  to MINOR by `major_version_zero`; `feat` → MINOR; `fix`, `refactor`, `perf` →
  PATCH. Everything else bumps nothing.
- **Changelog sections** (`change_type_map`): `feat` → Feat, `fix` → Fix,
  `refactor` → Refactor, `perf` → Perf. `docs`, `chore`, `test`, `build`, `ci`,
  `style` and `revert` produce no entry, matching what the reference repo hides.
- **`allowed_prefixes`** stays at its default — `Merge`, `Revert`, `Pull request`,
  `fixup!`, `squash!`, `amend!`. It is an early-accept bypass, not a type allowlist:
  `validate_commit_message` returns `True` on a prefix match before the pattern is
  applied. Keeping it means merge and fixup commits are not rejected by the hook.

Write a security fix as `fix(security): …` — it validates, bumps PATCH, and lands in
the changelog. Record that convention in CONTRIBUTING so the dropped types do not
come back as ad-hoc invented ones.

### Refusing unsigned commits locally — `tools/check_signing.py`

Signing is required, so the working copy refuses to produce or push an unsigned
commit. What each stage can actually check is decided by two measured facts about
this machine, not by preference:

- At `pre-commit` time there is no signature to inspect. The commit object does
  not exist yet.
- `git verify-commit` may not work when `gpg.program` is the Windows GnuPG.
  Signing succeeds because git pipes the payload
  through stdin, but verification writes the signature to a Linux temp path and hands
  `gpg.exe` a path it cannot open: `gpg: can't open '/tmp/.git_vtag_tmpD2cjXA'`. A
  hook built on `verify-commit` would therefore reject every commit in this
  repository, all 51 of which are signed. It is not usable, and no amount of
  `TMPDIR` juggling fixes an argument in the wrong path namespace.

So the script has two modes:

- `config` (pre-commit) — fail unless `git config --get commit.gpgsign` is `true`.
  That is the one setting deciding whether the commit about to be created is signed,
  and git aborts the commit itself if signing then fails, so "configured to sign"
  and "signed" cannot diverge silently. Checking config rather than a key id keeps a
  contributor relying on gpg's default key from being failed for nothing.
- `commits` (pre-push) — for every commit in `git rev-list HEAD --not --remotes`,
  require a `gpgsig` (or `gpgsig-sha256`) header, read with `git cat-file commit` and
  parsed only down to the blank line that ends the header block, so a message body
  quoting the word cannot forge a pass. No gpg process is involved, which is why it
  works here at all. This is the layer that catches an explicit
  `git commit --no-gpg-sign`, which the config check cannot see. SSH signatures use
  the same header, so the check is format-agnostic.

**Neither local check proves a signature is valid or trusted** — that needs a key
this machine cannot currently use. Validity is established server-side, twice: the
branch ruleset from phase 1 rejects unsigned pushes to `main`, and phase 5's CI step
asserts GitHub itself marked each commit `verification.verified == true`. Local hooks
catch the mistake in the second it is made; GitHub is what makes the guarantee.

**Python, not bash**, unlike `tools/link_devcontainer.sh` — which is bash because it
runs inside the ha-core container, where this project's environment does not exist.
This runs on the host, where uv is a given. The reasons are that `tools` is already
on pytest's `pythonpath` and `tests/test_cli.py` already imports a tool module
directly, so the header parsing gets real tests in `tests/test_check_signing.py`
rather than only being exercised by making commits; and ruff lints `tools/*.py`,
whereas no shellcheck hook is planned. Cost is measured, not assumed: `uv run python`
starts in ~250 ms against bash's ~30 ms, on a commit path that already pays
`uv run cz check` for the message hook.

Cases the tests must cover, each a way a naive implementation passes something it
should not: a commit whose *message body* contains a line beginning `gpgsig`;
`gpgsig-sha256` as the header; an empty push range; and a genuinely unsigned commit
naming itself in the failure output.

### Signing, and what `cz bump` does with it

Read from `commitizen/commands/bump.py` and `commitizen/git.py`:

- The bump **commit** is `git commit -a -F <file>`, with no signing flag of its own,
  so it inherits `commit.gpgsign = true` from git config. Already signed here.

  That asymmetry is deliberate, not an oversight: `--gpg-sign`'s help reads "Sign
  tag instead of lightweight one", a sibling of `--annotated-tag`. `git tag` defaults
  to a lightweight tag and no config makes it otherwise, so commitizen must choose;
  `git commit` needs no help, because `commit.gpgsign` already owns that decision and
  passing `-S` would override a user who turned signing off on purpose.
- The bump **tag** is `git tag -a <tag> -m <msg>`, or `git tag -s <tag> -m <msg>`
  when `gpg_sign` is set. `tag.gpgsign = true` would sign it either way; `gpg_sign =
  true` in `pyproject.toml` states the requirement in the repository, so a clone
  without that git config still produces a signed tag.
- One clash is foreseeable: commitizen writes `CHANGELOG.md`, and if
  `end-of-file-fixer` or `trailing-whitespace` rewrites it, the hook has modified a
  staged file and the bump aborts. The fix is to exclude `CHANGELOG.md` from those
  two hooks. Commitizen also offers a flag (don't use it) that skips verification
  on the bump commit; it disables the commit-msg hook on the one commit whose message
  is generated rather than written.

### Coverage and test results

```toml
[tool.coverage.run]
branch = true
source = ["custom_components/stiebel_eltron_ir/devices", "tools"]
omit = ["*/.venv/*", "*/site-packages/*", "tests/*"]

[tool.coverage.report]
show_missing = true
exclude_also = ["if TYPE_CHECKING:", "raise NotImplementedError"]
```

**Amendment, found in practice — three attempts, the first two wrong.**

1. `source = [".../devices", "tools"]` reported six integration modules at 0%,
   because they import Home Assistant and the host suite cannot import them.
2. Omitting those six was worse: it hid the best-tested code in the repository,
   which the devcontainer suite covers at 97-100%. `include` compounded it, because
   a file matching no pattern vanishes entirely — a module with no tests at all
   would never appear, which is the one row worth having.
3. The answer is neither filter. **One command runs both suites and merges them.**

`tools/coverage.sh` runs the 903 unit tests from this repo's root and the 207
integration tests from ha-core's test tree, both inside the devcontainer — which
already runs both — then `coverage combine`. Output is a single report in
`coverage/`, paths relative to the repository root so Coverage Gutters resolves
them in the editor.

Merging is not bookkeeping. `protocol.py` measures 94% from the unit suite and 93%
from the integration suite; **combined it is 97%**, because each covers lines the
other misses. Neither number alone is a fact about the code. Measured 2026-08-20,
the merged total is 78% over 1084 statements.

`[tool.coverage.run]` therefore uses `source`, not `include`, plus
`relative_files = true`; and `[tool.coverage.paths]` maps the integration run's
absolute `/workspaces/acp35/...` paths onto the unit run's repository-relative ones.
Two traps, both hit before they were understood: the integration run must name the
package as a **module**, since config `source` paths resolve against a working
directory that is ha-core; and the merge must be `coverage combine` over separate
data files, because `--cov-append` never applies the path mapping and pytest-cov's
own end-of-session combine defeats `parallel = true`.

**Phase 4b, done — and it replaced all of the above.** The two-run scheme worked but
failed the actual requirement: a shell script producing data that only a third-party
extension can display is not coverage in the IDE, and VS Code's own Test Explorer
can only report on tests it runs itself. So every test had to become runnable on the
host.

`pytest-homeassistant-custom-component` 0.13.356 packages ha-core's test fixtures.
With it, **all 1110 tests run in one `uv run pytest` in 16 seconds**, and
`uv run pytest --cov` produces exactly the numbers the two-run merge produced — 78%
total, `protocol.py` 97%. `tools/coverage.sh` and `[tool.coverage.paths]` are
deleted, the Coverage Gutters extension is no longer recommended, and Test Explorer's
**Run Tests with Coverage** is the supported route.

Four things the migration turned up, none of them guessable in advance:

- `requires-python = ">=3.14"` was too loose. Home Assistant needs `>=3.14.2` — the
  comment beside it already said so, but the constraint did not enforce it.
- PHACC's fixtures are async and autouse, so 903 synchronous unit tests error under
  pytest-asyncio's strict mode. `asyncio_mode = "auto"` settles it, which is what
  ha-core sets for the same reason.
- `entity_registry_enabled_by_default` lives in ha-core's
  `tests/components/conftest.py`, which PHACC does not package — it carries the root
  fixtures, not the per-domain ones. Four lines locally.
- `fake_ir` needed no relocation, so HACS's rule of one directory under
  `custom_components/` is intact. The 39 tests that use it pass.

Tests are now grouped `tests/unit`, `tests/integration`, `tests/hardware`, so the
Test Explorer shows what each suite is. Three symlinks into ha-core's test tree are
gone from `tools/link_devcontainer.sh`; the container is now only for running Home
Assistant itself and for hardware sessions.

**The virtualenv had to move off `/mnt/c`, and that is not a detail.** Home Assistant
arrives with 129 packages whose fixtures touch thousands of files, and every one of
those crosses WSL2's 9p mount. Measured here, same code, same repository location:

| | venv on `/mnt/c` | venv on ext4 |
| - | ---------------- | ------------ |
| `uv sync` | ~20 min | 2.4 s |
| 9 integration tests | 19.0 s | 3.2 s |
| whole suite | 7m34s | ~1m45s |

The fix is uv's own: `preview-features = ["centralized-project-envs"]` in
`[tool.uv]`. uv keeps the environment in its cache — which is on the user's native
filesystem — and maintains `.venv` as a link to it, so activation, the git hooks and
the editor need to know nothing. It is declared in `pyproject.toml`, so it applies to
every contributor rather than being a local ritual, and an older uv ignores the
unknown preview name with a warning and builds an ordinary `.venv`.

Two hand-rolled alternatives were tried first and are worse. A manual symlink works
but has to be recreated by hand in every clone and documented in the README.
`UV_PROJECT_ENVIRONMENT` is worse still: the git hooks run `uv run` from a shell that
never sourced a profile, so a variable set only in the editor would leave the
pre-push hook — which runs the whole suite — on the slow interpreter. uv's own docs
also warn it clobbers when shared across projects.

`.gitignore` needed `.venv` without a trailing slash either way, since `.venv/`
matches a directory and not the link.

The unit loop also slowed, 3 s to 13 s, because PHACC's plugin loads Home Assistant's
fixtures for every session. `uv run pytest tests/unit -p no:homeassistant` restores
2.9 s for editing a codec; the full run stays the default everywhere else.

**Consequence for phase 5:** integration tests in CI are no longer out of scope.
They are simply part of `uv run pytest`.

The full local command:

```bash
uv run pytest --cov --cov-report=term-missing \
  --cov-report=xml:coverage/coverage.xml \
  --cov-report=lcov:coverage/lcov.info \
  --junitxml=test-results/unit/junit.xml
```

`coverage.xml` is Cobertura — what Codecov ingests and what VS Code's Python
extension and Coverage Gutters read, so one file serves the local and the eventual
remote consumer. Add `coverage/`, `.coverage` and `test-results/` to `.gitignore`.

### VS Code Test Explorer coverage

`.vscode/settings.json`:

```json
{
  "python.testing.pytestEnabled": true,
  "python.testing.unittestEnabled": false,
  "python.testing.pytestArgs": ["tests"]
}
```

**Amendment, measured.** An earlier draft put `--cov` in `pytestArgs`, reasoning that
the extension injects `--cov=.` when it finds none and that this would override the
`source` list. Both halves were wrong in practice. The injected `--cov=.` produces
*exactly* the same report — 1084 statements, 204 missed, 78% — because the `omit`
list narrows it to the same files. And forcing `--cov` applies it to every run,
including a single test: one file measured **6%**, which in the Test Explorer paints
the rest of the codebase red. Coverage belongs to **Run Tests with Coverage** and
nothing else.

No `python.defaultInterpreterPath` either: the extension finds `.venv` in the
workspace root on every platform, and a hardcoded path would name `bin/python` where
Windows keeps `Scripts\python.exe`.

Coverage flags stay **out of `addopts`** so `uv run pytest` keeps its plain, fast
default for the pre-push hook and CI's own explicit invocation.

`.vscode/extensions.json` recommends `ms-python.python`, `charliermarsh.ruff` and
`editorconfig.editorconfig`. **Not** `ryanluker.vscode-coverage-gutters`, which an
earlier draft of this plan added: after phase 4b the Test Explorer runs every test
itself, so its own coverage view is complete and no third-party extension is
involved in reading it.

### `.pre-commit-config.yaml`

Structure and hook choices lifted from the reference repo; three stages, same
`default_install_hook_types`.

```yaml
default_install_hook_types: [pre-commit, commit-msg, pre-push]

repos:
  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.30.0
    hooks:
      - id: gitleaks
        args: ['--redact', '--verbose']
        stages: [pre-commit]

  - repo: https://github.com/zizmorcore/zizmor-pre-commit
    rev: v1.23.1
    hooks:
      - id: zizmor
        args: ['--pedantic']
        stages: [pre-commit]

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.16.3
    hooks:
      - id: ruff-check
        args: [--fix]
        stages: [pre-commit]
      - id: ruff-format
        stages: [pre-commit]

  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v6.0.0
    hooks:
      - id: end-of-file-fixer
      - id: fix-byte-order-marker
      - id: mixed-line-ending
        args: ['--fix=lf']
      - id: trailing-whitespace
        args: ['--markdown-linebreak-ext=md']
        exclude: Stiebel Eltron air conditioner ACP 35\.md$
      - id: check-json
      - id: check-yaml
      - id: check-merge-conflict
      - id: check-added-large-files

  - repo: local
    hooks:
      - id: signing-configured
        name: Commit signing is configured
        stages: [pre-commit]
        language: system
        entry: uv run python tools/check_signing.py config
        pass_filenames: false
        always_run: true

      - id: commitizen
        name: Conventional commit message
        stages: [commit-msg]
        language: system
        entry: uv run cz check --allow-abort --commit-msg-file

      - id: signed-commits
        name: Every pushed commit is signed
        stages: [pre-push]
        language: system
        entry: uv run python tools/check_signing.py commits
        pass_filenames: false
        always_run: true

      - id: pytest
        name: Unit tests
        stages: [pre-push]
        language: system
        entry: uv run pytest -q
        pass_filenames: false
        always_run: true
```

Two deliberate departures from the reference:

- **gitleaks earns its place here specifically.** `.env` holds a Home Assistant
  long-lived access token and is gitignored; a hook that catches the day that
  gitignore is bypassed is worth more here than in a repo with no secret.
- **The commit-msg hook is `local`, not the upstream commitizen hook.** commitizen
  is a dev dependency anyway, because `cz bump` must run in the project environment,
  so a local hook keeps one pinned version in `uv.lock` instead of a second pin in a
  hook `rev` that drifts from it. `--allow-abort` matches the upstream hook's own
  arguments.

`ruff-check` runs before `ruff-format`, as astral-sh documents for `--fix`.

### Why `prek install` is still a step

`uvx prek run --all-files` does install and run prek in one command — but that
installs *the tool*, not *the git hooks*. Hooks fire because `.git/hooks/pre-commit`,
`commit-msg` and `pre-push` exist as shims that call prek; `prek install` is what
writes those shims, once per clone, and no amount of `uvx` substitutes for it.

Two consequences shape the instruction given in CONTRIBUTING:

- The shim runs later, from git, outside any `uvx` invocation, so `prek` must be
  resolvable then. An ephemeral `uvx` environment is the wrong place for it, which
  is why `prek` is a dev dependency and the command is `uv run prek install`.
- prek has an open report that `install` wires only the `pre-commit` shim even when
  `default_install_hook_types` lists more, so name them explicitly and verify:

  ```bash
  uv run prek install --hook-type pre-commit --hook-type commit-msg --hook-type pre-push
  ls .git/hooks/{pre-commit,commit-msg,pre-push}
  ```

**Exercise:** commit a message with a bad type (rejected) and a good one (accepted);
push to a scratch branch and watch the pre-push suite run; `uv run prek run
--all-files` clean; `uv run cz bump --dry-run` prints a sensible version; open
`coverage/coverage.xml` in VS Code and run **Run Tests with Coverage** from the Test
Explorer.

## Phase 5 — CI and GitHub automation

Workflows under `.github/workflows/`, hardened to the reference repo's standard:
`permissions: {}` at workflow level with per-job grants, every action **pinned to a
commit SHA with a `# vX.Y.Z` trailing comment**, `persist-credentials: false` on
every checkout, and `concurrency` with `cancel-in-progress` on the push/PR
workflows. Copy the SHA pins that already exist in
`/mnt/c/njs/gpg-windows-relay/.github/workflows/` for `actions/checkout`,
`actions/cache`, `actions/upload-artifact`, `actions/download-artifact`,
`EnricoMi/publish-unit-test-result-action` and `github/issue-labeler`; resolve new
ones for `astral-sh/setup-uv`, `j178/prek-action`,
`home-assistant/actions/hassfest` and `hacs/action`.

Pinning hassfest and HACS to a SHA contradicts their own docs, which say `@master` /
`@main`, but `zizmor --pedantic` rejects unpinned uses and Dependabot bumps SHA pins
on a schedule. Where zizmor still objects, use an inline `# zizmor: ignore[<rule>]`
with a one-line reason, as the reference repo does.

### Why hassfest and HACS run on a cron, and CI does not

`ci.yaml` tests this repo's code against pinned inputs: with no commit, nothing can
change, so a scheduled run would re-prove yesterday's result.

The other two validate against **rules that live upstream and change without any
commit here**:

- hassfest runs the `ghcr.io/home-assistant/hassfest` container image, pulled at run
  time and tracking Home Assistant's beta channel. Pinning the *action* to a SHA
  does not freeze those rules — the image is still fetched fresh. A new HA release
  can add a manifest requirement or deprecate a key, and this repo becomes invalid
  having changed nothing.
- `hacs/action` evaluates HACS's current publishing requirements *and* live GitHub
  state: description, topics, issues enabled, releases present. Someone editing
  repository settings, or HACS changing a rule, breaks it with no commit either.

Without a schedule, both failures would surface at the worst moment — while cutting
a release, mixed in with whatever else changed. A scheduled run surfaces them
independently, attributable to nothing local, which is exactly the useful signal.

**The schedule is weekly, `10 3 * * 1`** — Mondays at 03:10 UTC — not nightly. Home
Assistant ships monthly and HACS changes its rules rarely, so a week is well inside
the window between a rule changing and a release needing it, and it is one run
instead of seven. Monday puts the answer at the start of the week rather than during
it. The offset from the hour is deliberate: GitHub queues scheduled runs, and the top
of the hour — `0 0` most of all — is when everyone else's cron fires, so a run there
waits behind the crowd. `workflow_dispatch` covers wanting an answer immediately.

### The workflows

**`ci.yaml`** — `on: push: branches: [main]`, `pull_request: branches: [main]`,
`workflow_dispatch`. Two jobs, the second gated on the first, mirroring the
reference's "quality gates can't be bypassed" ordering:

- `checks`: **verify every PR commit is signed** → checkout → `astral-sh/setup-uv` →
  `actions/cache` for `PREK_HOME` (`~/.cache/prek`, keyed on
  `hashFiles('.pre-commit-config.yaml')`) → `j178/prek-action`, running
  `prek run --all-files`. Only the `pre-commit` stage runs in an `--all-files` sweep;
  commit-msg and pre-push hooks are not part of it.

  The signature step is adapted from the reference repo: it runs first, before
  checkout, because `gh` and `github.token` need neither, and fails on any commit
  whose `.commit.verification.verified` is not `true`. It checks what GitHub
  *verified*, not merely what carries a signature, so an unknown or untrusted key
  fails as it should — which is the one thing the local hooks cannot establish.

  **It covers pushes as well as pull requests.** The reference repo guards the step
  with `if: github.event_name == 'pull_request'`, which suits a PR-based flow; here
  the normal path is a direct push to `main`, so a PR-only guard would mean the check
  never runs. Two branches, one step:
  - `pull_request` → `gh api repos/…/pulls/$N/commits --paginate --jq '.[].sha'`,
    needing `pull-requests: read`.
  - `push` → `gh api repos/…/compare/${{ github.event.before }}...${{ github.sha }}
    --jq '.commits[].sha'`, falling back to checking `github.sha` alone when `before`
    is all zeros, as it is for a newly created branch.

  The phase 1 ruleset already rejects unsigned pushes to `main` server-side, so this
  step is the *diagnosis* rather than the gate: it names the offending commit, and it
  still fires on branches the ruleset does not cover.
- `test`, `needs: [checks]`: `uv sync --locked` → the coverage command from phase 4.
  uv fetches Python 3.14 from `requires-python`. `addopts` already deselects the
  hardware marker, so nothing reaches for a device. `--locked` is what makes
  `version_provider = "uv"` necessary. Then `if: always()` artifact uploads with
  `if-no-files-found: error`: `test-results-unit`, `coverage`, and the
  `github-event` file the publish workflow needs, all `retention-days: 7`.

  A **commented-out** Codecov step follows the uploads, pinned and complete
  (`codecov/codecov-action`, `files: coverage/coverage.xml`, `flags: unittests`,
  `fail_ci_if_error: false`), with a comment naming the two things needed to enable
  it: add the `CODECOV_TOKEN` secret and uncomment. Coverage is produced in
  Codecov's format from day one; only the upload waits.

**`publish-test-results.yaml`** — `on: workflow_run: workflows: ['CI'], types: [completed]`,
`permissions: {}`. Ported from the reference, minus its integration-results job:

- `publish-test-results`: downloads the artifacts by `run-id`, runs
  `EnricoMi/publish-unit-test-result-action` with `commit`, `event_file` and
  `event_name` taken from the `workflow_run` payload so results attach to the right
  commit; `check_name: Unit test results`, `comment_mode: always`. Needs
  `actions: read`, `checks: write`, `pull-requests: write`.
- `update-badges`, only for pushes to the default branch: writes `unit-tests.json`
  and `coverage.json` to an orphan `badges` branch, bootstrapping it if absent,
  reusing the reference's `make_badge` shell function and its `http.extraheader`
  token handling. **The coverage percentage comes from the downloaded
  `coverage/coverage.xml`** (`line-rate` on the root element), not from the Codecov
  API — that single change is what lets the badge exist with no Codecov account.

  This job's commits are made by `github-actions[bot]` and pushed over HTTPS, so
  they are **unsigned**. That is why phase 1's ruleset targets `main` rather than all
  branches. If the badges ever stop updating, a rule silently extended to every
  branch is the first thing to check.

**`hassfest.yaml`** — `on: push, pull_request, schedule: "10 3 * * 1", workflow_dispatch`;
checkout then `home-assistant/actions/hassfest`.

**`hacs.yaml`** — same triggers; `hacs/action` with `category: "integration"` and no
`ignore`.

**`release.yaml`** — `on: push: tags: ["v*"]`, `permissions: contents: write`:
checkout with `fetch-depth: 0`; fail unless `manifest.json`'s `version` equals the
tag without its leading `v`; then

```bash
uvx --from commitizen cz changelog "$VERSION" --dry-run > notes.md
gh release create "$GITHUB_REF_NAME" --verify-tag --title "$GITHUB_REF_NAME" --notes-file notes.md
```

`--verify-tag` aborts if the tag is not on the remote. If `cz changelog` renders
nothing — possible when a release contains only hidden types — fall back to
`--generate-notes`, a documented `gh release create` flag that builds title and body
from GitHub's own Release Notes API.

**`issue-labels.yaml`** — `on: issues: [opened, edited]`; `github/issue-labeler` with
`configuration-path: .github/issue-labels.yml`, `sync-labels: 1`, `issues: write`.
Author `.github/issue-labels.yml` with rules for this domain: `acp35`, `protocol`,
`hardware`, `config-flow`, `hacs`, `documentation`.

**`.github/dependabot.yml`** — weekly, two ecosystems: `github-actions` (which
updates SHA pins and their version comments) and `uv`. Note in a comment that
Dependabot does **not** update `.pre-commit-config.yaml` revs; `uv run prek
auto-update` is the command for those, run by hand.

**Integration tests are in CI**, as of phase 4b — no longer a gap to explain away.
`uv sync --locked` installs Home Assistant through
`pytest-homeassistant-custom-component`, and `uv run pytest` runs all 1110 tests.
The reference repo needs a Docker build and a devcontainer for the equivalent; here
it is the same command a developer runs. Only the install is heavier, which the uv
cache absorbs.

**Exercise:** push the branch and confirm each workflow runs; open a throwaway PR to
see the test-results check and comment appear; merge to main and confirm the `badges`
branch is created with both JSON files; run `hassfest` and `hacs` via
`workflow_dispatch` rather than waiting for the cron.

Then prove the gate blocks. HACS is failing in this phase — see below — so a tag
pushed now exercises `release.yaml` end to end and must produce **no release at all**.
Refusing to publish on failed validation is the property the whole workflow exists
for, and this phase is the only place it can be shown on demand: phase 7 makes HACS
pass, and from then on a tag on a green tree publishes rather than blocks.

Use `v0.1.0`, matching the current `manifest.json`, so the `version` job passes and
the run reaches the gates instead of stopping at that cheap check:

```bash
git tag -s v0.1.0 -m "gate test"
git push origin v0.1.0
# expect: version ✓, ci ✓, hassfest ✓, hacs ✗, publish skipped
gh release list                    # must be empty
git push --delete origin v0.1.0
git tag -d v0.1.0
```

A release appearing means the gate does not work, and phase 8 must not proceed until
it does.

**Then tag a version that does *not* match `manifest.json`** and push that too. The
run must stop at `version` in about twenty seconds with the other four jobs skipped,
rather than spending the whole suite to reach the same answer. That is the reason
`version` runs first and alone, and it is a second cheap proof that the workflow
fails closed. Delete this tag as well.

**Delete every test tag afterwards, both places.** The phase 1 ruleset targets the default
branch and does not cover tags, so the deletion is allowed. It matters for phase 8:
`cz bump 0.5.0` is given its version explicitly *because there is no tag to measure
from*, and a leftover `v0.1.0` would silently change what that bump computes.

### The release gate

The plan as written published on a `v*` tag push and checked nothing but the
manifest version. That is not a gate. `ci.yaml` filters on `branches: [main]`, which
excludes tag refs, so **a tag push ran no tests at all**; and when commit and tag go
together, `git push --follow-tags` starts the branch run and the release run at the
same moment, with publishing far quicker than testing. The release won that race
every time.

A release must pass, on GitHub, against the tagged tree: the full test suite (unit
and integration, one pytest run since phase 4b), hassfest, and HACS.

`release.yaml` therefore runs all three itself rather than consulting an earlier run.
`ci.yaml`, `hassfest.yaml` and `hacs.yaml` each gained a `workflow_call` trigger, and
release.yaml calls them as jobs that `publish` depends on:

```text
version ─┬─> ci        ─┐
         ├─> hassfest  ─┼─> publish
         └─> hacs      ─┘
```

Waiting for the branch-push runs instead was rejected, and not only for the race: a
tag can be pushed on its own — `git push origin v0.5.0` for a commit already on main
— and then there is no run to wait for. A gate that depends on an event that may
never happen is not a gate. `version` runs first and alone so a mis-tagged release
fails in seconds rather than after the whole suite.

The cost is that a combined push tests the same commit twice, once for the branch and
once for the tag. That is the price of a gate that holds however the tag arrives.

To stop hassfest and HACS *also* running standalone on the tag push, their `push`
trigger is now `branches: ['**']`. Not `tags-ignore: ['**']`, which reads like the
same statement and is the opposite one: GitHub's rule is that "if you define only
tags/tags-ignore or only branches/branches-ignore, the workflow won't run for events
affecting the undefined Git ref", so a lone tag filter would have left branches
undefined and stopped both workflows running on any push at all.

### Amendments, written while building it

Six, none of them guessable from the plan alone:

- **The `checks` job must skip `signing-configured`.** It is an `always_run`
  pre-commit hook, so `prek run --all-files` runs it, and it asks whether *this
  working copy* is configured to sign the commit it is about to make. A runner makes
  no commits and has no `commit.gpgsign`, so CI would have been red on every run.
  `SKIP: signing-configured` on the step; the CI-relevant question — did GitHub
  verify what was pushed — is the signature step that already runs first.
- **No `actions/cache` step for `PREK_HOME`.** `j178/prek-action` v3 caches hook
  environments itself, keyed on the config file, which is what the separate step was
  for. Its `prek-version` is left at `latest` rather than pinned to uv.lock's: what a
  hook *does* is decided by the `rev` pins in `.pre-commit-config.yaml`, and prek only
  runs them, so a second pin would drift for nothing.
- **hassfest and HACS are pinned to their default-branch HEAD, not to a tag.** Their
  newest releases are `home-assistant/actions` 1.0.0 from 2020 and `hacs/action`
  22.5.0 from 2022; pinning to those would freeze the wrapper years back. The
  trailing comment reads `# master` / `# main` with the date in the comment above it,
  and Dependabot moves them. This is exactly the contradiction the plan predicted:
  their docs say a floating branch, `zizmor --pedantic` says a SHA.
- **The coverage badge is computed from four counts, not from `line-rate`.** Cobertura's
  root `line-rate` is statement coverage alone — 0.8118, an 81% badge — while `branch =
  true` makes `pytest --cov` report 78%. `(lines-covered + branches-covered) /
  (lines-valid + branches-valid)` reproduces 78% exactly. A badge that disagrees with
  the command anyone can run is worse than no badge.
- **One check, named `Test results`.** The reference's split into unit and integration
  checks has nothing to describe here: phase 4b made both one pytest run and one JUnit
  file. The badge is `tests.json`, not `unit-tests.json`. Its skipped count is normally
  zero, because the 166 hardware tests are *deselected* by `addopts` and never reach
  the JUnit file.
- **`release.yaml` needed two things zizmor asked for**: a `concurrency` group (with
  `cancel-in-progress: false` — a half-finished publish must not be superseded), and
  `enable-cache: false` on `setup-uv`, since a job that both restores a cache and
  publishes is the shape a cache-poisoning attack needs. Its fallback test is
  `grep -q '^- ' notes.md`, not `[ -s notes.md ]`: `cz changelog` emits a version
  heading even when every commit in the range is a hidden type, so the file is never
  empty.

### What only a real run could find

The amendments above were written while writing the files. These were invisible
until the workflows actually ran, and each is a case where local validation could not
have helped. Another, HACS validation, is large enough for its own section below.

- **The publish job needed `contents: read`.** All four of the first publish runs
  failed at `GET /repos/{owner}/{repo}/commits/{sha}` with 403, after creating the
  check run and before posting the comment.
  `EnricoMi/publish-unit-test-result-action` fetches the commit it compares against —
  a pull request's base, or on a push the previous commit on the branch — and that
  read needs `contents: read` on a private repository, where a public one is served by
  `metadata: read`. The reference repo's copy of this workflow does not grant it
  because that repository is public. Because `update-badges` has
  `needs: [publish-test-results]`, this also meant the badge branch was never created
  and none of that job's code had ever executed. Marked `PRIVATE-ONLY`; see phase 6.
- **Dependabot cannot bump `pytest` or `pytest-cov`, ever.**
  `pytest-homeassistant-custom-component` 0.13.356 requires `pytest==9.0.3` and
  `pytest-cov==7.1.0` exactly, so Dependabot's proposal of pytest 9.1.1 was
  unsatisfiable and the whole uv update job failed with
  `dependency_file_not_resolvable`. **No local command finds this**: `pytest>=8` and
  `pytest==9.0.3` resolve together happily, and `uv lock --upgrade` — the most
  aggressive local re-resolve there is — does not even mention pytest, because that
  pin caps it. It appears only when something rewrites the declared constraint. Both
  are now in `ignore` for the uv ecosystem. Their versions are not this repository's
  to choose; bumping `pytest-homeassistant-custom-component` is what moves them, and
  that is the same edit that moves the pinned Home Assistant, so Dependabot may still
  propose it.
- **hassfest and HACS ran twice for every pull request.** Their `push` trigger was
  `branches: ['**']` while `ci.yaml`'s was `branches: [main]`, so a branch push and
  then the pull request on that branch each triggered a run against the same commit.
  Both now use `branches: [main]`. The consequence, accepted deliberately: a branch
  with no pull request open gets no automatic run, which was already true of
  `ci.yaml`. The `pull_request` trigger has to stay regardless — once the repository
  is public, a fork's push raises no event here, so it is the only thing that
  validates a fork's contribution.
- **`release.yaml` could never have published anything.** All four workflows built
  their concurrency group from `${{ github.workflow }}`, which in a *called* workflow
  resolves to the **caller's** name. On a tag push `release.yaml` and the three
  workflows it calls therefore computed the identical string, and GitHub cancelled
  the run: `Canceling since a deadlock was detected for concurrency group
  'Release-refs/tags/v0.1.0' between a top level workflow and 'CI'`. The gate jobs
  never started, `publish` waited on `needs` that never completed, and no tag could
  have produced a release — on any version, ever.

  Each group now uses a literal prefix (`ci-`, `hassfest-`, `hacs-`, `release-`)
  that cannot change with the caller. `issue-labels.yaml` and
  `publish-test-results.yaml` keep `${{ github.workflow }}`; neither is ever called
  as a reusable workflow, so neither can collide.

  **This is the entry that justifies the gate exercise being mandatory.** Nothing
  local could find it: the expression is valid, `zizmor` passes, every workflow
  parses, and all four had run correctly hundreds of times under `push`,
  `pull_request`, `schedule` and `workflow_dispatch`. It appears only under
  `workflow_call`, which only a tag push reaches. Deferring the test to phase 8 would
  have meant discovering it while cutting the first real release.
- **A gate run uploaded three artifacts nothing would read.** `ci.yaml` uploads
  `test-results-unit`, `coverage` and `github-event` for one consumer,
  `publish-test-results.yaml`, which triggers on `workflow_run` for runs named `CI`.
  A called workflow's run is named for its caller, so on a release the consumer never
  fires and the three sat unread for seven days — three unexplained entries on a
  release page, which is a question rather than an answer. The uploads are now
  guarded by `github.workflow == 'CI'`, matching that consumer's own filter. The
  string `CI` is thereby coupled to three places: `name:` in `ci.yaml`, the guards,
  and `workflows: ['CI']` in `publish-test-results.yaml`. Renaming the workflow means
  changing all three, and nothing enforces it.

### HACS validation: two failures, only one of them planned

`hacs/action` runs nine checks. On the first push, seven passed — `brands`, `topics`,
`description`, `license`, `archived`, `issues`, `information` — which settles every
repository-settings question phase 0 and phase 1 raised, topics included. Two failed:

- **`hacsjson`** — "The repository has no 'hacs.json' file". Planned. Phase 7 is
  exactly this, and nothing before phase 7 can clear it.
- **`integration_manifest`** — "expected a dictionary. Got None". **Not planned, and
  not what it looks like.** Read against HACS's own validator
  (`custom_components/hacs/validate/integration_manifest.py`), the check tests the
  repository tree first and would have reported "the repository has no
  'manifest.json' file" had the file been missing. It did not. So `manifest.json` was
  found, and the *content* fetch that follows returned `None`. The file is present and
  valid — Home Assistant loads it, and hassfest passes on it in the same push.

  The cause was the repository being private: HACS reads file content from a GitHub
  `download_url`, which for a private repository is a short-lived signed URL its
  client was not authenticating for. **Confirmed in phase 6** — the first
  `workflow_dispatch` run after the repository went public reported 1/9 failed, with
  only `hacsjson` left, and no code changed between the two runs. That makes it the
  second private-only failure phase 5 found, alongside the `contents: read` one, and
  it corrects phase 0's assumption that `hacs/action` "authenticates with
  `${{ github.token }}` rather than reading the repository anonymously". For file
  content it does not.

**Consequence for phase 8.** `release.yaml` gates `publish` on HACS, so no release can
be cut while either check is red. The existing phase order already handles that —
public (6), then `hacs.json` (7), then the first release (8) — but this is on the
critical path to a release rather than a cosmetic red square, and both checks have to
be confirmed green before phase 8 begins.

## Phase 6 — Going public, community files and README badges

One manual step by Dale opens this phase, because the badges added below are the
first thing that cannot work on a private repository:

1. **Change the repository from private to public.** GitHub → the repository →
   **Settings** → **General** → **Danger Zone** → **Change repository visibility** →
   **Make public**, confirming by typing the repository name. Everything since phase
   1 has run privately; this is the point where shields.io and GitHub's badge SVGs
   need anonymous read, and where anyone but Dale can install the integration.
   Allow a few minutes before judging a broken badge — GitHub caches raw content for
   about five minutes, so a freshly public file can 404 briefly.

The signed-commits ruleset is already in place from phase 1; GitHub Pro allowed it to
be created while the repository was still private.

**Undo the private-only workarounds.** Search the tree for `PRIVATE-ONLY`; every one
is a concession to the repository not being readable, and each should be removed and
the workflow re-run to prove it was the only thing holding it up. As of phase 5 there
is one:

- `.github/workflows/publish-test-results.yaml` grants the publish job
  `contents: read`, so the action can read the commit it compares against — see
  [What only a real run could find](#what-only-a-real-run-could-find). On a public
  repository `metadata: read` covers that read, so the line should come out. Removing
  it and seeing the publish run stay green is the proof; if it 403s again, the
  reasoning was wrong and the line goes back.

**Re-run HACS validation as soon as the repository is public**, before doing anything
else in this phase:

```bash
gh workflow run hacs.yaml
```

Done: 1/9 failed, `hacsjson` alone. `integration_manifest` cleared with no code
change, which confirms the private-repository explanation — see
[HACS validation: two failures](#hacs-validation-two-failures-only-one-of-them-planned).
Phase 7 has the last check to clear.

Then the files:

- **`CONTRIBUTING.md`**, following the reference's headings with this repo's
  content: Prerequisites (uv, Python 3.14) · Dev setup · Testing (the three pytest
  markers — `hardware`, `manual`, `disruptive` — what `HW_RESTORE` gates, and the
  devcontainer path for integration tests) · Coverage (the command, the VS Code Test
  Explorer route, and how to enable the Codecov upload later) · Git hooks
  (`uv run prek install …`, what runs at which stage, and that they are a local
  convenience rather than the enforcement) · Commit conventions (the accepted types, how each
  maps to a version step and to a changelog section, and the `fix(security):`
  convention) · **Signed commits** (required on `main` by ruleset and checked on
  every PR; how to configure `commit.gpgsign`, `tag.gpgsign` and `user.signingkey`,
  and that the ruleset decides, so an unsigned commit is refused at the push whatever
  happened locally) · Release process (`cz bump`) · PR guidelines.
- **`.github/ISSUE_TEMPLATE/`** — `bug_report.yml`, `feature_request.yml`, and
  `config.yml` with `blank_issues_enabled: false`. The bug form asks for what
  actually determines an answer here: appliance model, Home Assistant version,
  integration version, emitter hardware and **its distance and line of sight to the
  appliance**, whether a receiver is configured, and debug logs.
- **README badges** at the top, now that the workflows exist: CI, hassfest, HACS,
  plus the two `img.shields.io/endpoint` badges reading `unit-tests.json` and
  `coverage.json` from the `badges` branch, as the reference does.
- One line in the README's Development section pointing at CONTRIBUTING, and a note
  that integration tests run only in the devcontainer.

**Exercise:** open a test issue from each template and confirm the labeler applies
the expected label; confirm every badge renders and links somewhere real.

## Phase 7 — `hacs.json`

```json
{
  "name": "Stiebel Eltron (infrared)",
  "homeassistant": "2026.8.0",
  "hide_default_branch": true
}
```

`hide_default_branch` keeps users on releases once one exists. `content_in_root`
stays default false; the layout already matches.

Versions stay at `0.1.0` through every phase above — bumping them by hand is exactly
what phase 4 exists to remove.

**Exercise:** re-run `hacs.yaml` by `workflow_dispatch` and confirm **all nine checks
pass, not just `hacsjson`**. Phase 5 left two failing, and this phase addresses only
one of them; `integration_manifest` is expected to have cleared in phase 6, when the
repository went public. Read the run's log rather than its overall status, so the
count is verified rather than assumed:

```bash
gh workflow run hacs.yaml
# then, on the resulting run:
gh api repos/diablodale/stiebel-eltron-climate-ir/actions/jobs/<JOB_ID>/logs \
  | grep -aE "Validation|checks failed"
```

A green HACS run is a precondition for phase 8: `release.yaml` gates `publish` on it,
so a red check here stops a release rather than merely reporting on one.

## Phase 8 — First release, `v0.5.0`

Repository description, topics and Issues were set in phase 0; confirm they are
still as HACS needs them, then, run by Dale rather than by me, since it commits and
tags:

```bash
uv run cz bump 0.5.0 --check-consistency
git show --stat        # pyproject.toml, uv.lock, manifest.json, CHANGELOG.md
git push && git push --tags
```

`0.5.0` is given explicitly only because there is no tag to measure from; afterwards
`uv run cz bump` derives it. `--check-consistency` fails if the version strings
disagree beforehand. `cz bump --files-only` edits without committing, if the bump
commit should be staged by hand — the tag then has to be created manually too.

Pushing the tag runs `release.yaml`, which publishes the GitHub Release. The first
`CHANGELOG.md` covers the whole development history; `changelog_start_rev` trims it
if that reads as noise, and it can be edited before the push.

### The publish path runs for the first time here

Phase 5 proves the gate **blocks**: a `v0.1.0` tag pushed while HACS was failing runs
`release.yaml` end to end and produces no release. What that test could not reach is
`publish` itself, because nothing was green to publish. This tag reaches it, so the
half of the workflow that creates something is exercised here for the first time.

**Before tagging, confirm all three gates are green on `main`**, since `publish`
depends on every one of them:

```bash
gh run list --workflow=ci.yaml       --limit 1
gh run list --workflow=hassfest.yaml --limit 1
gh run list --workflow=hacs.yaml     --limit 1
```

Check HACS by its nine checks rather than by the run's status alone — phases 6 and 7
each clear one of its two failures, and both must be gone. A red gate corrupts
nothing; it leaves the tag on the remote with no release attached, and recovering
means fixing the cause and re-running the workflow, or deleting the tag and
recreating it once the fix is in — the phase 1 ruleset targets the default branch and
does not cover tags, so that deletion is allowed.

**Then read the run.** The same five jobs phase 5 watched, now reaching one further:

```text
version ─┬─> ci        ─┐
         ├─> hassfest  ─┼─> publish
         └─> hacs      ─┘
```

- `version` failing on its own, with the other four skipped, means `manifest.json` and
  the tag disagree. It costs about twenty seconds rather than the whole suite, which
  is why it runs first and alone.
- `publish` skipped while all three gates are green means `needs` is wired wrongly.
  That is the opposite of the failure phase 5 tested for — a gate that blocks
  everything is as broken as one that blocks nothing — and this is the first phase in
  which it can appear at all.

**Exercise, in two parts.**

1. **The release.** Confirm the GitHub Release exists and is attached to the tag, that
   its notes came from `cz changelog` rather than the `--generate-notes` fallback, and
   that all five jobs ran in the shape above.
2. **The install, the way a stranger would.** Add the repo as a HACS custom repository
   on a real Home Assistant, install, restart, add the integration, pick an emitter,
   and confirm the climate, select and diagnostic-sensor entities appear. This is the
   only step in the whole plan that exercises what a user actually receives, rather
   than what CI says about it.

### What "HACS default repository" would mean later

HACS ships a curated default list, and anything on it is searchable and installable
inside HACS with no URL typed in. Getting listed means opening a pull request against
the **`hacs/default`** repository adding `diablodale/stiebel-eltron-climate-ir` to
its `integration` file. HACS's own prerequisites for that PR are: the HACS Action and
hassfest present and passing, a full GitHub Release created after those actions
succeeded, and the submitter being the repository owner. Everything phases 5–8 build
satisfies that list, so the PR is a small step whenever it is wanted — and until
then, the custom-repository route works with one extra step for the user.

## Out of scope

- Uploading coverage to Codecov, and `.codecov.yml`. The data is produced in
  Codecov's format and the workflow step is written but commented out; enabling it
  is a token and an uncomment.
- A mypy or pyright gate — the `tsc` equivalent. It needs Home Assistant installed to
  type-check the integration, so it belongs with the devcontainer work.
- A markdown/YAML/JSON formatter (prettier's remaining coverage).
- The `hacs/default` inclusion PR.
- `NOTICE`, per-file license headers, SECURITY.md, CODEOWNERS.
- Any change to protocol, entity or storage code.
