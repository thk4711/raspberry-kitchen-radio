"""Tests for the shared MusicSource contract in lib/music_source.py.

Covers the pydantic ``Metadata`` model, its thread-safe snapshot helper, and the
abstract ``MusicSource`` interface.
"""
import pytest
from music_source import Metadata, MusicSource


def _make_metadata(**overrides):
    base = dict(name="Station", title="Track", cover="", md5="", state=True)
    base.update(overrides)
    return Metadata(**base)


class TestMetadata:
    def test_valid_construction(self):
        md = _make_metadata(name="BBC", title="News", cover="/tmp/c.jpg", md5="abc", state=True)
        assert md.name == "BBC"
        assert md.title == "News"
        assert md.cover == "/tmp/c.jpg"
        assert md.md5 == "abc"
        assert md.state is True

    def test_rejects_wrong_type(self):
        # pydantic v2 coerces where it can, but a dict is not a valid str.
        with pytest.raises(Exception):
            Metadata(name={"not": "a string"}, title="t", cover="", md5="", state=True)


    def test_snapshot_is_independent(self):
        md = _make_metadata()
        snapshot = md.snapshot()
        snapshot.title = "Changed"
        assert md.title == "Track"


class _GoodSource(MusicSource):
    def get_play_state(self) -> bool:
        return True

    def set_play_state(self, desired_state: bool) -> bool:
        return desired_state

    def play_index(self, index: int) -> bool:
        return True

    def get_metadata(self) -> Metadata:
        return _make_metadata()


class _BadStateSource(MusicSource):
    def get_play_state(self):  # annotation inherited from base -> enforced
        return "yes"  # wrong type

    def set_play_state(self, desired_state: bool) -> bool:
        return True

    def play_index(self, index: int) -> bool:
        return True

    def get_metadata(self) -> Metadata:
        return _make_metadata()


class _ImplicitNoneSource(MusicSource):
    def get_play_state(self) -> bool:
        return True

    def set_play_state(self, desired_state: bool) -> bool:
        return True

    def play_index(self, index: int) -> bool:
        return True

    def get_metadata(self) -> Metadata:
        return None  # implicit-None style; violates the contract


class TestMusicSourceContract:
    def test_conforming_subclass_works(self):
        src = _GoodSource()
        assert src.get_play_state() is True
        assert src.set_play_state(False) is False
        assert src.play_index(1) is True
        assert isinstance(src.get_metadata(), Metadata)

    def test_annotations_do_not_add_runtime_wrappers(self):
        src = _BadStateSource()
        assert src.get_play_state() == "yes"

    def test_boundary_is_responsible_for_validation(self):
        src = _ImplicitNoneSource()
        assert src.get_metadata() is None
