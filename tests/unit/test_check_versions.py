"""Tests for tools/check_versions.py.

Two things carry the risk: an unreadable file must never read as "the versions
match", and the tag list must never contain an empty name.
"""

import check_versions
import pytest

MANIFEST = '{"domain": "stiebel_eltron_ir", "version": "0.5.0"}'
PYPROJECT = '[project]\nname = "stiebel-eltron-ir"\nversion = "0.5.0"\n'


class TestManifestVersion:
    """Reading the version out of manifest.json content."""

    def test_reads_the_version(self):
        assert check_versions.manifest_version(MANIFEST) == "0.5.0"

    @pytest.mark.parametrize(
        "content", ["", "not json at all", "{}", '{"version": 5}', "[]", "null"]
    )
    def test_anything_unusable_is_None_not_a_match(self, content):
        # None must never compare equal to a version string, or a manifest that is
        # missing, malformed or numeric would pass as agreeing.
        assert check_versions.manifest_version(content) is None


class TestProjectVersion:
    """Reading the version out of pyproject.toml content."""

    def test_reads_the_project_version(self):
        assert check_versions.project_version(PYPROJECT) == "0.5.0"

    @pytest.mark.parametrize(
        "content",
        [
            "",
            "this is not toml = = =",
            "[project]\nname = 'x'\n",
            "[project]\nversion = 5\n",
            "[tool.other]\nversion = '0.5.0'\n",
            "project = 'not a table'\n",
        ],
    )
    def test_anything_unusable_is_None_not_a_match(self, content):
        assert check_versions.project_version(content) is None

    def test_does_not_take_a_version_from_another_table(self):
        # `[tool.commitizen]` has no version key today, but a future one must not
        # be mistaken for the project's.
        toml = "[project]\nversion = '0.5.0'\n[tool.commitizen]\nversion = '9.9.9'\n"
        assert check_versions.project_version(toml) == "0.5.0"


class TestCheckFiles:
    """The pre-commit mode."""

    def _files(self, monkeypatch, pyproject, manifest):
        monkeypatch.setattr(
            check_versions,
            "_read",
            lambda path: pyproject if path == check_versions.PYPROJECT else manifest,
        )

    def test_passes_when_both_record_the_same_version(self, monkeypatch):
        self._files(monkeypatch, PYPROJECT, MANIFEST)
        assert check_versions.check_files() == 0

    def test_fails_and_prints_both_when_they_differ(self, monkeypatch, capsys):
        self._files(monkeypatch, PYPROJECT, '{"version": "0.4.0"}')
        assert check_versions.check_files() == 1
        err = capsys.readouterr().err
        assert "0.5.0" in err
        assert "0.4.0" in err

    def test_an_unreadable_manifest_is_a_failure(self, monkeypatch, capsys):
        self._files(monkeypatch, PYPROJECT, "")
        assert check_versions.check_files() == 1
        assert "unreadable" in capsys.readouterr().err

    def test_an_unreadable_pyproject_is_a_failure(self, monkeypatch, capsys):
        # Both unreadable is still a failure, not two Nones comparing equal.
        self._files(monkeypatch, "", "")
        assert check_versions.check_files() == 1
        assert "unreadable" in capsys.readouterr().err


class TestVersionTags:
    """Listing the local version tags."""

    def test_returns_every_version_tag_sorted(self, monkeypatch):
        monkeypatch.setattr(check_versions, "_git", lambda *a: "v0.2.0\nv0.1.0\nv0.3.0")
        assert check_versions.version_tags() == ["v0.1.0", "v0.2.0", "v0.3.0"]

    def test_no_local_tags_is_not_one_empty_name(self, monkeypatch):
        # `"".split("\n")` is `[""]`, which would become a tag named "" and then a
        # mismatch reported against a tag that does not exist.
        monkeypatch.setattr(check_versions, "_git", lambda *a: "")
        assert check_versions.version_tags() == []

    def test_asks_git_for_local_tags_only(self, monkeypatch):
        # The check must work offline: no `ls-remote`, no network, on any push.
        seen = []
        monkeypatch.setattr(
            check_versions, "_git", lambda *a: seen.append(a) or "v0.1.0"
        )
        check_versions.version_tags()
        assert seen == [("tag", "--list", "v*")]


class TestCheckTags:
    """The pre-push mode, with git stubbed out."""

    def test_passes_when_there_are_no_tags(self, monkeypatch):
        monkeypatch.setattr(check_versions, "version_tags", list)
        assert check_versions.check_tags() == 0

    def test_passes_when_the_tag_matches(self, monkeypatch):
        monkeypatch.setattr(check_versions, "version_tags", lambda: ["v0.5.0"])
        monkeypatch.setattr(check_versions, "manifest_version_at", lambda t: "0.5.0")
        assert check_versions.check_tags() == 0

    def test_names_only_the_mismatched_tag(self, monkeypatch, capsys):
        monkeypatch.setattr(
            check_versions, "version_tags", lambda: ["v0.5.0", "v0.6.0"]
        )
        monkeypatch.setattr(
            check_versions,
            "manifest_version_at",
            lambda t: "0.5.0" if t == "v0.5.0" else "0.1.0",
        )
        assert check_versions.check_tags() == 1
        err = capsys.readouterr().err
        assert "v0.6.0 expects 0.6.0" in err
        assert "manifest.json says 0.1.0" in err
        assert "v0.5.0" not in err

    def test_a_missing_manifest_fails_and_says_so(self, monkeypatch, capsys):
        monkeypatch.setattr(check_versions, "version_tags", lambda: ["v0.5.0"])
        monkeypatch.setattr(check_versions, "manifest_version_at", lambda t: None)
        assert check_versions.check_tags() == 1
        assert "missing or unreadable" in capsys.readouterr().err

    def test_reads_the_manifest_from_the_tagged_tree(self, monkeypatch):
        # Not from the working copy: a tag made at the wrong commit is one of the
        # failures being looked for, and only the tagged tree exposes it.
        seen = []
        monkeypatch.setattr(
            check_versions, "_git", lambda *a: seen.append(a) or MANIFEST
        )
        assert check_versions.manifest_version_at("v0.5.0") == "0.5.0"
        assert seen == [("show", f"v0.5.0:{check_versions.MANIFEST}")]


class TestMain:
    """Argument handling."""

    @pytest.mark.parametrize("argv", [[], ["files", "tags"], ["nonsense"]])
    def test_rejects_anything_but_one_known_mode(self, argv, capsys):
        assert check_versions.main(argv) == 2
        assert "usage:" in capsys.readouterr().err
