# Adding a new `MusicSource`

The radio treats every playback backend uniformly through the `MusicSource`
abstraction in [`lib/music_source.py`](../lib/music_source.py). MPD (internet
radio), AirPlay and Spotify Connect are all `MusicSource` subclasses. This guide
shows how to add another one (e.g. a local file player, an internet-radio
directory, …). The **Bluetooth A2DP** source added later is a worked example of
this exact pattern — see `lib/bluetooth_service`.

## The `MusicSource` contract

`MusicSource` is a conventional abstract base class with four annotated abstract
methods. Keep those annotations on overrides. The controller validates backend
results at its integration boundary and logs an error naming a backend that
violates the contract, without terminating the worker loop.

```python
class MusicSource(ABC):

    @abstractmethod
    def get_play_state(self) -> bool:
        """Return True while this source is actively playing."""

    @abstractmethod
    def set_play_state(self, desired_state: bool) -> bool:
        """Start (True) or stop/pause (False) playback."""

    @abstractmethod
    def play_index(self, index: int) -> bool:
        """Select/play a preset by index (used by the 1..6 buttons)."""

    @abstractmethod
    def get_metadata(self) -> Metadata:
        """Return the current Metadata for the display."""
```

### The `Metadata` model

`get_metadata()` must return a pydantic `Metadata` instance:

```python
class Metadata(BaseModel):
    name: str    # source/station/app name (top display row)
    title: str   # current track/title (bottom display row)
    cover: str   # path to a cover-art image file (or "" for none)
    md5: str     # hash of the cover; the display skips redraw when unchanged
    state: bool  # True while playing
```

Build one like `Metadata(name="", title="", cover="", md5="", state=False)` and
update it as your backend reports new track info. The `md5` lets the display
avoid re-decoding the cover art when nothing changed.

## Minimal skeleton

Create `lib/<your_service>/<your_service>.py`:

```python
import logging
from music_source import MusicSource, Metadata

logger = logging.getLogger(__name__)


class MyService(MusicSource):
    def __init__(self):
        self.name = "myservice"
        self.desired_play_state = False
        self.metadata = Metadata(name="", title="", cover="", md5="", state=False)
        # Start any background thread / external binary here.

    def get_play_state(self) -> bool:
        return self.desired_play_state

    def set_play_state(self, desired_state: bool) -> bool:
        self.desired_play_state = desired_state
        # start/stop your backend here
        return self.desired_play_state

    def play_index(self, index: int) -> bool:
        # optional: select preset `index` (1..6); return False if unsupported
        return False

    def get_metadata(self) -> Metadata:
        return self.metadata
```

Add an empty `lib/<your_service>/__init__.py` so it is importable (the rest of
the codebase imports top-level module names off the `sys.path.append('lib')`
entry set in `radio.py`).

### Long-running backends & external binaries

The shipped services follow a common pattern worth reusing:

- They run a **daemon thread** that continuously re-asserts
  `desired_play_state` and refreshes `metadata` (see `MPDService`).
- Services that wrap an external binary launch it via
  `UtilityLibrary.start_external_program_in_background(cmd)`, which restarts the
  binary if it dies and cleans it up at exit (see `SpotifyService` /
  `AirplayService`).
- Host/port/pipe paths and other constants should come from a config section in
  `radio.conf` (see how `[spotify]`, `[airplay]` and `[adc]` are injected in
  `radio.py`), keeping literals only as constructor defaults.

## Register the service in `radio.py`

1. Import and construct it in `RadioController.__init__`:

   ```python
   from myservice.myservice import MyService
   ...
   self.myservice = MyService()
   ```

2. Add it to the `self.services` list so the play-state watchdog manages it
   (it enforces the *single active service* invariant — starting one stops the
   others):

   ```python
   self.services = [
       {"name": "airplay", "service": self.airplay, "state": False},
       {"name": "mpd", "service": self.mpd, "state": False},
       {"name": "spotify", "service": self.spotify, "state": False},
       {"name": "myservice", "service": self.myservice, "state": False},
   ]
   ```

`check_play_states()` then automatically makes your service the
`active_service` (and updates the display) whenever its `get_play_state()`
flips to `True`, and stops the other services.

3. (Optional) If your source should be selectable from the hardware buttons,
   wire it into `handle_button_press` the way MPD is (`self.active_service =
   self.myservice; self.myservice.play_index(n)`).

## Reference implementations

- [`lib/mpd_service/mpd_service.py`](../lib/mpd_service/mpd_service.py) —
  subprocess (`mpc`) control + preset stations + background state thread.
- [`lib/spotify_service/spotify_service.py`](../lib/spotify_service/spotify_service.py)
  — wraps the `go-librespot` backend and reads its API for metadata.
- [`lib/airplay_service/airplay_service.py`](../lib/airplay_service/airplay_service.py)
  — wraps `shairport-sync` and parses its metadata pipe.
- [`lib/bluetooth_service/bluetooth_service.py`](../lib/bluetooth_service/bluetooth_service.py)
  — pure D-Bus *consumer* of BlueZ (`org.bluez.MediaPlayer1`): no daemon of its
  own (the `S42bluetooth` init script owns `bluealsa`/`bluealsa-aplay` and the
  auto-pairing agent; `bluetoothd` is owned by `S40bluetoothd`). Reads AVRCP
  track metadata and issues `Play`/`Pause`. Reconnect loop mirrors AirPlay.
