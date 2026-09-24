"""Identity helpers and bounded provider access for online artwork lookup."""

import hashlib
import io
import ipaddress
import json
import logging
import os
import socket
import tempfile
import threading
import time
import unicodedata
import uuid
import warnings
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import timezone
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import Any, Callable, List, Mapping, Optional, Sequence, Set, Tuple
from urllib.parse import urljoin, urlparse

import requests
from _version import __version__
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

MUSICBRAINZ_RECORDING_URL = "https://musicbrainz.org/ws/2/recording"
MUSICBRAINZ_RESULT_LIMIT = 5
MUSICBRAINZ_MAX_RESPONSE_BYTES = 256 * 1024
MUSICBRAINZ_TIMEOUT = (3.05, 8.0)
MUSICBRAINZ_REQUEST_INTERVAL = 1.0
MUSICBRAINZ_MIN_MATCH_SCORE = 95
MUSICBRAINZ_USER_AGENT = f"PiSonic/{__version__} (https://thk4711.github.io/pisonic/)"

COVER_ART_ARCHIVE_BASE_URL = "https://coverartarchive.org"
COVER_ART_MAX_RESPONSE_BYTES = 1024 * 1024
COVER_ART_MAX_REDIRECTS = 3
COVER_ART_MAX_RELEASE_REQUESTS = 2
COVER_ART_MAX_DIMENSION = 4096
COVER_ART_MAX_PIXELS = 16 * 1024 * 1024
COVER_ART_OUTPUT_MAX_DIMENSION = 320
COVER_ART_TIMEOUT = (3.05, 10.0)
COVER_ART_USER_AGENT = MUSICBRAINZ_USER_AGENT

ARTWORK_CACHE_DIRECTORY = "/tmp/pisonic/artwork-cache"
ARTWORK_PUBLICATION_PATH = "/tmp/bluetooth_cover.jpg"
ARTWORK_MAX_POSITIVE_FILES = 32
ARTWORK_MAX_CACHE_BYTES = 8 * 1024 * 1024
ARTWORK_MAX_NEGATIVE_ENTRIES = 128
ARTWORK_NEGATIVE_TTL = 6 * 60 * 60
ARTWORK_TRANSIENT_RETRY = 5 * 60
ARTWORK_RATE_LIMIT_RETRY = 15 * 60
ARTWORK_MAX_RETRY_AFTER = 24 * 60 * 60

_LUCENE_SPECIAL_CHARACTERS = frozenset('+-&|!(){}[]^"~*?:\\/')
_COVER_ART_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_COVER_ART_MEDIA_SIGNATURES = {
    "image/jpeg": b"\xff\xd8\xff",
    "image/png": b"\x89PNG\r\n\x1a\n",
}


def _clean_query_text(value: str) -> str:
    """Trim and collapse whitespace without changing text sent to a provider."""
    return " ".join(value.split())


def normalize_for_comparison(value: str) -> str:
    """Return a Unicode-aware, case-insensitive, punctuation-neutral value."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    without_punctuation = "".join(
        " " if unicodedata.category(character).startswith("P") else character
        for character in normalized
    )
    return " ".join(without_punctuation.split())


@dataclass(frozen=True)
class TrackIdentity:
    """Provider query text and its stable, privacy-safe comparison identity."""

    artist: str
    title: str
    album: Optional[str] = None
    normalized_artist: str = field(init=False)
    normalized_title: str = field(init=False)
    normalized_album: str = field(init=False)
    cache_key: str = field(init=False)

    def __post_init__(self) -> None:
        artist = _clean_query_text(self.artist)
        title = _clean_query_text(self.title)
        album = _clean_query_text(self.album) if self.album else None
        normalized_artist = normalize_for_comparison(artist)
        normalized_title = normalize_for_comparison(title)
        normalized_album = normalize_for_comparison(album or "")

        if not normalized_artist or not normalized_title:
            raise ValueError("track identity requires both artist and title")

        key_source = json.dumps(
            [normalized_artist, normalized_title, normalized_album],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        object.__setattr__(self, "artist", artist)
        object.__setattr__(self, "title", title)
        object.__setattr__(self, "album", album)
        object.__setattr__(self, "normalized_artist", normalized_artist)
        object.__setattr__(self, "normalized_title", normalized_title)
        object.__setattr__(self, "normalized_album", normalized_album)
        object.__setattr__(
            self,
            "cache_key",
            hashlib.sha256(key_source.encode("utf-8")).hexdigest(),
        )

    @classmethod
    def from_metadata(
        cls, artist: str, title: str, album: Optional[str] = None
    ) -> Optional["TrackIdentity"]:
        """Build an identity, or return ``None`` for incomplete metadata."""
        try:
            return cls(artist=artist, title=title, album=album)
        except ValueError:
            return None


def escape_lucene_query(value: str) -> str:
    """Escape characters with special meaning in a Lucene query value."""
    return "".join(
        f"\\{character}" if character in _LUCENE_SPECIAL_CHARACTERS else character
        for character in value
    )


class MusicBrainzFailure(Enum):
    """Controlled MusicBrainz failure categories used by retry policy later."""

    TIMEOUT = "timeout"
    CONNECTION = "connection"
    TLS = "tls"
    RATE_LIMITED = "rate_limited"
    SERVER = "server"
    HTTP = "http"
    REDIRECT = "redirect"
    RESPONSE_TOO_LARGE = "response_too_large"
    INVALID_RESPONSE = "invalid_response"


@dataclass(frozen=True)
class MusicBrainzSearchResult:
    """A bounded recording search result or a classified, non-raising failure."""

    recordings: Tuple[Mapping[str, Any], ...] = ()
    failure: Optional[MusicBrainzFailure] = None
    status_code: Optional[int] = None
    retry_after: Optional[float] = None

    @property
    def succeeded(self) -> bool:
        return self.failure is None


@dataclass(frozen=True)
class MusicBrainzMatch:
    """A confidently matched recording and its ranked artwork identifiers."""

    recording_id: str
    score: int
    release_group_id: Optional[str]
    release_ids: Tuple[str, ...]


@dataclass(frozen=True)
class _ReleaseCandidate:
    release_id: Optional[str]
    release_group_id: Optional[str]
    album_match: bool
    official: bool
    preferred_type: bool
    non_compilation: bool

    @property
    def rank(self) -> Tuple[bool, bool, bool, bool, bool]:
        return (
            self.album_match,
            self.official,
            self.preferred_type,
            self.non_compilation,
            self.release_group_id is not None,
        )


@dataclass(frozen=True)
class _RecordingCandidate:
    match: MusicBrainzMatch
    rank: Tuple[bool, bool, bool, bool, bool]


def _parse_mbid(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        return None


def _artist_credit_text(value: Any) -> Optional[str]:
    if not isinstance(value, list) or not value:
        return None

    parts: List[str] = []
    for credit in value:
        if not isinstance(credit, dict):
            return None
        name = credit.get("name")
        if not isinstance(name, str) or not name.strip():
            artist = credit.get("artist")
            name = artist.get("name") if isinstance(artist, dict) else None
        if not isinstance(name, str) or not name.strip():
            return None
        parts.append(name)
        join_phrase = credit.get("joinphrase", "")
        if not isinstance(join_phrase, str):
            return None
        parts.append(join_phrase)
    return "".join(parts)


def _release_candidate(
    release: Any, normalized_album: str
) -> Optional[Tuple[_ReleaseCandidate, Tuple[str, ...]]]:
    if not isinstance(release, dict):
        return None

    release_id = _parse_mbid(release.get("id"))
    release_group = release.get("release-group")
    if not isinstance(release_group, dict):
        release_group = {}
    release_group_id = _parse_mbid(release_group.get("id"))
    if release_id is None and release_group_id is None:
        return None

    album_names = []
    for value in (release.get("title"), release_group.get("title")):
        if isinstance(value, str) and normalize_for_comparison(value):
            album_names.append(normalize_for_comparison(value))
    album_match = bool(normalized_album and normalized_album in album_names)

    status = release.get("status")
    official = isinstance(status, str) and normalize_for_comparison(status) == "official"
    primary_type = release_group.get("primary-type")
    normalized_primary_type = (
        normalize_for_comparison(primary_type) if isinstance(primary_type, str) else ""
    )
    secondary_types = release_group.get("secondary-types", [])
    if not isinstance(secondary_types, list):
        secondary_types = []
    normalized_secondary_types = {
        normalize_for_comparison(value) for value in secondary_types if isinstance(value, str)
    }
    non_compilation = "compilation" not in normalized_secondary_types
    preferred_type = normalized_primary_type in {"album", "single", "ep"} and non_compilation
    return (
        _ReleaseCandidate(
            release_id=release_id,
            release_group_id=release_group_id,
            album_match=album_match,
            official=official,
            preferred_type=preferred_type,
            non_compilation=non_compilation,
        ),
        tuple(album_names),
    )


def _recording_candidate(
    track: TrackIdentity, recording: Mapping[str, Any]
) -> Optional[_RecordingCandidate]:
    score = recording.get("score")
    if isinstance(score, bool) or not isinstance(score, int):
        return None
    if score < MUSICBRAINZ_MIN_MATCH_SCORE:
        return None

    recording_id = _parse_mbid(recording.get("id"))
    title = recording.get("title")
    artist_credit = _artist_credit_text(recording.get("artist-credit"))
    if recording_id is None or not isinstance(title, str) or artist_credit is None:
        return None
    if normalize_for_comparison(title) != track.normalized_title:
        return None
    if normalize_for_comparison(artist_credit) != track.normalized_artist:
        return None

    releases = recording.get("releases")
    if not isinstance(releases, list):
        return None
    parsed_releases = []
    known_album_names: Set[str] = set()
    for release in releases:
        parsed = _release_candidate(release, track.normalized_album)
        if parsed is not None:
            candidate, album_names = parsed
            parsed_releases.append(candidate)
            known_album_names.update(album_names)
    if not parsed_releases:
        return None

    if (
        track.normalized_album
        and known_album_names
        and not any(release.album_match for release in parsed_releases)
    ):
        return None

    ranked_releases = sorted(
        parsed_releases,
        key=lambda release: (
            release.rank,
            release.release_group_id or "",
            release.release_id or "",
        ),
        reverse=True,
    )
    best_release = ranked_releases[0]
    release_ids = tuple(
        dict.fromkeys(
            release.release_id for release in ranked_releases if release.release_id is not None
        )
    )
    match = MusicBrainzMatch(
        recording_id=recording_id,
        score=score,
        release_group_id=best_release.release_group_id,
        release_ids=release_ids,
    )
    return _RecordingCandidate(match=match, rank=best_release.rank)


def select_musicbrainz_match(
    track: TrackIdentity, recordings: Sequence[Mapping[str, Any]]
) -> Optional[MusicBrainzMatch]:
    """Select one high-confidence result, rejecting ties and malformed data."""
    candidates = []
    for recording in recordings:
        candidate = _recording_candidate(track, recording)
        if candidate is not None:
            candidates.append(candidate)
    if not candidates:
        return None

    candidates.sort(key=lambda candidate: candidate.rank, reverse=True)
    if len(candidates) > 1 and candidates[0].rank == candidates[1].rank:
        return None
    return candidates[0].match


class MusicBrainzClient:
    """Search MusicBrainz with strict request pacing and response bounds."""

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._session = session if session is not None else requests.Session()
        self._owns_session = session is None
        self._clock = clock
        self._sleeper = sleeper
        self._request_lock = threading.Lock()
        self._last_request_started: Optional[float] = None
        self._session.headers.update(
            {
                "User-Agent": MUSICBRAINZ_USER_AGENT,
                "Accept": "application/json",
            }
        )

    def close(self) -> None:
        """Close the dedicated HTTP session created by this client."""
        if self._owns_session:
            self._session.close()

    def search(self, track: TrackIdentity) -> MusicBrainzSearchResult:
        """Return up to five recording mappings without leaking request errors."""
        query = (
            f'recording:"{escape_lucene_query(track.title)}" AND '
            f'artist:"{escape_lucene_query(track.artist)}"'
        )
        params = {
            "query": query,
            "fmt": "json",
            "limit": MUSICBRAINZ_RESULT_LIMIT,
        }

        response: Optional[requests.Response] = None
        try:
            response = self._get(params)
            status_code = response.status_code
            if 300 <= status_code < 400:
                return MusicBrainzSearchResult(
                    failure=MusicBrainzFailure.REDIRECT, status_code=status_code
                )
            if status_code == 429:
                return MusicBrainzSearchResult(
                    failure=MusicBrainzFailure.RATE_LIMITED,
                    status_code=status_code,
                    retry_after=_retry_after_seconds(response.headers.get("Retry-After")),
                )
            if 500 <= status_code < 600:
                return MusicBrainzSearchResult(
                    failure=MusicBrainzFailure.SERVER,
                    status_code=status_code,
                    retry_after=_retry_after_seconds(response.headers.get("Retry-After")),
                )
            if status_code != 200:
                return MusicBrainzSearchResult(
                    failure=MusicBrainzFailure.HTTP, status_code=status_code
                )

            payload = self._read_bounded_body(response)
            if payload is None:
                return MusicBrainzSearchResult(
                    failure=MusicBrainzFailure.RESPONSE_TOO_LARGE,
                    status_code=status_code,
                )
            return self._decode_result(payload, status_code)
        except requests.exceptions.SSLError:
            return MusicBrainzSearchResult(failure=MusicBrainzFailure.TLS)
        except requests.exceptions.Timeout:
            return MusicBrainzSearchResult(failure=MusicBrainzFailure.TIMEOUT)
        except requests.exceptions.ConnectionError:
            return MusicBrainzSearchResult(failure=MusicBrainzFailure.CONNECTION)
        except requests.exceptions.RequestException:
            return MusicBrainzSearchResult(failure=MusicBrainzFailure.CONNECTION)
        finally:
            if response is not None:
                response.close()

    def _get(self, params: Mapping[str, Any]) -> requests.Response:
        """Start a request no sooner than one second after the previous one."""
        with self._request_lock:
            now = self._clock()
            if self._last_request_started is not None:
                delay = MUSICBRAINZ_REQUEST_INTERVAL - (now - self._last_request_started)
                if delay > 0:
                    self._sleeper(delay)
            self._last_request_started = self._clock()
            return self._session.get(
                MUSICBRAINZ_RECORDING_URL,
                params=params,
                timeout=MUSICBRAINZ_TIMEOUT,
                stream=True,
                allow_redirects=False,
            )

    @staticmethod
    def _read_bounded_body(response: requests.Response) -> Optional[bytes]:
        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except (TypeError, ValueError):
                return None
            if declared_length < 0 or declared_length > MUSICBRAINZ_MAX_RESPONSE_BYTES:
                return None

        payload = bytearray()
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            if len(payload) + len(chunk) > MUSICBRAINZ_MAX_RESPONSE_BYTES:
                return None
            payload.extend(chunk)
        return bytes(payload)

    @staticmethod
    def _decode_result(payload: bytes, status_code: int) -> MusicBrainzSearchResult:
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return MusicBrainzSearchResult(
                failure=MusicBrainzFailure.INVALID_RESPONSE,
                status_code=status_code,
            )

        if not isinstance(document, dict):
            return MusicBrainzSearchResult(
                failure=MusicBrainzFailure.INVALID_RESPONSE,
                status_code=status_code,
            )
        recordings = document.get("recordings")
        if not isinstance(recordings, list) or not all(
            isinstance(recording, dict) for recording in recordings
        ):
            return MusicBrainzSearchResult(
                failure=MusicBrainzFailure.INVALID_RESPONSE,
                status_code=status_code,
            )
        return MusicBrainzSearchResult(
            recordings=tuple(recordings[:MUSICBRAINZ_RESULT_LIMIT]),
            status_code=status_code,
        )


class CoverArtFailure(Enum):
    """Controlled Cover Art Archive failures used by later retry policy."""

    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    TLS = "tls"
    RATE_LIMITED = "rate_limited"
    SERVER = "server"
    HTTP = "http"
    REDIRECT = "redirect"
    UNSAFE_REDIRECT = "unsafe_redirect"
    RESPONSE_TOO_LARGE = "response_too_large"
    INVALID_MEDIA_TYPE = "invalid_media_type"
    INVALID_IMAGE = "invalid_image"
    INVALID_IDENTIFIER = "invalid_identifier"
    FILE_IO = "file_io"


@dataclass(frozen=True)
class CoverArtResult:
    """A normalized published image or a classified, non-raising failure."""

    fingerprint: Optional[str] = None
    failure: Optional[CoverArtFailure] = None
    status_code: Optional[int] = None
    retry_after: Optional[float] = None

    @property
    def succeeded(self) -> bool:
        return self.failure is None and self.fingerprint is not None


@dataclass(frozen=True)
class _DownloadedCover:
    data: Optional[bytes] = None
    failure: Optional[CoverArtFailure] = None
    status_code: Optional[int] = None
    retry_after: Optional[float] = None


def _retry_after_seconds(value: Optional[str]) -> Optional[float]:
    """Parse a bounded delta-seconds or HTTP-date Retry-After value."""
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except (TypeError, ValueError):
        try:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=timezone.utc)
            seconds = retry_at.timestamp() - time.time()
        except (TypeError, ValueError, OverflowError):
            return None
    if seconds < 0:
        seconds = 0
    return min(seconds, ARTWORK_MAX_RETRY_AFTER)


def _resolve_addresses(hostname: str) -> Sequence[str]:
    addresses: List[str] = []
    for result in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM):
        address = result[4][0]
        if isinstance(address, str) and address not in addresses:
            addresses.append(address)
    return addresses


class CoverArtArchiveClient:
    """Fetch, validate, normalize, and atomically publish CAA cover images."""

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        address_resolver: Callable[[str], Sequence[str]] = _resolve_addresses,
    ) -> None:
        self._session = session if session is not None else requests.Session()
        self._owns_session = session is None
        self._address_resolver = address_resolver
        self._session.headers.update(
            {
                "User-Agent": COVER_ART_USER_AGENT,
                "Accept": "image/jpeg, image/png",
            }
        )

    def close(self) -> None:
        """Close the dedicated HTTP session created by this client."""
        if self._owns_session:
            self._session.close()

    def fetch(self, match: MusicBrainzMatch, destination: str) -> CoverArtResult:
        """Try one release group and two releases, publishing only a valid JPEG."""
        urls = self._candidate_urls(match)
        if not urls:
            return CoverArtResult(failure=CoverArtFailure.INVALID_IDENTIFIER)

        last_not_found = CoverArtResult(failure=CoverArtFailure.NOT_FOUND)
        for url in urls:
            downloaded = self._download(url)
            if downloaded.data is not None:
                fingerprint = hashlib.sha256(downloaded.data).hexdigest()
                if not self._publish(destination, downloaded.data):
                    return CoverArtResult(failure=CoverArtFailure.FILE_IO)
                return CoverArtResult(
                    fingerprint=fingerprint,
                    status_code=downloaded.status_code,
                )
            result = CoverArtResult(
                failure=downloaded.failure,
                status_code=downloaded.status_code,
                retry_after=downloaded.retry_after,
            )
            if downloaded.failure is not CoverArtFailure.NOT_FOUND:
                return result
            last_not_found = result
        return last_not_found

    @staticmethod
    def _candidate_urls(match: MusicBrainzMatch) -> Tuple[str, ...]:
        urls = []
        release_group_id = _parse_mbid(match.release_group_id)
        if release_group_id is not None:
            urls.append(f"{COVER_ART_ARCHIVE_BASE_URL}/release-group/{release_group_id}/front-250")

        valid_release_ids = []
        for value in match.release_ids:
            release_id = _parse_mbid(value)
            if release_id is not None and release_id not in valid_release_ids:
                valid_release_ids.append(release_id)
        urls.extend(
            f"{COVER_ART_ARCHIVE_BASE_URL}/release/{release_id}/front-250"
            for release_id in valid_release_ids[:COVER_ART_MAX_RELEASE_REQUESTS]
        )
        return tuple(urls)

    def _download(self, entry_url: str) -> _DownloadedCover:
        current_url = entry_url
        for redirect_count in range(COVER_ART_MAX_REDIRECTS + 1):
            response: Optional[requests.Response] = None
            try:
                response = self._session.get(
                    current_url,
                    timeout=COVER_ART_TIMEOUT,
                    stream=True,
                    allow_redirects=False,
                )
                status_code = response.status_code
                if status_code in _COVER_ART_REDIRECT_STATUSES:
                    if redirect_count >= COVER_ART_MAX_REDIRECTS:
                        return _DownloadedCover(
                            failure=CoverArtFailure.REDIRECT,
                            status_code=status_code,
                        )
                    location = response.headers.get("Location")
                    if not location:
                        return _DownloadedCover(
                            failure=CoverArtFailure.REDIRECT,
                            status_code=status_code,
                        )
                    redirect_url = urljoin(current_url, location)
                    if not self._safe_redirect(redirect_url):
                        return _DownloadedCover(
                            failure=CoverArtFailure.UNSAFE_REDIRECT,
                            status_code=status_code,
                        )
                    current_url = redirect_url
                    continue
                if 300 <= status_code < 400:
                    return _DownloadedCover(
                        failure=CoverArtFailure.REDIRECT,
                        status_code=status_code,
                    )
                if status_code == 404:
                    return _DownloadedCover(
                        failure=CoverArtFailure.NOT_FOUND,
                        status_code=status_code,
                    )
                if status_code == 429:
                    return _DownloadedCover(
                        failure=CoverArtFailure.RATE_LIMITED,
                        status_code=status_code,
                        retry_after=_retry_after_seconds(response.headers.get("Retry-After")),
                    )
                if 500 <= status_code < 600:
                    return _DownloadedCover(
                        failure=CoverArtFailure.SERVER,
                        status_code=status_code,
                        retry_after=_retry_after_seconds(response.headers.get("Retry-After")),
                    )
                if status_code != 200:
                    return _DownloadedCover(
                        failure=CoverArtFailure.HTTP,
                        status_code=status_code,
                    )

                body_failure, body = self._read_image_body(response)
                if body_failure is not None:
                    return _DownloadedCover(failure=body_failure, status_code=status_code)
                normalized = self._normalize_image(body)
                if normalized is None:
                    return _DownloadedCover(
                        failure=CoverArtFailure.INVALID_IMAGE,
                        status_code=status_code,
                    )
                return _DownloadedCover(data=normalized, status_code=status_code)
            except requests.exceptions.SSLError:
                return _DownloadedCover(failure=CoverArtFailure.TLS)
            except requests.exceptions.Timeout:
                return _DownloadedCover(failure=CoverArtFailure.TIMEOUT)
            except requests.exceptions.ConnectionError:
                return _DownloadedCover(failure=CoverArtFailure.CONNECTION)
            except requests.exceptions.RequestException:
                return _DownloadedCover(failure=CoverArtFailure.CONNECTION)
            finally:
                if response is not None:
                    response.close()
        return _DownloadedCover(failure=CoverArtFailure.REDIRECT)

    def _safe_redirect(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            port = parsed.port
        except ValueError:
            return False
        hostname = parsed.hostname
        if (
            parsed.scheme != "https"
            or hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
            or not self._expected_host(hostname)
        ):
            return False

        try:
            addresses = self._address_resolver(hostname)
        except (OSError, ValueError):
            return False
        if not addresses:
            return False
        try:
            return all(self._public_address(address) for address in addresses)
        except ValueError:
            return False

    @staticmethod
    def _expected_host(hostname: str) -> bool:
        hostname = hostname.rstrip(".").casefold()
        return (
            hostname == "coverartarchive.org"
            or hostname == "archive.org"
            or hostname.endswith(".archive.org")
        )

    @staticmethod
    def _public_address(address: str) -> bool:
        parsed = ipaddress.ip_address(address)
        return not (
            parsed.is_private
            or parsed.is_loopback
            or parsed.is_link_local
            or parsed.is_multicast
            or parsed.is_reserved
            or parsed.is_unspecified
        )

    @staticmethod
    def _read_image_body(
        response: requests.Response,
    ) -> Tuple[Optional[CoverArtFailure], bytes]:
        content_type = response.headers.get("Content-Type", "").partition(";")[0].strip().lower()
        signature = _COVER_ART_MEDIA_SIGNATURES.get(content_type)
        if signature is None:
            return CoverArtFailure.INVALID_MEDIA_TYPE, b""

        content_length = response.headers.get("Content-Length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except (TypeError, ValueError):
                return CoverArtFailure.RESPONSE_TOO_LARGE, b""
            if declared_length < 0 or declared_length > COVER_ART_MAX_RESPONSE_BYTES:
                return CoverArtFailure.RESPONSE_TOO_LARGE, b""

        payload = bytearray()
        for chunk in response.iter_content(chunk_size=8192):
            if not chunk:
                continue
            if len(payload) + len(chunk) > COVER_ART_MAX_RESPONSE_BYTES:
                return CoverArtFailure.RESPONSE_TOO_LARGE, b""
            payload.extend(chunk)
        body = bytes(payload)
        if not body.startswith(signature):
            return CoverArtFailure.INVALID_MEDIA_TYPE, b""
        return None, body

    @staticmethod
    def _normalize_image(data: bytes) -> Optional[bytes]:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as source:
                    width, height = source.size
                    if (
                        width <= 0
                        or height <= 0
                        or width > COVER_ART_MAX_DIMENSION
                        or height > COVER_ART_MAX_DIMENSION
                        or width * height > COVER_ART_MAX_PIXELS
                    ):
                        return None
                    source.load()
                    image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail(
                (COVER_ART_OUTPUT_MAX_DIMENSION, COVER_ART_OUTPUT_MAX_DIMENSION),
                Image.Resampling.LANCZOS,
            )
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=88, optimize=True)
            return output.getvalue()
        except (Image.DecompressionBombError, Image.DecompressionBombWarning):
            return None
        except (OSError, UnidentifiedImageError, ValueError):
            return None

    @staticmethod
    def _publish(destination: str, data: bytes) -> bool:
        directory = os.path.dirname(destination) or "."
        temporary: Optional[str] = None
        try:
            fd, temporary = tempfile.mkstemp(dir=directory, prefix="artwork-")
            with os.fdopen(fd, "wb") as file:
                file.write(data)
            os.chmod(temporary, 0o644)
            os.replace(temporary, destination)
            return True
        except OSError:
            return False
        finally:
            if temporary is not None and os.path.exists(temporary):
                os.unlink(temporary)


@dataclass(frozen=True)
class ArtworkResolution:
    """A current-track image published at the fixed integration path."""

    track_key: str
    cover_path: str
    fingerprint: str


@dataclass(frozen=True)
class _ArtworkRequest:
    track: TrackIdentity
    callback: Callable[[ArtworkResolution], None]
    generation: int
    not_before: float


@dataclass(frozen=True)
class _PositiveCacheEntry:
    path: str
    fingerprint: str
    size: int


@dataclass(frozen=True)
class _NegativeCacheEntry:
    retry_at: float
    definitive: bool


class _ApplyResult(Enum):
    APPLIED = "applied"
    STALE = "stale"
    INVALID_CACHE = "invalid_cache"
    PUBLICATION_FAILED = "publication_failed"


class OnlineArtworkResolver:
    """Resolve artwork on one daemon worker with a latest-request-wins slot."""

    def __init__(
        self,
        musicbrainz: Optional[MusicBrainzClient] = None,
        cover_art: Optional[CoverArtArchiveClient] = None,
        cache_directory: str = ARTWORK_CACHE_DIRECTORY,
        publication_path: str = ARTWORK_PUBLICATION_PATH,
        clock: Callable[[], float] = time.monotonic,
        negative_ttl: float = ARTWORK_NEGATIVE_TTL,
        transient_retry: float = ARTWORK_TRANSIENT_RETRY,
        rate_limit_retry: float = ARTWORK_RATE_LIMIT_RETRY,
        max_negative_entries: int = ARTWORK_MAX_NEGATIVE_ENTRIES,
        max_positive_files: int = ARTWORK_MAX_POSITIVE_FILES,
        max_cache_bytes: int = ARTWORK_MAX_CACHE_BYTES,
    ) -> None:
        if (
            min(
                negative_ttl,
                transient_retry,
                rate_limit_retry,
                max_negative_entries,
                max_positive_files,
                max_cache_bytes,
            )
            <= 0
        ):
            raise ValueError("artwork resolver limits must be positive")

        self._musicbrainz = musicbrainz if musicbrainz is not None else MusicBrainzClient()
        self._cover_art = cover_art if cover_art is not None else CoverArtArchiveClient()
        self._cache_directory = cache_directory
        self._publication_path = publication_path
        self._clock = clock
        self._negative_ttl = negative_ttl
        self._transient_retry = transient_retry
        self._rate_limit_retry = rate_limit_retry
        self._max_negative_entries = max_negative_entries
        self._max_positive_files = max_positive_files
        self._max_cache_bytes = max_cache_bytes

        self._condition = threading.Condition(threading.RLock())
        self._stop = False
        self._generation = 0
        self._current_key: Optional[str] = None
        self._pending: Optional[_ArtworkRequest] = None
        self._positive: "OrderedDict[str, _PositiveCacheEntry]" = OrderedDict()
        self._negative: "OrderedDict[str, _NegativeCacheEntry]" = OrderedDict()
        self._prepare_cache()
        self.worker_thread = threading.Thread(
            target=self._worker,
            daemon=True,
            name="online-artwork",
        )
        self.worker_thread.start()

    def request(
        self,
        track: TrackIdentity,
        callback: Callable[[ArtworkResolution], None],
    ) -> bool:
        """Queue a track without blocking; replace any older pending track."""
        with self._condition:
            if self._stop or self._current_key == track.cache_key:
                return False
            self._generation += 1
            self._current_key = track.cache_key
            self._pending = _ArtworkRequest(
                track=track,
                callback=callback,
                generation=self._generation,
                not_before=self._clock(),
            )
            self._condition.notify()
            return True

    def cancel(self) -> None:
        """Invalidate active work and discard the pending request."""
        with self._condition:
            self._generation += 1
            self._current_key = None
            self._pending = None
            self._condition.notify()

    def close(self) -> None:
        """Interrupt pending waits, stop the worker, and close provider clients."""
        with self._condition:
            if self._stop:
                return
            self._stop = True
            self._pending = None
            self._condition.notify()
        if threading.current_thread() is not self.worker_thread:
            self.worker_thread.join()
        self._musicbrainz.close()
        self._cover_art.close()

    def _worker(self) -> None:
        while True:
            request = self._next_request()
            if request is None:
                return
            try:
                self._process(request)
            except Exception as exc:
                logger.error(
                    "Online artwork resolver contained unexpected %s",
                    type(exc).__name__,
                )
                self._defer(request, self._transient_retry, definitive=False)

    def _next_request(self) -> Optional[_ArtworkRequest]:
        with self._condition:
            while not self._stop:
                request = self._pending
                if request is None:
                    self._condition.wait()
                    continue
                delay = request.not_before - self._clock()
                if delay > 0:
                    self._condition.wait(delay)
                    continue
                self._pending = None
                return request
            return None

    def _process(self, request: _ArtworkRequest) -> None:
        key = request.track.cache_key
        positive = self._positive.get(key)
        if positive is not None:
            self._positive.move_to_end(key)
            apply_result = self._apply(request, positive)
            if apply_result in {_ApplyResult.APPLIED, _ApplyResult.STALE}:
                return
            if apply_result is _ApplyResult.PUBLICATION_FAILED:
                self._defer(request, self._transient_retry, definitive=False)
                return
            self._drop_positive(key)

        negative = self._negative.get(key)
        if negative is not None:
            if negative.retry_at > self._clock():
                self._reschedule(request, negative.retry_at)
                return
            self._negative.pop(key, None)

        search = self._musicbrainz.search(request.track)
        if not search.succeeded:
            self._defer(
                request,
                self._retry_delay(search.retry_after, search.status_code),
                definitive=False,
            )
            return

        match = select_musicbrainz_match(request.track, search.recordings)
        if match is None:
            self._defer(request, self._negative_ttl, definitive=True)
            return
        if not self._is_current(request):
            return

        cache_path = os.path.join(self._cache_directory, f"{key}.jpg")
        result = self._cover_art.fetch(match, cache_path)
        if not result.succeeded:
            if result.failure is CoverArtFailure.NOT_FOUND:
                self._defer(request, self._negative_ttl, definitive=True)
            else:
                self._defer(
                    request,
                    self._retry_delay(result.retry_after, result.status_code),
                    definitive=False,
                )
            return

        entry = self._store_positive(key, cache_path, result.fingerprint)
        if entry is None:
            self._defer(request, self._transient_retry, definitive=False)
            return
        apply_result = self._apply(request, entry)
        if apply_result is _ApplyResult.INVALID_CACHE:
            self._drop_positive(key)
        if apply_result in {_ApplyResult.INVALID_CACHE, _ApplyResult.PUBLICATION_FAILED}:
            self._defer(request, self._transient_retry, definitive=False)

    def _retry_delay(self, retry_after: Optional[float], status_code: Optional[int]) -> float:
        if retry_after is not None:
            return max(1.0, min(retry_after, ARTWORK_MAX_RETRY_AFTER))
        if status_code in {429, 503}:
            return self._rate_limit_retry
        return self._transient_retry

    def _defer(self, request: _ArtworkRequest, delay: float, definitive: bool) -> None:
        retry_at = self._clock() + delay
        key = request.track.cache_key
        with self._condition:
            self._negative[key] = _NegativeCacheEntry(
                retry_at=retry_at,
                definitive=definitive,
            )
            self._negative.move_to_end(key)
            while len(self._negative) > self._max_negative_entries:
                self._negative.popitem(last=False)
            if self._matches_current(request):
                self._pending = _ArtworkRequest(
                    track=request.track,
                    callback=request.callback,
                    generation=request.generation,
                    not_before=retry_at,
                )
                self._condition.notify()

    def _reschedule(self, request: _ArtworkRequest, retry_at: float) -> None:
        with self._condition:
            if self._matches_current(request):
                self._pending = _ArtworkRequest(
                    track=request.track,
                    callback=request.callback,
                    generation=request.generation,
                    not_before=retry_at,
                )
                self._condition.notify()

    def _apply(self, request: _ArtworkRequest, entry: _PositiveCacheEntry) -> _ApplyResult:
        if not self._is_current(request):
            return _ApplyResult.STALE
        data = self._read_cache_file(entry.path, entry.size)
        if data is None or hashlib.sha256(data).hexdigest() != entry.fingerprint:
            return _ApplyResult.INVALID_CACHE
        if not CoverArtArchiveClient._publish(self._publication_path, data):
            return _ApplyResult.PUBLICATION_FAILED

        resolution = ArtworkResolution(
            track_key=request.track.cache_key,
            cover_path=self._publication_path,
            fingerprint=entry.fingerprint,
        )
        with self._condition:
            if not self._matches_current(request):
                return _ApplyResult.STALE
            self._negative.pop(request.track.cache_key, None)
            try:
                request.callback(resolution)
            except Exception as exc:
                logger.error(
                    "Online artwork callback contained unexpected %s",
                    type(exc).__name__,
                )
            return _ApplyResult.APPLIED

    def _is_current(self, request: _ArtworkRequest) -> bool:
        with self._condition:
            return self._matches_current(request)

    def _matches_current(self, request: _ArtworkRequest) -> bool:
        return (
            not self._stop
            and self._generation == request.generation
            and self._current_key == request.track.cache_key
        )

    def _prepare_cache(self) -> None:
        try:
            os.makedirs(self._cache_directory, mode=0o755, exist_ok=True)
        except OSError:
            return

        candidates = []
        try:
            entries = list(os.scandir(self._cache_directory))
        except OSError:
            return
        for directory_entry in entries:
            name = directory_entry.name
            key, separator, extension = name.partition(".")
            if (
                not directory_entry.is_file(follow_symlinks=False)
                or separator != "."
                or extension != "jpg"
                or len(key) != 64
                or any(character not in "0123456789abcdef" for character in key)
            ):
                if directory_entry.is_file(follow_symlinks=False) or directory_entry.is_symlink():
                    self._remove_file(directory_entry.path)
                continue
            try:
                file_stat = directory_entry.stat(follow_symlinks=False)
            except OSError:
                continue
            candidates.append((file_stat.st_mtime, key, directory_entry.path, file_stat.st_size))

        for _mtime, key, path, size in sorted(candidates):
            fingerprint = self._fingerprint_cache_file(path, size)
            if fingerprint is None:
                self._remove_file(path)
                continue
            self._positive[key] = _PositiveCacheEntry(path, fingerprint, size)
        self._enforce_positive_bounds()

    def _store_positive(
        self,
        key: str,
        path: str,
        fingerprint: Optional[str],
    ) -> Optional[_PositiveCacheEntry]:
        if fingerprint is None:
            return None
        try:
            size = os.path.getsize(path)
        except OSError:
            return None
        if size <= 0 or size > self._max_cache_bytes:
            self._remove_file(path)
            return None

        entry = _PositiveCacheEntry(path=path, fingerprint=fingerprint, size=size)
        self._positive[key] = entry
        self._positive.move_to_end(key)
        self._negative.pop(key, None)
        self._enforce_positive_bounds()
        return self._positive.get(key)

    def _enforce_positive_bounds(self) -> None:
        total_size = sum(entry.size for entry in self._positive.values())
        while len(self._positive) > self._max_positive_files or total_size > self._max_cache_bytes:
            _key, entry = self._positive.popitem(last=False)
            total_size -= entry.size
            self._remove_file(entry.path)

    def _drop_positive(self, key: str) -> None:
        entry = self._positive.pop(key, None)
        if entry is not None:
            self._remove_file(entry.path)

    def _fingerprint_cache_file(self, path: str, size: int) -> Optional[str]:
        data = self._read_cache_file(path, size)
        if data is None:
            return None
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as image:
                    if (
                        image.format != "JPEG"
                        or image.mode != "RGB"
                        or image.width <= 0
                        or image.height <= 0
                        or image.width > COVER_ART_OUTPUT_MAX_DIMENSION
                        or image.height > COVER_ART_OUTPUT_MAX_DIMENSION
                    ):
                        return None
                    image.load()
        except (
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
            OSError,
            UnidentifiedImageError,
            ValueError,
        ):
            return None
        return hashlib.sha256(data).hexdigest()

    def _read_cache_file(self, path: str, expected_size: int) -> Optional[bytes]:
        if expected_size <= 0 or expected_size > self._max_cache_bytes:
            return None
        try:
            with open(path, "rb") as cache_file:
                data = cache_file.read(self._max_cache_bytes + 1)
        except OSError:
            return None
        if len(data) != expected_size or len(data) > self._max_cache_bytes:
            return None
        if not data.startswith(_COVER_ART_MEDIA_SIGNATURES["image/jpeg"]):
            return None
        return data

    @staticmethod
    def _remove_file(path: str) -> None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except OSError:
            pass
