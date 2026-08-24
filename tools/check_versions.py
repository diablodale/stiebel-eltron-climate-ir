"""Keep the recorded version numbers from drifting apart.

`cz bump` writes the version into pyproject.toml, uv.lock and manifest.json and
creates the tag `v<version>`, all in one step, so they agree by construction. They
come apart only by hand: a file edited directly, or `cz bump --files-only` followed
by tagging separately.

Two modes, at the two moments a disagreement can still be undone cheaply:

    files   pre-commit -- do pyproject.toml and manifest.json record the same version
    tags    pre-push   -- does each `v*` tag match manifest.json at that tag

`release.yaml` makes the second comparison too, and refuses to publish on a
mismatch. Having it locally as well is what keeps a bad tag off the remote: caught
here it is one `git tag -d`, caught server-side it has to be deleted in two places.

uv.lock carries the version a third time and is deliberately not checked here.
`uv sync --locked` in CI fails loudly if it drifts, which is a better test than
re-implementing uv's lock format.

manifest.json is the file every comparison runs against, because it is the one Home
Assistant and HACS actually read.
"""

# PEP 723 inline metadata, so `uv run --script` runs this without the project
# environment. It needs nothing but the standard library, and requiring the
# project's 3.14 and its 129 packages to compare two version strings would mean
# CI installing Home Assistant before it could run a git hook.
# `>=3.11` is the real floor -- `tomllib` arrived there
# `--no-python-downloads`, so uv may not fetch what it cannot find, and
# a GitHub runner ships 3.12.
#
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

import json
import subprocess
import sys
import tomllib
from pathlib import Path

# `tag_format = "v$version"` in pyproject, so a tag is the version with this prefix
# and nothing else.
TAG_PREFIX = "v"
MANIFEST = "custom_components/stiebel_eltron_ir/manifest.json"
PYPROJECT = "pyproject.toml"


def _git(*args: str) -> str:
    """Return the stdout of a git command, or "" if it failed."""
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def _read(path: str) -> str:
    """Return a file's text, or "" if it cannot be read."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return ""


def _version_string(value: object) -> str | None:
    """Return `value` if it is a version string, else None.

    None never compares equal to a version, so every unusable shape -- absent,
    numeric, nested -- fails the comparison rather than passing it.
    """
    return value if isinstance(value, str) else None


def manifest_version(content: str) -> str | None:
    """Return the `version` recorded in manifest.json content."""
    try:
        manifest = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(manifest, dict):
        return None
    return _version_string(manifest.get("version"))


def project_version(content: str) -> str | None:
    """Return `[project] version` from pyproject.toml content."""
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return None
    project = data.get("project")
    if not isinstance(project, dict):
        return None
    return _version_string(project.get("version"))


def check_files() -> int:
    """Fail unless pyproject.toml and manifest.json record the same version."""
    pyproject = project_version(_read(PYPROJECT))
    manifest = manifest_version(_read(MANIFEST))
    if pyproject is not None and pyproject == manifest:
        return 0
    print(
        f"The recorded versions disagree:\n"
        f"  {PYPROJECT}: {pyproject if pyproject is not None else 'unreadable'}\n"
        f"  {MANIFEST}: {manifest if manifest is not None else 'unreadable'}\n"
        "\n`cz bump` writes both together, so this is an edit to one of them by\n"
        "hand. Put them back in step rather than bumping on top of the drift; a\n"
        "version that disagrees with itself cannot be released.",
        file=sys.stderr,
    )
    return 1


def version_tags() -> list[str]:
    """Return every local version tag.

    Every one, not only the unpushed ones. Git keeps no local record of which tags
    a remote already has -- unlike branches, which have remote-tracking refs -- so
    narrowing the list would mean `git ls-remote`, and a network round trip to
    GitHub on every single push. A hook that cannot run offline is worse than one
    that checks a few tags twice.

    The cost is that an already-pushed tag is re-checked forever. That is one
    `git show` per tag on a repository that gains a few tags a year, and a
    historical tag that disagrees with its manifest is worth blocking on rather
    than passing over.
    """
    local = _git("tag", "--list", f"{TAG_PREFIX}*")
    return sorted(local.split("\n")) if local else []


def manifest_version_at(tag: str) -> str | None:
    """Return the version manifest.json carries *at that tag*.

    Read from the tagged tree rather than the working copy, because the two differ
    exactly when this check matters -- a tag made at the wrong commit is one of the
    failures being looked for.
    """
    return manifest_version(_git("show", f"{tag}:{MANIFEST}"))


def check_tags() -> int:
    """Fail if any local version tag disagrees with its manifest."""
    mismatched = []
    for tag in version_tags():
        expected = tag.removeprefix(TAG_PREFIX)
        found = manifest_version_at(tag)
        if found != expected:
            mismatched.append((tag, expected, found))
    if not mismatched:
        return 0
    print("These tags disagree with manifest.json:", file=sys.stderr)
    for tag, expected, found in mismatched:
        says = f"says {found}" if found is not None else "is missing or unreadable"
        print(f"  {tag} expects {expected}, but manifest.json {says}", file=sys.stderr)
    print(
        "\nHome Assistant and HACS read the version from manifest.json, and the\n"
        "release workflow refuses to publish while the two disagree.\n"
        "Delete the tag with:  git tag -d <tag>\n"
        "then let `cz bump` create it, so every file and the tag move together.",
        file=sys.stderr,
    )
    return 1


MODES = {"files": check_files, "tags": check_tags}


def main(argv: list[str]) -> int:
    """Run one mode, named by the single argument."""
    if len(argv) != 1 or argv[0] not in MODES:
        print(f"usage: check_versions.py {'|'.join(MODES)}", file=sys.stderr)
        return 2
    return MODES[argv[0]]()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
