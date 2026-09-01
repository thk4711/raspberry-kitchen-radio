# textformat.py
"""Pure text-formatting helpers for the now-playing display (Workstream 3).

No Pillow, no numpy, no hardware — just string logic, so these are
unit-testable on any machine (consistent with ``layout.py``). Two jobs:

* :func:`split_artist_title` — decide the two bottom-band text rows for each
  source. Spotify/AirPlay already deliver a clean artist/title split; MPD radio
  delivers a station name plus a stream ``title`` that is frequently
  ``"Artist - Title"``, which this parses when a separator is present.
* :func:`source_label` — the short uppercase badge text for the status strip
  ("RADIO" / "SPOTIFY" / "AIRPLAY").
"""
from __future__ import annotations

from typing import Optional, Tuple

# Separators commonly used by internet-radio stream titles between the artist
# and the track, in priority order. The spaced hyphen / en dash / em dash are
# tried first so hyphenated names ("Jean-Michel Jarre") are not split.
_TITLE_SEPARATORS = (" - ", " – ", " — ", " – ")


def _clean(text: Optional[str]) -> str:
    """Return ``text`` with surrounding whitespace stripped (``None`` -> "")."""
    return (text or "").strip()


def split_artist_title(
    name: str,
    title: str,
    art_mode: str,
) -> Tuple[str, str]:
    """Return ``(primary, secondary)`` text for the two bottom-band rows.

    ``primary`` is the larger, more prominent line (the track/title); the
    ``secondary`` line is the smaller subtitle (the artist/station).

    * Cover mode (Spotify/AirPlay): metadata is already split — ``title`` is
      the track (primary), ``name`` is the artist (secondary).
    * Radio mode (MPD): ``name`` is the station. When the stream ``title`` looks
      like ``"Artist - Title"`` it is split so the track becomes the primary
      line and the artist the secondary; otherwise the stream title is the
      primary line and the station name the secondary. When no stream title is
      present yet, the station name is shown as the primary line.

    Args:
        name: The ``name`` metadata field (artist or station).
        title: The ``title`` metadata field (track or stream title).
        art_mode: ``"cover"`` or ``"radio"``.

    Returns:
        A ``(primary, secondary)`` tuple of already-stripped strings.
    """
    name = _clean(name)
    title = _clean(title)

    if art_mode == "cover":
        # Already split by the backend; title is the track, name the artist.
        return title or name, title and name or ""

    # Radio mode.
    if title:
        for sep in _TITLE_SEPARATORS:
            if sep in title:
                artist, _, track = title.partition(sep)
                artist, track = artist.strip(), track.strip()
                if artist and track:
                    return track, artist
        # No usable separator: stream title is the headline, station the sub.
        return title, name
    # No stream metadata yet: show the station name alone.
    return name, ""


def source_label(source: Optional[str], art_mode: Optional[str] = None) -> str:
    """Return the uppercase status-strip badge text for a source.

    Args:
        source: The active backend name (``"mpd"`` / ``"spotify"`` /
            ``"airplay"``), if known.
        art_mode: Fallback when ``source`` is unknown (``"radio"`` -> RADIO,
            otherwise MUSIC).

    Returns:
        A short uppercase label, e.g. ``"RADIO"``, ``"SPOTIFY"``, ``"AIRPLAY"``.
    """
    mapping = {
        "mpd": "RADIO",
        "radio": "RADIO",
        "spotify": "SPOTIFY",
        "airplay": "AIRPLAY",
    }
    key = _clean(source).lower()
    if key in mapping:
        return mapping[key]
    if _clean(art_mode).lower() == "radio":
        return "RADIO"
    return "MUSIC"
