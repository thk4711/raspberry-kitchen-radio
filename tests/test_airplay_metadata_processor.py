"""Tests for AirplayMetadataProcessor in lib/airplay_service.

Covers the pure parsing/decoding logic: XML <item> parsing, base64 data
detection and decoding, image MIME sniffing by magic number, change detection,
and a full process_line flow using a fake pipe.
"""
import base64
import io

from airplay_service.airplay_metadata_processor import AirplayMetadataProcessor


def _hex(s):
    return s.encode().hex()


class TestStartItem:
    def test_parses_type_code_length(self):
        proc = AirplayMetadataProcessor()
        # type "core", code "minm", length 5
        line = f"<item><type>{_hex('core')}</type><code>{_hex('minm')}</code><length>5</length>"
        item_type, item_code, length = proc.start_item(line)
        assert item_type == "core"
        assert item_code == "minm"
        assert length == 5

    def test_no_match_returns_empty(self):
        proc = AirplayMetadataProcessor()
        assert proc.start_item("<item>garbage") == ("", "", 0)


class TestStartData:
    def test_detects_base64_header(self):
        proc = AirplayMetadataProcessor()
        assert proc.start_data('<data encoding="base64">abc') is True

    def test_non_base64(self):
        proc = AirplayMetadataProcessor()
        assert proc.start_data("<data>abc") is False


class TestReadData:
    def test_decodes_to_string(self):
        proc = AirplayMetadataProcessor()
        payload = base64.b64encode(b"hello").decode()
        line = f"{payload}</data>"
        assert proc.read_data(line, decode=True) == "hello"

    def test_returns_bytes_when_not_decoding(self):
        proc = AirplayMetadataProcessor()
        payload = base64.b64encode(b"\xff\xd8raw").decode()
        line = f"{payload}</data>"
        assert proc.read_data(line, decode=False) == b"\xff\xd8raw"

    def test_garbage_returns_empty_string(self):
        proc = AirplayMetadataProcessor()
        assert proc.read_data("!!!not base64!!!</data>", decode=True) == ""


class TestGuessImageMime:
    def test_jpeg_magic(self):
        proc = AirplayMetadataProcessor()
        assert proc.guess_image_mime(b"\xff\xd8\xff\xe0rest") == "jpg"

    def test_png_magic(self):
        proc = AirplayMetadataProcessor()
        assert proc.guess_image_mime(b"\x89PNG\r\n\x1a\rrest") == "png"

    def test_default_is_jpg(self):
        proc = AirplayMetadataProcessor()
        assert proc.guess_image_mime(b"whatever") == "jpg"


class TestUpdateMetadata:
    def test_returns_dict_on_change(self):
        proc = AirplayMetadataProcessor()
        proc.meta_data["track"] = "New Track"
        result = proc.update_metadata()
        assert result is not None
        assert result["track"] == "New Track"

    def test_returns_none_when_unchanged(self):
        proc = AirplayMetadataProcessor()
        # No changes vs. the initial (empty) state.
        assert proc.update_metadata() is None


class TestProcessLine:
    def test_non_item_line_returns_none(self):
        proc = AirplayMetadataProcessor()
        pipe = io.StringIO("")
        assert proc.process_line("<something/>", pipe) is None

    def test_core_track_update_sets_state(self):
        proc = AirplayMetadataProcessor()
        track_b64 = base64.b64encode(b"My Song").decode()
        line = (
            f"<item><type>{_hex('core')}</type>"
            f"<code>{_hex('minm')}</code><length>7</length>"
        )
        # process_line reads the next two lines off the pipe: the <data ...>
        # header, then the base64 payload.
        pipe = io.StringIO(f'<data encoding="base64">\n{track_b64}</data>\n')
        # A core/minm item does not itself flush; it just records the track.
        proc.process_line(line, pipe)
        assert proc.meta_data["track"] == "My Song"

    def test_flush_marker_returns_accumulated_metadata(self):
        proc = AirplayMetadataProcessor()
        proc.meta_data["track"] = "Cached Track"
        # ssnc/pfls with length 0 triggers update_metadata() and a flush.
        line = (
            f"<item><type>{_hex('ssnc')}</type>"
            f"<code>{_hex('pfls')}</code><length>0</length>"
        )
        pipe = io.StringIO("")
        result = proc.process_line(line, pipe)
        assert result is not None
        assert result["track"] == "Cached Track"
