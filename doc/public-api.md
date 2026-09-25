# Public playback API

PiSonic exposes now-playing metadata and four playback controls over HTTP on
port 8080. The API is intended for home-automation clients and other devices on
the same trusted LAN.

> **Security warning:** the API requires no password, session cookie, or CSRF
> token. Every device that can reach PiSonic can read playback metadata and
> control playback. Use it only on a trusted LAN, and do not expose port 8080 to
> the internet or an untrusted network. Responses do not grant cross-origin
> browser access, but the absence of CORS headers is not an authentication
> mechanism and does not restrict non-browser clients.

Use either `http://<hostname>.local:8080` or `http://<device-ip>:8080` as the
base URL. All API responses are JSON with `Cache-Control: no-store`.
PiSonic's public dashboard uses this API directly for its live metadata and
playback controls, so loading the dashboard also exercises the API.

## Read player metadata

```text
GET /api/v1/player
```

No request headers or authentication are required.

```sh
curl http://pisonic.local:8080/api/v1/player
```

Example response (`200 OK`):

```json
{
  "available": true,
  "stale": false,
  "power": true,
  "active_source": "spotify",
  "playing": true,
  "metadata": {
    "name": "Artist",
    "title": "Track",
    "album": "Album",
    "artwork": {
      "id": "spotify",
      "version": "abc123",
      "url": "/dashboard/artwork?id=spotify&v=abc123"
    }
  },
  "sources": {
    "spotify": {"playing": true}
  }
}
```

Fields:

| Field | Meaning |
| --- | --- |
| `available` | `true` when the web service could read a valid player status snapshot. |
| `stale` | `true` when that snapshot has not been refreshed recently. |
| `power` | Physical power-switch state, or `null` when unavailable. |
| `active_source` | Current source ID, or `null`. Shipped IDs are `mpd`, `airplay`, `spotify`, `bluetooth`, and `usb`. |
| `playing` | Active source playback state, or `null` when unknown. |
| `metadata.name` | Current display name, such as an artist, station, or source name. |
| `metadata.title` | Current track or stream title. |
| `metadata.album` | Current album name, or `""` when the source provides none. |
| `metadata.artwork` | Safe artwork descriptor, or `{}` when no artwork is available. The relative `url` can be fetched from the same PiSonic host. |
| `sources` | Available source IDs and their last observed `playing` state. |

The endpoint keeps the same schema when the player snapshot is unavailable and
still returns `200`; unavailable Boolean/string values become `null`, `false`,
or empty values as appropriate. It never exposes local filesystem paths, stream
URLs, credentials, or backend details.

## Control playback

| Method and path | Action |
| --- | --- |
| `POST /api/v1/player/play` | Play or resume the active source. |
| `POST /api/v1/player/pause` | Pause or stop the active source. |
| `POST /api/v1/player/next` | Select the next track or preset. |
| `POST /api/v1/player/previous` | Select the previous track or preset. |

Control requests must use `Content-Type: application/json`. The actions take no
arguments; send an empty JSON object:

```sh
curl -X POST \
  -H 'Content-Type: application/json' \
  -d '{}' \
  http://pisonic.local:8080/api/v1/player/next
```

Accepted response (`200 OK`):

```json
{
  "ok": true,
  "code": "accepted",
  "message": "Playback command accepted",
  "source": "spotify"
}
```

The `source` field identifies the source that received the command. Error
responses use the same four fields with `ok` set to `false`.

## Response codes

| HTTP status | `code` | Meaning |
| --- | --- | --- |
| `200` | `accepted` | The playback backend accepted or dispatched the command. |
| `400` | `invalid_json` | The request body is not valid JSON. |
| `400` | `invalid_request` | The request body is not an empty JSON object. |
| `409` | `power_off` | The physical power switch is off; playback commands are disabled. |
| `409` | `command_failed` | The active backend rejected the command or cannot perform it now. |
| `415` | `unsupported_media_type` | The control request did not use `application/json`. |
| `413` | `request_too_large` | The request body exceeds the server limit. |
| `500` | `internal_error` | The active backend raised an unexpected error or violated its command contract. |
| `503` | `player_unavailable` | `radio.py` is starting, restarting, unavailable, or returned an invalid local response. |
| `404` | `not_found` | The requested API path does not exist. |
| `405` | `method_not_allowed` | The endpoint exists but does not support the request method. |

Example rejection:

```json
{
  "ok": false,
  "code": "power_off",
  "message": "Power switch is off",
  "source": "mpd"
}
```

An accepted response means the command was dispatched successfully; it does not
guarantee that a remote sender or host acted on it. Read the metadata endpoint
afterward if the client needs the observed playback state.

## Source-specific behavior

- Internet Radio Play resumes the selected live stream rather than a saved
  position. Previous and Next cycle configured presets and wrap at both ends.
- AirPlay remote commands depend on sender support.
- Bluetooth commands require a connected AVRCP player.
- USB Audio sends HID media keys; behavior depends on the connected host and
  its foreground media player.

Source selection, volume, physical power control, station editing, and all other
administration operations are outside this public API. The existing
administration pages remain password-protected.
