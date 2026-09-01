"""Shared ``MusicSource`` abstraction and metadata value object.

Every playback backend (MPD internet radio, AirPlay, Spotify Connect, and any
future source) subclasses :class:`MusicSource`. The interface is statically
annotated and values are validated where the controller integrates a backend:

* :class:`Metadata` is the pydantic model returned by ``get_metadata`` and
  rendered on the display (see the field comments below).
``get_metadata`` must return a :class:`Metadata`; ``get_play_state``,
``set_play_state`` and ``play_index`` return ``bool``. See
``doc/adding-a-music-source.md`` for a worked example.
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel


class Metadata(BaseModel):
    """Track/source information shared between backends and the display.

    A :class:`Metadata` instance is what every :class:`MusicSource` returns from
    ``get_metadata`` and what the display renders. The field comments describe
    exactly how each value is used on screen.
    """

    name: str    # source/station/app name (top display row)
    title: str   # current track/title (bottom display row)
    cover: str   # path to a cover-art image file (or "" for none)
    md5: str     # hash of the cover; the display skips redraw when unchanged
    state: bool  # True while this source is actively playing

    def snapshot(self) -> "Metadata":
        """Return a deep copy suitable for passing between worker threads."""
        copier = getattr(self, "model_copy", None)
        return copier(deep=True) if copier else self.copy(deep=True)


class MusicSource(ABC):
    """Abstract base class for every playback backend.

    Subclasses (MPD, AirPlay, Spotify Connect, …) implement the four abstract
    methods below so the :class:`~radio.RadioController` can treat them
    interchangeably. Implementations are statically annotated; the controller
    validates values at the integration boundary so errors name the backend and
    operation that violated the contract.
    """

    @abstractmethod
    def get_play_state(self) -> bool:
        """
        Get the play state of the music source.
        """
        pass

    @abstractmethod
    def set_play_state(self, desired_state: bool) -> bool:
        """
        Set the play state of the music source.
        """
        pass

    @abstractmethod
    def play_index(self, index: int) -> bool:
        """
        Play a specific track or playlist by index.
        """
        pass

    @abstractmethod
    def get_metadata(self) -> Metadata:
        """
        Retrieve metadata for the current playing track.
        """
        pass
