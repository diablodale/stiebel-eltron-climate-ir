"""Tests for tools/acp35_cli.py."""

import acp35_cli
import pytest
from conftest import PROTOCOL_DOC, load_captures
from devices.acp35.protocol import Acp35Command, Acp35Fan, Acp35Mode

CAPTURES = load_captures()


@pytest.fixture
def first_capture_log() -> str:
    """Return the first capture as ESPHome would have logged it."""
    return f"[21:02:45][I][remote.pronto:233]: {CAPTURES[0].pronto}"


class TestReadTimings:
    """Input sniffing: ESPHome log, bare Pronto, or raw signed timings."""

    def test_reads_an_esphome_log_line(self, first_capture_log):
        (timings,) = acp35_cli.read_timings(first_capture_log)
        assert timings == CAPTURES[0].timings

    def test_reads_a_bare_pronto_code(self):
        (timings,) = acp35_cli.read_timings(CAPTURES[0].pronto)
        assert timings == CAPTURES[0].timings

    def test_reads_raw_signed_timings(self):
        expected = Acp35Command(
            power=True, mode=Acp35Mode.COOL, fan=Acp35Fan.HIGH, celsius=22
        ).get_raw_timings()
        (timings,) = acp35_cli.read_timings(", ".join(map(str, expected)))
        assert timings == expected

    def test_reads_several_captures_at_once(self):
        text = "\n".join(
            f"[I][remote.pronto:233]: {capture.pronto}" for capture in CAPTURES[:3]
        )
        assert len(acp35_cli.read_timings(text)) == 3

    def test_returns_nothing_for_unrelated_text(self):
        assert acp35_cli.read_timings("no codes here at all") == []


class TestDecodeStream:
    """Reading from stdin."""

    def test_decodes_and_reports_the_state(self, capsys, first_capture_log):
        assert acp35_cli.decode_stream(first_capture_log) == 0
        out = capsys.readouterr().out
        assert "55 32 00 07 00 00 31 C0 7F" in out
        assert "19 C / 66 F" in out
        assert "cool" in out
        assert "high" in out

    def test_reports_a_frame_it_cannot_decode(self, capsys):
        assert acp35_cli.decode_stream("100, -100, 100, -100") == 1
        assert "not an ACP 35 frame" in capsys.readouterr().out

    def test_reports_empty_input(self, capsys):
        assert acp35_cli.decode_stream("nothing useful") == 2
        assert "no Pronto code" in capsys.readouterr().err


class TestDecodeDocument:
    """Reading the protocol document."""

    def test_every_capture_decodes(self, capsys):
        assert acp35_cli.decode_document(PROTOCOL_DOC, "text") == 0
        assert len(capsys.readouterr().out.strip().splitlines()) == len(CAPTURES)

    def test_bytes_format_matches_the_decoder(self, capsys):
        assert acp35_cli.decode_document(PROTOCOL_DOC, "bytes") == 0
        lines = capsys.readouterr().out.split()
        expected = Acp35Command.from_raw_timings(CAPTURES[0].timings).to_bytes()
        assert lines[:9] == expected.hex(" ").upper().split()

    def test_table_format_is_markdown(self, capsys):
        assert acp35_cli.decode_document(PROTOCOL_DOC, "table") == 0
        lines = capsys.readouterr().out.splitlines()
        assert lines[0].startswith("| # |")
        assert set(lines[1]) <= set("| -")
        assert len(lines) == len(CAPTURES) + 2

    def test_missing_document_is_reported(self, capsys, tmp_path):
        assert acp35_cli.decode_document(tmp_path / "empty.md", "text") == 2


class TestMain:
    """Argument handling."""

    def test_document_flag_defaults_to_the_protocol_document(self, capsys):
        assert acp35_cli.main(["--document"]) == 0
        assert "55 32 00 07 00 00 31 C0 7F" in capsys.readouterr().out

    def test_unknown_format_is_rejected(self):
        with pytest.raises(SystemExit):
            acp35_cli.main(["--document", "--format", "yaml"])
