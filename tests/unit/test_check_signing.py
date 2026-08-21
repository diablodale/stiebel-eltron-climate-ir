"""Tests for tools/check_signing.py.

The interesting part is the parsing: every case here is a way a naive check passes
something it should not, or fails something it should not.
"""

import check_signing
import pytest

SIGNED = b"""tree 726c37c5df952c8f428f0d8301974650d89f3679
parent b83f530b8ada3dafd5af8d90f872ff60f7d7877c
author Dale Phurrough <dale@hidale.com> 1787112023 +0200
committer Dale Phurrough <dale@hidale.com> 1787112023 +0200
gpgsig -----BEGIN PGP SIGNATURE-----
 \x20
 iHUEABYKAB0WIQScbl6cnUEIfJfcNDoZ9e3OT7C3IAUCaoUqWAAKCRAZ9e3OT7C3
 IH68AQCiBQZ0tyXFwuNJRQAXDdHqaoXzfJ8/TC1SX2oB3rK/2gEA5W1EtelxV0t1
 -----END PGP SIGNATURE-----

test: measure carrier the ACP 35 hears best
"""

UNSIGNED = b"""tree 726c37c5df952c8f428f0d8301974650d89f3679
parent b83f530b8ada3dafd5af8d90f872ff60f7d7877c
author Dale Phurrough <dale@hidale.com> 1787112023 +0200
committer Dale Phurrough <dale@hidale.com> 1787112023 +0200

test: measure carrier the ACP 35 hears best
"""


class TestIsSigned:
    """Which raw commit objects count as carrying a signature."""

    def test_accepts_a_gpgsig_header(self):
        assert check_signing.is_signed(SIGNED)

    def test_rejects_a_commit_with_no_signature(self):
        assert not check_signing.is_signed(UNSIGNED)

    def test_accepts_the_sha256_header(self):
        assert check_signing.is_signed(SIGNED.replace(b"gpgsig ", b"gpgsig-sha256 "))

    def test_accepts_an_ssh_signature(self):
        # SSH signing writes the same header with a different payload, so the
        # check must not care which format the signature is in.
        ssh = SIGNED.replace(
            b"-----BEGIN PGP SIGNATURE-----", b"-----BEGIN SSH SIGNATURE-----"
        )
        assert check_signing.is_signed(ssh)

    def test_a_message_body_cannot_forge_a_signature(self):
        # The single case that decides the whole implementation: a commit message
        # quoting the header, on an otherwise unsigned commit, must not pass.
        forged = UNSIGNED + b"\ngpgsig -----BEGIN PGP SIGNATURE-----\n"
        assert not check_signing.is_signed(forged)

    def test_a_message_body_alone_is_not_searched(self):
        # Same trap from the other direction: the header block ends at the first
        # blank line, so nothing after it is ever examined.
        assert b"gpgsig" not in check_signing.header_block(
            UNSIGNED + b"\ngpgsig anything\n"
        )


class TestHeaderBlock:
    """Splitting a raw commit object into headers and message."""

    def test_keeps_every_continuation_line_of_the_signature(self):
        # A PGP signature spans many lines, each continued by a leading space. A
        # parser that stopped at the first line would still find `gpgsig`, but one
        # that split on the wrong boundary would swallow the message too.
        block = check_signing.header_block(SIGNED)
        # tree, parent, author, committer, then five signature lines, the last of
        # which has no newline because the split consumed the blank line.
        assert block.count(b"\n") == 8
        assert b"END PGP SIGNATURE" in block
        assert b"test: measure carrier" not in block

    def test_a_commit_with_no_message_is_all_header(self):
        assert check_signing.header_block(b"tree abc\n") == b"tree abc\n"


class TestMain:
    """Argument handling."""

    @pytest.mark.parametrize("argv", [[], ["config", "commits"], ["nonsense"]])
    def test_rejects_anything_but_one_known_mode(self, argv, capsys):
        assert check_signing.main(argv) == 2
        assert "usage:" in capsys.readouterr().err


class TestCheckCommits:
    """The pre-push mode, with git stubbed out."""

    def test_passes_when_nothing_is_unpushed(self, monkeypatch):
        # An empty range is a pass, not a failure: there is nothing to object to.
        monkeypatch.setattr(check_signing, "unpushed_commits", list)
        assert check_signing.check_commits() == 0

    def test_names_each_unsigned_commit(self, monkeypatch, capsys):
        monkeypatch.setattr(
            check_signing, "unpushed_commits", lambda: ["aaa1111", "bbb2222"]
        )
        monkeypatch.setattr(
            check_signing,
            "subprocess",
            _FakeSubprocess({"aaa1111": SIGNED, "bbb2222": UNSIGNED}),
        )
        assert check_signing.check_commits() == 1
        err = capsys.readouterr().err
        assert "bbb2222" in err
        assert "aaa1111" not in err


class _FakeSubprocess:
    """Enough of `subprocess` for check_commits: cat-file and log."""

    def __init__(self, objects: dict[str, bytes]):
        self.objects = objects
        self.CalledProcessError = Exception

    def run(self, args, **kwargs):
        if args[1] == "cat-file":
            return _Result(stdout=self.objects[args[3]])
        return _Result(stdout=f"{args[-1]} a commit subject")


class _Result:
    """A stand-in for CompletedProcess."""

    def __init__(self, stdout):
        self.stdout = stdout
        self.returncode = 0
