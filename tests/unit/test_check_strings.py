"""Tests for tools/check_strings.py.

Two things carry the risk. A file that cannot be read must never pass as "they
agree", and a difference must be reported by its key path -- a check that only
says "these files differ" leaves somebody comparing two JSON files by eye.
"""

import json

import check_strings
import pytest


def paths(source: dict, translation: dict) -> list[str]:
    """Return the reported key paths, without their explanations."""
    return [
        line.strip().split(":", 1)[0]
        for line in check_strings.differences(source, translation)
    ]


class TestFilesThatAgree:
    """Nothing to report."""

    def test_identical_trees_report_nothing(self):
        tree = {"config": {"error": {"emitter_in_use": "Choose a different emitter."}}}
        assert check_strings.differences(tree, json.loads(json.dumps(tree))) == []

    def test_key_order_is_not_a_difference(self):
        # Compared as parsed JSON, so the two files are free to be formatted
        # differently while the strings themselves must not differ.
        source = {"a": "1", "b": "2"}
        assert check_strings.differences(source, {"b": "2", "a": "1"}) == []

    def test_an_empty_pair_agrees(self):
        assert check_strings.differences({}, {}) == []


class TestFilesThatDisagree:
    """Every difference is named by where it is."""

    def test_a_changed_string_is_named_by_its_full_path(self):
        assert paths(
            {"config": {"error": {"emitter_in_use": "old"}}},
            {"config": {"error": {"emitter_in_use": "new"}}},
        ) == ["config.error.emitter_in_use"]

    def test_a_key_only_in_the_source_is_reported(self):
        # The direction that matters most: edited in strings.json alone, so every
        # check passes and the interface keeps showing the old text.
        source = {"config": {"abort": {"already_in_progress": "..."}}}
        assert paths(source, {"config": {"abort": {}}}) == [
            "config.abort.already_in_progress"
        ]

    def test_a_key_only_in_the_translation_is_reported(self):
        assert paths({"config": {}}, {"config": {"step": {}}}) == ["config.step"]

    @pytest.mark.parametrize(
        ("source", "translation"),
        [
            ({"a": "1"}, {"a": 1}),
            ({"a": {"b": "1"}}, {"a": "1"}),
            ({"a": "1"}, {"a": None}),
        ],
    )
    def test_a_value_of_a_different_shape_is_a_difference(self, source, translation):
        assert check_strings.differences(source, translation)

    def test_every_difference_is_reported_not_just_the_first(self):
        # A hook that stops at the first one turns a single edit into several
        # rounds of commit, fail, fix.
        assert len(paths({"a": "1", "b": "2"}, {"a": "9", "b": "8"})) == 2


class TestUnreadableFiles:
    """A file that cannot be read fails; it never passes as agreeing."""

    @pytest.mark.parametrize("content", ["", "not json at all", "{"])
    def test_unparseable_json_is_None(self, tmp_path, content):
        path = tmp_path / "broken.json"
        path.write_text(content, encoding="utf-8")
        assert check_strings.load(str(path)) is None

    def test_a_missing_file_is_None(self, tmp_path):
        assert check_strings.load(str(tmp_path / "absent.json")) is None

    def test_main_fails_when_a_file_is_missing(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(check_strings, "SOURCE", str(tmp_path / "absent.json"))
        assert check_strings.main() == 1
        assert "Unreadable" in capsys.readouterr().err


class TestTheRealFiles:
    """The two files this repository ships."""

    def test_they_agree(self, capsys):
        # The hook runs only when one of them changes; this runs on every suite,
        # so a drift committed some other way is still caught.
        assert check_strings.main() == 0, capsys.readouterr().err
