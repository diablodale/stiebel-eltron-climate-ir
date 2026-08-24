# Contributing

Thanks for looking. This integration reproduces what a handheld infrared remote
transmits, byte for byte, so most changes are decided by evidence from a real
appliance rather than by preference. That shapes a lot of what follows.

## Prerequisites

- **uv** — <https://docs.astral.sh/uv/getting-started/installation/>
- **Python 3.14.2 or newer.** `pyproject.toml` states the floor and `uv` installs a
  matching interpreter for you; `uv python install 3.14` if you would rather have it
  on the system. Home Assistant sets that floor, not this repository.
- **git**, configured to sign commits — see [Signed commits](#signed-commits).

No container, no Home Assistant install, no appliance. All three are optional and
only the last is irreplaceable.

## Dev setup

```bash
git clone https://github.com/diablodale/stiebel-eltron-climate-ir.git
cd stiebel-eltron-climate-ir
uv sync
uv run prek install --hook-type pre-commit --hook-type commit-msg --hook-type pre-push
```

That is the whole setup, on Linux, macOS or Windows. Nothing else is needed to run
every test, including the ones that drive Home Assistant.

Name every hook type. prek has been reported to wire only `pre-commit` when left to
read `default_install_hook_types`, which silently drops the commit-message and
pre-push checks.

### Where the virtualenv lives

`uv sync` keeps the environment in uv's cache and makes `.venv` a link to it, which
`pyproject.toml` requests with uv's `centralized-project-envs`. Activation, the git
hooks and your editor all use `.venv` as usual and need to know nothing about it.

This matters on one kind of machine and is invisible on the rest. Home Assistant
brings 129 packages, and the fixtures that walk them are punishing over a filesystem
that is not native — measured on a checkout under WSL2's `/mnt/c`, with the cache on
ext4 where uv puts it by default:

| | environment beside the repo | environment in uv's cache |
| - | --------------------------- | ------------------------- |
| `uv sync` | ~20 min | 2.2 s |
| nine integration tests | 19.0 s | 3.2 s |
| whole suite | 7m34s | ~1m35s |

An older uv ignores the unknown preview name with a warning and creates an ordinary
`.venv` directory, which works exactly as it always did.

## Testing

```bash
uv run pytest                                  # everything, ~1m30s
uv run pytest tests/unit -p no:homeassistant   # the fast loop, ~3s
```

One command runs every suite, because `pytest-homeassistant-custom-component`
supplies Home Assistant's own test fixtures, pinned to one exact Home Assistant so
the tests prove the integration against a known version. There is no separate
container run.

The devcontainer is still how Home Assistant *itself* is run against this
integration, and how hardware sessions are driven — see
[devcontainer.md](docs/ha_ir_platform/devcontainer.md). It is not needed to run the
tests.

| suite | needs |
| ----- | ----- |
| `tests/unit` | nothing but this repository |
| `tests/integration` | Home Assistant, from the pinned test-fixture package |
| `tests/hardware` | the KC868-AG, the ACP 35, or the original remote |

`-p no:homeassistant` disables the fixture plugin. Worth it when iterating on a
codec: loading Home Assistant's fixtures costs about ten seconds per session and a
unit test needs none of them.

### The three markers

`addopts` deselects `hardware` by default, so a plain `uv run pytest` never reaches
for a device. Every `manual` and `disruptive` test also carries `hardware`, so that
single filter excludes all three.

| marker | means |
| ------ | ----- |
| `hardware` | needs the KC868-AG, the ACP 35, or the original remote |
| `manual` | needs a human to press a button or watch the unit |
| `disruptive` | transmits frames a listening appliance will act on |

```bash
uv run pytest -m hardware -s                 # everything, prompts included
uv run pytest -m "hardware and not manual"   # device needed, human not
```

`disruptive` needs more than a marker, because selecting it is not the same as
consenting to it. Those tests change the state of a real appliance, so they skip
unless `HW_RESTORE` names the state to put it back to:

```bash
HW_RESTORE="on,cool,medium,21" uv run pytest -m hardware
```

### Coverage

```bash
uv run pytest --cov --cov-report=term-missing
```

VS Code's Test Explorer drives the same run through **Run Tests with Coverage**.
Coverage flags stay out of `addopts` deliberately: applying them to every run means
a single test reports the rest of the codebase as uncovered, which paints the editor
red for no reason.

Coverage is already produced in Codecov's format (`coverage/coverage.xml`). Enabling
the upload is two steps: add a `CODECOV_TOKEN` repository secret, and uncomment the
step at the end of `.github/workflows/ci.yaml`.

## Git hooks

Three stages, installed by the `prek install` line above:

| stage | what runs |
| ----- | --------- |
| `pre-commit` | gitleaks, zizmor, ruff check and format, whitespace and file hygiene, version agreement between `pyproject.toml` and `manifest.json`, and whether git is configured to sign |
| `commit-msg` | `cz check` on the message |
| `pre-push` | every commit carries a signature, version tags match `manifest.json`, and the full test suite |

These are a convenience, not the enforcement. What decides is the ruleset on `main`
and the CI workflows, which run the same checks on what actually arrives.

## Commit conventions

[Conventional Commits](https://www.conventionalcommits.org/), enforced by `cz check`
in the `commit-msg` hook. `uv run cz info` prints the current rules; `uv run cz
commit` walks you through a message.

```text
<type>(<scope>): <subject>

<body>

<footer>
```

Two types have release consequences:

| type | version step | changelog |
| ---- | ------------ | --------- |
| `feat` | minor | **Feat** |
| `fix` | patch | **Fix** |
| any type with `!`, or a `BREAKING CHANGE:` footer | minor while the project is pre-1.0 | its own section |

`refactor`, `perf`, `docs`, `style`, `test`, `build`, `ci`, `chore` and `revert` are
accepted and carry no release consequence — they neither move the version nor appear
in `CHANGELOG.md`. `refactor` and `perf` are local exceptions to stock Conventional
Commits, which would have them bump a patch; a release made only of internal
restructuring is not a release.

Use `fix(security):` for a security fix, so it is findable in the history.

Subjects are imperative and lower case, with no trailing period: *add the receiver
entity*, not *Added the receiver entity.*

## Signed commits

**Every commit on `main` must be signed.** A repository ruleset rejects an unsigned
push, and CI checks that GitHub marked each pushed commit `verified` — which is
stricter than carrying a signature, since an unknown or untrusted key fails it.

```bash
git config user.signingkey <your-key-id>
git config commit.gpgsign true
git config tag.gpgsign true
```

SSH signing works equally well; set `gpg.format ssh` and point `user.signingkey` at
your public key. Either way the key has to be registered on your GitHub account, or
GitHub will show the commit as unverified and CI will say so.

The ruleset is what decides. Whatever happened on your machine, an unsigned commit
is refused at the push.

## Release process

Releases are cut by hand and published by CI:

```bash
uv run cz bump --check-consistency
git push && git push --tags
```

`cz bump` reads the conventional commits since the last tag, computes the version
step, writes it into `pyproject.toml`, `uv.lock` and `manifest.json`, updates
`CHANGELOG.md`, commits, and creates a signed annotated tag. Nothing in CI decides a
version.

Pushing the tag runs `release.yaml`, which re-runs the full test suite, hassfest and
HACS validation against the tagged tree and publishes a GitHub Release only if all
three pass. A failure leaves the tag on the remote with no release attached, which
is the recoverable state.

## Pull requests

- One concern per pull request.
- `uv run pytest` passes, and new behaviour comes with a test.
- Every commit signed and conventionally formatted — CI checks both.
- For anything touching the protocol, say what the evidence is: a capture from the
  original remote, or an observed response from the appliance. "It seems more
  correct" is not enough to change a frame, because the remote's behaviour is the
  specification.
- Documentation changes are welcome on their own.

Hardware you may not have is not a barrier. Say what you could not test and it can
be checked against a real ACP 35 during review.
