"""Tests for the panel-selection factory (lib/display/panel_factory.py).

``get_panel_class`` maps the ``[display] panel`` config value to the correct
driver class.  These tests run entirely off-target (no SPI/GPIO needed) because
the factory only imports driver *classes*, not instances.

Covered invariants:

* ``"st7789"`` (and variants) → ``ST7789``
* ``"gc9a01"`` (and variants) → ``GC9A01``
* Unknown name → ``ST7789`` with a WARNING log entry (never raises)
* Empty string  → ``ST7789`` with a WARNING log entry
* Extra whitespace is stripped before the lookup
"""
import logging

import pytest
from display.panel_factory import get_panel_class
from display.panel_gc9a01 import GC9A01
from display.panel_st7789 import ST7789

# ---------------------------------------------------------------------------
# Happy-path: known names
# ---------------------------------------------------------------------------

def test_st7789_exact_name():
    assert get_panel_class("st7789") is ST7789


def test_gc9a01_exact_name():
    assert get_panel_class("gc9a01") is GC9A01


def test_st7789_uppercase():
    assert get_panel_class("ST7789") is ST7789


def test_gc9a01_uppercase():
    assert get_panel_class("GC9A01") is GC9A01


def test_st7789_mixed_case():
    assert get_panel_class("St7789") is ST7789


def test_gc9a01_mixed_case():
    assert get_panel_class("Gc9A01") is GC9A01


def test_st7789_with_surrounding_whitespace():
    assert get_panel_class("  st7789  ") is ST7789


def test_gc9a01_with_surrounding_whitespace():
    assert get_panel_class("  gc9a01  ") is GC9A01


# ---------------------------------------------------------------------------
# Fallback: unknown / blank names → ST7789 + warning
# ---------------------------------------------------------------------------

def test_unknown_name_falls_back_to_st7789(caplog):
    with caplog.at_level(logging.WARNING, logger="display.panel_factory"):
        result = get_panel_class("unknown_panel")
    assert result is ST7789
    assert any("unknown_panel" in r.message for r in caplog.records)
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_empty_string_falls_back_to_st7789(caplog):
    with caplog.at_level(logging.WARNING, logger="display.panel_factory"):
        result = get_panel_class("")
    assert result is ST7789
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_none_treated_as_empty_falls_back_to_st7789(caplog):
    with caplog.at_level(logging.WARNING, logger="display.panel_factory"):
        result = get_panel_class(None)  # type: ignore[arg-type]
    assert result is ST7789
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_whitespace_only_falls_back_to_st7789(caplog):
    with caplog.at_level(logging.WARNING, logger="display.panel_factory"):
        result = get_panel_class("   ")
    assert result is ST7789
    assert any(r.levelno == logging.WARNING for r in caplog.records)


# ---------------------------------------------------------------------------
# Return type is always a class (not an instance)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("st7789", ST7789),
    ("gc9a01", GC9A01),
])
def test_return_is_the_class_itself(name, expected):
    cls = get_panel_class(name)
    assert cls is expected
    assert isinstance(cls, type)
