"""Unit tests for the pure artist/title split and source-label helpers (WS3).

No Pillow, no numpy, no hardware — pure string logic, runnable anywhere.
"""
from display_1_inch_69 import textformat


def test_cover_mode_uses_backend_split():
    # Spotify/AirPlay already split: title is track (primary), name is artist.
    primary, secondary = textformat.split_artist_title("Daft Punk", "One More Time", "cover")
    assert primary == "One More Time"
    assert secondary == "Daft Punk"


def test_cover_mode_missing_artist_shows_only_title():
    primary, secondary = textformat.split_artist_title("", "Some Track", "cover")
    assert primary == "Some Track"
    assert secondary == ""


def test_radio_mode_splits_artist_dash_title():
    primary, secondary = textformat.split_artist_title(
        "Deutschlandfunk", "Coldplay - Yellow", "radio")
    assert primary == "Yellow"      # track is the headline
    assert secondary == "Coldplay"  # artist is the subtitle


def test_radio_mode_splits_on_en_dash():
    primary, secondary = textformat.split_artist_title(
        "Station", "Artist \u2013 Track", "radio")
    assert primary == "Track"
    assert secondary == "Artist"


def test_radio_mode_does_not_split_hyphenated_name():
    # A bare hyphen without surrounding spaces must not split.
    primary, secondary = textformat.split_artist_title(
        "Station", "Jean-Michel Jarre", "radio")
    assert primary == "Jean-Michel Jarre"
    assert secondary == "Station"


def test_radio_mode_no_stream_title_shows_station():
    primary, secondary = textformat.split_artist_title("MDR JUMP", "", "radio")
    assert primary == "MDR JUMP"
    assert secondary == ""


def test_radio_mode_strips_trailing_space_from_stream_title():
    # MPD appends a trailing space to the stream title.
    primary, secondary = textformat.split_artist_title(
        "Station", "Nice Song ", "radio")
    assert primary == "Nice Song"


def test_source_label_known_sources():
    assert textformat.source_label("mpd") == "RADIO"
    assert textformat.source_label("spotify") == "SPOTIFY"
    assert textformat.source_label("airplay") == "AIRPLAY"


def test_source_label_case_insensitive():
    assert textformat.source_label("Spotify") == "SPOTIFY"


def test_source_label_falls_back_to_art_mode():
    assert textformat.source_label("", "radio") == "RADIO"
    assert textformat.source_label(None, "cover") == "MUSIC"
    assert textformat.source_label(None) == "MUSIC"
