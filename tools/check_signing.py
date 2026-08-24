"""Refuse to make or push an unsigned commit.

Signing is required in this repository, and the enforcement that matters is
server-side: a ruleset on `main` rejects an unsigned push, and CI asserts GitHub
marked each commit verified. These checks are the local half -- they catch the
mistake in the second it is made, rather than at the push that would otherwise be
the first news of it.

Neither mode verifies a signature. That is deliberate and not a shortcut: this
working copy points `gpg.program` at the Windows GnuPG, which cannot open the Linux
temp path `git verify-commit` hands it, so verification fails on every commit here
whether signed or not. What can be established locally is that a commit *will* be
signed, and that one *carries* a signature; whether that signature is valid and
from a known key is what GitHub decides.

    config    pre-commit -- is git configured to sign the commit about to be made
    commits   pre-push   -- does every commit about to leave carry a signature
"""

# PEP 723 inline metadata, so `uv run --script` runs this without the project
# environment -- see tools/check_versions.py for the reasoning. `>=3.11` matches
# that file rather than anything this one needs; it is stdlib-only and would run
# on far older.
#
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///

import subprocess
import sys

# git writes a commit's headers before a blank line, then the message. A message
# body may contain anything, including a line that looks like a signature header,
# so only the header block is ever searched.
SIGNATURE_HEADERS = (b"gpgsig", b"gpgsig-sha256")


def _git(*args: str) -> str:
    """Return the stdout of a git command, or "" if it failed."""
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def header_block(commit_object: bytes) -> bytes:
    """Return the header block of a raw commit object, without the message.

    The block ends at the first empty line. A signature header spans several
    lines, each continuation indented by one space, so the whole block is kept
    rather than only its first lines.
    """
    return commit_object.split(b"\n\n", 1)[0]


def is_signed(commit_object: bytes) -> bool:
    """Is a signature header present in this raw commit object."""
    return any(
        line.startswith(header)
        for line in header_block(commit_object).split(b"\n")
        for header in SIGNATURE_HEADERS
    )


def check_config() -> int:
    """Fail unless git is configured to sign the commit about to be created."""
    if _git("config", "--get", "commit.gpgsign") == "true":
        return 0
    print(
        "commit.gpgsign is not true, so this commit would be unsigned.\n"
        "This repository requires signed commits; a push to main is rejected "
        "without one.\n"
        "Fix with:  git config commit.gpgsign true",
        file=sys.stderr,
    )
    return 1


def unpushed_commits() -> list[str]:
    """Return the commits reachable from HEAD that no remote already has.

    Before the first push there is no remote-tracking branch, so this is the whole
    history -- which is the correct answer: all of it is about to be published.
    """
    out = _git("rev-list", "HEAD", "--not", "--remotes")
    return out.split("\n") if out else []


def check_commits() -> int:
    """Fail if any commit about to be pushed carries no signature."""
    unsigned = [
        sha
        for sha in unpushed_commits()
        if not is_signed(
            subprocess.run(
                ["git", "cat-file", "commit", sha],
                capture_output=True,
                check=True,
            ).stdout
        )
    ]
    if not unsigned:
        return 0
    print("These commits are not signed and cannot be pushed:", file=sys.stderr)
    for sha in unsigned:
        print(f"  {_git('log', '-1', '--pretty=%h %s', sha)}", file=sys.stderr)
    print(
        "\nSign them by rebasing, or amend the most recent with:\n"
        "  git commit --amend --no-edit -S",
        file=sys.stderr,
    )
    return 1


MODES = {"config": check_config, "commits": check_commits}


def main(argv: list[str]) -> int:
    """Run one mode, named by the single argument."""
    if len(argv) != 1 or argv[0] not in MODES:
        print(f"usage: check_signing.py {'|'.join(MODES)}", file=sys.stderr)
        return 2
    return MODES[argv[0]]()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
