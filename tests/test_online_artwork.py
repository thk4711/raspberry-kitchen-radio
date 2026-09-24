import hashlib
import io
import json
import re
import shutil
import stat
import threading
import time
from copy import deepcopy
from pathlib import Path

import online_artwork
import pytest
import requests
from online_artwork import (
    COVER_ART_ARCHIVE_BASE_URL,
    COVER_ART_MAX_REDIRECTS,
    COVER_ART_MAX_RESPONSE_BYTES,
    COVER_ART_OUTPUT_MAX_DIMENSION,
    COVER_ART_TIMEOUT,
    COVER_ART_USER_AGENT,
    MUSICBRAINZ_MAX_RESPONSE_BYTES,
    MUSICBRAINZ_MIN_MATCH_SCORE,
    MUSICBRAINZ_RECORDING_URL,
    MUSICBRAINZ_TIMEOUT,
    MUSICBRAINZ_USER_AGENT,
    CoverArtArchiveClient,
    CoverArtFailure,
    CoverArtResult,
    MusicBrainzClient,
    MusicBrainzFailure,
    MusicBrainzMatch,
    MusicBrainzSearchResult,
    OnlineArtworkResolver,
    TrackIdentity,
    escape_lucene_query,
    normalize_for_comparison,
    select_musicbrainz_match,
)
from PIL import Image

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "musicbrainz"


@pytest.fixture(autouse=True)
def _block_live_http(monkeypatch):
    def fail_live_request(*_args, **_kwargs):
        raise AssertionError("online artwork tests must not use the live network")

    monkeypatch.setattr(requests.sessions.Session, "request", fail_live_request)


def load_recordings(name):
    with (FIXTURE_DIR / name).open(encoding="utf-8") as fixture:
        return json.load(fixture)["recordings"]


class FakeResponse:
    def __init__(self, body=b'{"recordings":[]}', status_code=200, headers=None, chunks=None):
        self.body = body
        self.status_code = status_code
        self.headers = headers or {}
        self.chunks = chunks
        self.closed = False

    def iter_content(self, chunk_size):
        assert chunk_size == 8192
        if isinstance(self.chunks, Exception):
            raise self.chunks
        yield from self.chunks if self.chunks is not None else [self.body]

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses=None, exception=None):
        self.headers = {}
        self.responses = list(responses or [FakeResponse()])
        self.exception = exception
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.exception is not None:
            raise self.exception
        return self.responses.pop(0)


def make_image(image_format="PNG", size=(640, 480), orientation=None):
    image = Image.new("RGB", size, (20, 100, 180))
    output = io.BytesIO()
    kwargs = {}
    if orientation is not None:
        exif = Image.Exif()
        exif[274] = orientation
        kwargs["exif"] = exif
    image.save(output, format=image_format, **kwargs)
    return output.getvalue()


def image_response(image_format="PNG", size=(640, 480), orientation=None):
    body = make_image(image_format, size, orientation)
    media_type = "image/png" if image_format == "PNG" else "image/jpeg"
    return FakeResponse(
        body=body,
        headers={"Content-Type": media_type, "Content-Length": str(len(body))},
    )


def artwork_match(release_group=True, release_count=3):
    return MusicBrainzMatch(
        recording_id="11111111-1111-4111-8111-111111111111",
        score=100,
        release_group_id=("22222222-2222-4222-8222-222222222222" if release_group else None),
        release_ids=tuple(
            f"33333333-3333-4333-8333-{index:012d}" for index in range(1, release_count + 1)
        ),
    )


def public_addresses(_hostname):
    return ["93.184.216.34"]


def test_whitespace_and_case_differences_share_an_identity():
    first = TrackIdentity("  The   Band ", " A\tSong ", " The Album ")
    second = TrackIdentity("the band", "a song", "the album")

    assert first.artist == "The Band"
    assert first.title == "A Song"
    assert first.cache_key == second.cache_key


def test_unicode_normalization_handles_composed_diacritics():
    composed = TrackIdentity("Beyonc\u00e9", "D\u00e9j\u00e0 Vu")
    decomposed = TrackIdentity("Beyonce\u0301", "De\u0301ja\u0300 Vu")

    assert composed.cache_key == decomposed.cache_key
    assert normalize_for_comparison("Beyonc\u00e9") != normalize_for_comparison("Beyonce")


def test_punctuation_is_normalized_for_comparison():
    punctuated = TrackIdentity("Artist, One & Artist-Two", "Hello: World!")
    plain = TrackIdentity("artist one artist two", "hello world")

    assert punctuated.cache_key == plain.cache_key


def test_multiple_artists_and_feat_forms_are_compared_conservatively():
    abbreviated = TrackIdentity("Artist feat. Guest", "Song")
    punctuation_variant = TrackIdentity("ARTIST FEAT GUEST", "Song")
    expanded = TrackIdentity("Artist featuring Guest", "Song")

    assert abbreviated.cache_key == punctuation_variant.cache_key
    assert abbreviated.cache_key != expanded.cache_key


@pytest.mark.parametrize("qualifier", ["Remix", "Live", "Radio Version"])
def test_title_qualifiers_remain_distinguishable(qualifier):
    original = TrackIdentity("Artist", "Song")
    version = TrackIdentity("Artist", f"Song ({qualifier})")

    assert original.cache_key != version.cache_key


@pytest.mark.parametrize(
    ("artist", "title"),
    [("", "Song"), ("Artist", ""), (" \t ", "Song"), ("Artist", "... ")],
)
def test_missing_or_unusable_artist_or_title_has_no_identity(artist, title):
    assert TrackIdentity.from_metadata(artist, title) is None
    with pytest.raises(ValueError):
        TrackIdentity(artist, title)


def test_album_is_optional_but_distinguishes_release_context():
    no_album = TrackIdentity("Artist", "Song")
    first_album = TrackIdentity("Artist", "Song", "First Album")
    second_album = TrackIdentity("Artist", "Song", "Second Album")

    assert no_album.album is None
    assert len({no_album.cache_key, first_album.cache_key, second_album.cache_key}) == 3


def test_cache_key_is_stable_and_does_not_expose_metadata():
    identity = TrackIdentity("Private Artist", "Secret Song", "Hidden Album")

    assert identity.cache_key == "bf068051acd7a5dc5691edb2165072d34368c1e6e4ffe333221419285eef264c"
    assert re.fullmatch(r"[0-9a-f]{64}", identity.cache_key)
    assert "private" not in identity.cache_key
    assert "secret" not in identity.cache_key


def test_lucene_special_characters_are_escaped():
    special = '+-&|!(){}[]^"~*?:\\/'

    assert escape_lucene_query(special) == "".join(f"\\{character}" for character in special)


def test_musicbrainz_search_uses_bounded_identified_request():
    response = FakeResponse(
        json.dumps({"recordings": [{"id": "first"}, {"id": "second"}]}).encode()
    )
    session = FakeSession([response])
    client = MusicBrainzClient(session=session)

    result = client.search(TrackIdentity("AC/DC", "Who? [Live]"))

    assert result.succeeded
    assert [recording["id"] for recording in result.recordings] == ["first", "second"]
    assert result.status_code == 200
    assert session.headers == {
        "User-Agent": MUSICBRAINZ_USER_AGENT,
        "Accept": "application/json",
    }
    assert session.calls == [
        (
            MUSICBRAINZ_RECORDING_URL,
            {
                "params": {
                    "query": r'recording:"Who\? \[Live\]" AND artist:"AC\/DC"',
                    "fmt": "json",
                    "limit": 5,
                },
                "timeout": MUSICBRAINZ_TIMEOUT,
                "stream": True,
                "allow_redirects": False,
            },
        )
    ]
    assert response.closed


def test_provider_user_agent_identifies_product_version_and_project():
    assert re.fullmatch(
        r"PiSonic/[^ ]+ \(https://thk4711\.github\.io/pisonic/\)",
        MUSICBRAINZ_USER_AGENT,
    )
    assert COVER_ART_USER_AGENT == MUSICBRAINZ_USER_AGENT


def test_musicbrainz_search_caps_server_results():
    recordings = [{"id": str(index)} for index in range(8)]
    client = MusicBrainzClient(
        session=FakeSession([FakeResponse(json.dumps({"recordings": recordings}).encode())])
    )

    result = client.search(TrackIdentity("Artist", "Title"))

    assert len(result.recordings) == 5


@pytest.mark.parametrize(
    ("status_code", "failure"),
    [
        (302, MusicBrainzFailure.REDIRECT),
        (400, MusicBrainzFailure.HTTP),
        (404, MusicBrainzFailure.HTTP),
        (429, MusicBrainzFailure.RATE_LIMITED),
        (500, MusicBrainzFailure.SERVER),
        (503, MusicBrainzFailure.SERVER),
    ],
)
def test_musicbrainz_http_failures_are_classified(status_code, failure):
    response = FakeResponse(status_code=status_code)
    client = MusicBrainzClient(session=FakeSession([response]))

    result = client.search(TrackIdentity("Artist", "Title"))

    assert not result.succeeded
    assert result.failure is failure
    assert result.status_code == status_code
    assert response.closed


def test_musicbrainz_exposes_bounded_retry_after():
    response = FakeResponse(status_code=429, headers={"Retry-After": "120"})
    client = MusicBrainzClient(session=FakeSession([response]))

    result = client.search(TrackIdentity("Artist", "Title"))

    assert result.failure is MusicBrainzFailure.RATE_LIMITED
    assert result.retry_after == 120


def test_retry_after_supports_http_dates_and_rejects_invalid_values(monkeypatch):
    monkeypatch.setattr(online_artwork.time, "time", lambda: 100.0)
    dated = MusicBrainzClient(
        session=FakeSession(
            [
                FakeResponse(
                    status_code=503,
                    headers={"Retry-After": "Thu, 01 Jan 1970 00:02:40 GMT"},
                )
            ]
        )
    ).search(TrackIdentity("Artist", "Title"))
    invalid = MusicBrainzClient(
        session=FakeSession([FakeResponse(status_code=503, headers={"Retry-After": "later"})])
    ).search(TrackIdentity("Artist", "Title"))

    assert dated.retry_after == 60
    assert invalid.retry_after is None


@pytest.mark.parametrize(
    ("exception", "failure"),
    [
        (requests.exceptions.Timeout("secret song"), MusicBrainzFailure.TIMEOUT),
        (requests.exceptions.ConnectionError("secret song"), MusicBrainzFailure.CONNECTION),
        (requests.exceptions.SSLError("secret song"), MusicBrainzFailure.TLS),
    ],
)
def test_musicbrainz_network_failures_do_not_raise_or_log_metadata(caplog, exception, failure):
    client = MusicBrainzClient(session=FakeSession(exception=exception))

    result = client.search(TrackIdentity("Private Artist", "Secret Song"))

    assert result.failure is failure
    assert "Private Artist" not in caplog.text
    assert "Secret Song" not in caplog.text


def test_musicbrainz_read_failure_is_controlled_and_closes_response():
    response = FakeResponse(chunks=requests.exceptions.ConnectionError("stream failed"))
    client = MusicBrainzClient(session=FakeSession([response]))

    result = client.search(TrackIdentity("Artist", "Title"))

    assert result.failure is MusicBrainzFailure.CONNECTION
    assert response.closed


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(headers={"Content-Length": str(MUSICBRAINZ_MAX_RESPONSE_BYTES + 1)}),
        FakeResponse(headers={"Content-Length": "invalid"}),
        FakeResponse(chunks=[b"x" * MUSICBRAINZ_MAX_RESPONSE_BYTES, b"x"]),
    ],
)
def test_musicbrainz_response_size_is_bounded(response):
    client = MusicBrainzClient(session=FakeSession([response]))

    result = client.search(TrackIdentity("Artist", "Title"))

    assert result.failure is MusicBrainzFailure.RESPONSE_TOO_LARGE
    assert response.closed


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b"{}",
        b'{"recordings":{}}',
        b'{"recordings":[null]}',
        b"\xff",
    ],
)
def test_musicbrainz_malformed_json_or_schema_is_controlled(body):
    client = MusicBrainzClient(session=FakeSession([FakeResponse(body)]))

    result = client.search(TrackIdentity("Artist", "Title"))

    assert result.failure is MusicBrainzFailure.INVALID_RESPONSE


def test_musicbrainz_requests_start_at_least_one_second_apart():
    now = [10.0]
    sleeps = []

    def sleep(delay):
        sleeps.append(delay)
        now[0] += delay

    session = FakeSession([FakeResponse(), FakeResponse()])
    client = MusicBrainzClient(session=session, clock=lambda: now[0], sleeper=sleep)
    track = TrackIdentity("Artist", "Title")

    assert client.search(track).succeeded
    assert client.search(track).succeeded

    assert sleeps == [1.0]


def test_match_selector_accepts_exact_title_artist_and_album():
    match = select_musicbrainz_match(
        TrackIdentity("David Bowie", "Heroes", "Heroes"),
        load_recordings("exact-match.json"),
    )

    assert match is not None
    assert match.recording_id == "11111111-1111-4111-8111-111111111111"
    assert match.score == 100
    assert match.release_group_id == "11111111-1111-4111-8111-111111111114"
    assert match.release_ids == ("11111111-1111-4111-8111-111111111113",)


def test_match_selector_requires_the_requested_artist_credit():
    recordings = load_recordings("same-title-artists.json")

    match = select_musicbrainz_match(TrackIdentity("Beyonce", "Halo"), recordings)

    assert match is not None
    assert match.recording_id == "22222222-2222-4222-8222-222222222224"


def test_match_selector_prefers_original_release_context_over_compilation():
    recordings = load_recordings("original-and-compilation.json")
    track = TrackIdentity("Example Artist", "Common Song")

    match = select_musicbrainz_match(track, recordings)
    reversed_match = select_musicbrainz_match(track, list(reversed(recordings)))

    assert match is not None
    assert reversed_match == match
    assert match.recording_id == "33333333-3333-4333-8333-333333333334"
    assert match.release_group_id == "33333333-3333-4333-8333-333333333336"


def test_matching_album_outranks_other_release_context():
    recordings = load_recordings("original-and-compilation.json")

    match = select_musicbrainz_match(
        TrackIdentity("Example Artist", "Common Song", "Various Hits"), recordings
    )

    assert match is not None
    assert match.recording_id == "33333333-3333-4333-8333-333333333331"


def test_contradictory_album_rejects_a_result():
    match = select_musicbrainz_match(
        TrackIdentity("David Bowie", "Heroes", "Unrelated Album"),
        load_recordings("exact-match.json"),
    )

    assert match is None


def test_version_qualifiers_are_not_removed_to_force_a_match():
    match = select_musicbrainz_match(
        TrackIdentity("M83", "Midnight City"),
        load_recordings("version-ambiguity.json"),
    )

    assert match is None


def test_equally_ranked_exact_results_are_rejected_as_ambiguous():
    recordings = load_recordings("ambiguous-exact.json")

    assert (
        select_musicbrainz_match(TrackIdentity("Example Artist", "Duplicate Song"), recordings)
        is None
    )
    assert (
        select_musicbrainz_match(
            TrackIdentity("Example Artist", "Duplicate Song"), list(reversed(recordings))
        )
        is None
    )


def test_match_selector_rejects_scores_below_threshold():
    recordings = load_recordings("exact-match.json")
    recordings[0]["score"] = MUSICBRAINZ_MIN_MATCH_SCORE - 1

    assert select_musicbrainz_match(TrackIdentity("David Bowie", "Heroes"), recordings) is None


def test_match_selector_rejects_recordings_without_usable_releases():
    recordings = load_recordings("exact-match.json")
    recordings[0]["releases"] = []

    assert select_musicbrainz_match(TrackIdentity("David Bowie", "Heroes"), recordings) is None


def test_match_selector_ignores_malformed_fields():
    valid = load_recordings("exact-match.json")[0]
    malformed_id = deepcopy(valid)
    malformed_id["id"] = "not-an-mbid"
    malformed_artist = deepcopy(valid)
    malformed_artist["artist-credit"] = [None]
    malformed_releases = deepcopy(valid)
    malformed_releases["releases"] = [{"id": "not-an-mbid"}]
    string_score = deepcopy(valid)
    string_score["score"] = "100"

    match = select_musicbrainz_match(
        TrackIdentity("David Bowie", "Heroes"),
        [{}, malformed_id, malformed_artist, malformed_releases, string_score],
    )

    assert match is None


def test_cover_art_uses_release_group_then_at_most_two_releases(tmp_path):
    responses = [FakeResponse(status_code=404), FakeResponse(status_code=404), image_response()]
    session = FakeSession(responses)
    destination = tmp_path / "cover.jpg"
    client = CoverArtArchiveClient(session=session, address_resolver=public_addresses)

    result = client.fetch(artwork_match(), str(destination))

    assert result.succeeded
    assert [call[0] for call in session.calls] == [
        f"{COVER_ART_ARCHIVE_BASE_URL}/release-group/22222222-2222-4222-8222-222222222222/front-250",
        f"{COVER_ART_ARCHIVE_BASE_URL}/release/33333333-3333-4333-8333-000000000001/front-250",
        f"{COVER_ART_ARCHIVE_BASE_URL}/release/33333333-3333-4333-8333-000000000002/front-250",
    ]
    assert all(response.closed for response in responses)


def test_cover_art_stops_after_three_not_found_requests(tmp_path):
    responses = [FakeResponse(status_code=404) for _ in range(3)]
    session = FakeSession(responses)
    client = CoverArtArchiveClient(session=session, address_resolver=public_addresses)

    result = client.fetch(artwork_match(release_count=4), str(tmp_path / "cover.jpg"))

    assert result.failure is CoverArtFailure.NOT_FOUND
    assert result.status_code == 404
    assert len(session.calls) == 3


def test_cover_art_skips_an_absent_release_group(tmp_path):
    session = FakeSession([image_response()])
    client = CoverArtArchiveClient(session=session, address_resolver=public_addresses)

    result = client.fetch(
        artwork_match(release_group=False, release_count=1), str(tmp_path / "cover.jpg")
    )

    assert result.succeeded
    assert session.calls[0][0].startswith(f"{COVER_ART_ARCHIVE_BASE_URL}/release/")


def test_cover_art_rejects_invalid_identifiers_without_requesting(tmp_path):
    session = FakeSession()
    match = MusicBrainzMatch("invalid", 100, "invalid", ("also-invalid",))
    client = CoverArtArchiveClient(session=session, address_resolver=public_addresses)

    result = client.fetch(match, str(tmp_path / "cover.jpg"))

    assert result.failure is CoverArtFailure.INVALID_IDENTIFIER
    assert session.calls == []


def test_cover_art_follows_expected_https_internet_archive_redirect(tmp_path):
    redirect = FakeResponse(
        status_code=307,
        headers={"Location": "https://archive.org/download/mbid/image.jpg"},
    )
    image = image_response("JPEG")
    session = FakeSession([redirect, image])
    client = CoverArtArchiveClient(session=session, address_resolver=public_addresses)

    result = client.fetch(artwork_match(release_count=0), str(tmp_path / "cover.jpg"))

    assert result.succeeded
    assert session.calls[1][0] == "https://archive.org/download/mbid/image.jpg"
    assert session.calls[1][1] == {
        "timeout": COVER_ART_TIMEOUT,
        "stream": True,
        "allow_redirects": False,
    }
    assert session.headers == {
        "User-Agent": COVER_ART_USER_AGENT,
        "Accept": "image/jpeg, image/png",
    }
    assert redirect.closed
    assert image.closed


@pytest.mark.parametrize(
    ("location", "addresses"),
    [
        ("http://archive.org/download/image.jpg", ["93.184.216.34"]),
        ("https://example.com/image.jpg", ["93.184.216.34"]),
        ("https://archive.org:444/image.jpg", ["93.184.216.34"]),
        ("https://archive.org/image.jpg", ["127.0.0.1"]),
        ("https://archive.org/image.jpg", ["10.0.0.1"]),
        ("https://archive.org/image.jpg", ["169.254.1.1"]),
        ("https://archive.org/image.jpg", ["::1"]),
    ],
)
def test_cover_art_rejects_unsafe_redirects(tmp_path, location, addresses):
    session = FakeSession([FakeResponse(status_code=302, headers={"Location": location})])
    client = CoverArtArchiveClient(session=session, address_resolver=lambda _host: addresses)

    result = client.fetch(artwork_match(release_count=0), str(tmp_path / "cover.jpg"))

    assert result.failure is CoverArtFailure.UNSAFE_REDIRECT
    assert len(session.calls) == 1


def test_cover_art_rejects_redirects_that_do_not_resolve(tmp_path):
    session = FakeSession(
        [FakeResponse(status_code=302, headers={"Location": "https://archive.org/image.jpg"})]
    )

    def failed_resolution(_hostname):
        raise OSError("DNS unavailable")

    client = CoverArtArchiveClient(session=session, address_resolver=failed_resolution)
    result = client.fetch(artwork_match(release_count=0), str(tmp_path / "cover.jpg"))

    assert result.failure is CoverArtFailure.UNSAFE_REDIRECT


def test_cover_art_bounds_redirect_count(tmp_path):
    responses = [
        FakeResponse(status_code=302, headers={"Location": "https://archive.org/image.jpg"})
        for _ in range(COVER_ART_MAX_REDIRECTS + 1)
    ]
    session = FakeSession(responses)
    client = CoverArtArchiveClient(session=session, address_resolver=public_addresses)

    result = client.fetch(artwork_match(release_count=0), str(tmp_path / "cover.jpg"))

    assert result.failure is CoverArtFailure.REDIRECT
    assert len(session.calls) == COVER_ART_MAX_REDIRECTS + 1
    assert all(response.closed for response in responses)


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(
            body=b"\x89PNG\r\n\x1a\n",
            headers={
                "Content-Type": "image/png",
                "Content-Length": str(COVER_ART_MAX_RESPONSE_BYTES + 1),
            },
        ),
        FakeResponse(
            body=b"\x89PNG\r\n\x1a\n",
            headers={"Content-Type": "image/png", "Content-Length": "invalid"},
        ),
        FakeResponse(
            headers={"Content-Type": "image/jpeg"},
            chunks=[b"\xff\xd8\xff" + b"x" * (COVER_ART_MAX_RESPONSE_BYTES - 3), b"x"],
        ),
    ],
)
def test_cover_art_response_size_is_bounded(tmp_path, response):
    client = CoverArtArchiveClient(
        session=FakeSession([response]), address_resolver=public_addresses
    )

    result = client.fetch(artwork_match(release_count=0), str(tmp_path / "cover.jpg"))

    assert result.failure is CoverArtFailure.RESPONSE_TOO_LARGE
    assert response.closed


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse(body=make_image(), headers={"Content-Type": "text/html"}),
        FakeResponse(body=make_image(), headers={"Content-Type": "image/jpeg"}),
        FakeResponse(body=b"\xff\xd8\xffnot-an-image", headers={"Content-Type": "image/jpeg"}),
        FakeResponse(
            body=b"\x89PNG\r\n\x1a\nnot-an-image",
            headers={"Content-Type": "image/png"},
        ),
    ],
)
def test_cover_art_rejects_unsupported_mismatched_or_corrupt_images(tmp_path, response):
    destination = tmp_path / "cover.jpg"
    destination.write_bytes(b"existing")
    client = CoverArtArchiveClient(
        session=FakeSession([response]), address_resolver=public_addresses
    )

    result = client.fetch(artwork_match(release_count=0), str(destination))

    assert result.failure in {CoverArtFailure.INVALID_MEDIA_TYPE, CoverArtFailure.INVALID_IMAGE}
    assert destination.read_bytes() == b"existing"


@pytest.mark.parametrize("image_format", ["JPEG", "PNG"])
def test_cover_art_normalizes_and_atomically_publishes_supported_images(tmp_path, image_format):
    destination = tmp_path / "cover.jpg"
    destination.write_bytes(b"old image")
    session = FakeSession([image_response(image_format, (640, 480))])
    client = CoverArtArchiveClient(session=session, address_resolver=public_addresses)

    result = client.fetch(artwork_match(release_count=0), str(destination))

    data = destination.read_bytes()
    assert result.succeeded
    assert result.fingerprint == hashlib.sha256(data).hexdigest()
    assert data.startswith(b"\xff\xd8\xff")
    assert stat.S_IMODE(destination.stat().st_mode) == 0o644
    assert not list(tmp_path.glob("artwork-*"))
    with Image.open(destination) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.size == (COVER_ART_OUTPUT_MAX_DIMENSION, 240)


def test_cover_art_publication_uses_atomic_replace(tmp_path, monkeypatch):
    destination = tmp_path / "cover.jpg"
    destination.write_bytes(b"old image")
    replacements = []
    real_replace = online_artwork.os.replace

    def record_replace(source, target):
        replacements.append((source, target))
        real_replace(source, target)

    monkeypatch.setattr(online_artwork.os, "replace", record_replace)
    client = CoverArtArchiveClient(
        session=FakeSession([image_response("JPEG")]), address_resolver=public_addresses
    )

    result = client.fetch(artwork_match(release_count=0), str(destination))

    assert result.succeeded
    assert len(replacements) == 1
    assert Path(replacements[0][0]).parent == tmp_path
    assert replacements[0][1] == str(destination)


def test_cover_art_applies_exif_orientation(tmp_path):
    destination = tmp_path / "cover.jpg"
    session = FakeSession([image_response("JPEG", (80, 40), orientation=6)])
    client = CoverArtArchiveClient(session=session, address_resolver=public_addresses)

    result = client.fetch(artwork_match(release_count=0), str(destination))

    assert result.succeeded
    with Image.open(destination) as image:
        assert image.size == (40, 80)


def test_cover_art_rejects_unreasonable_dimensions(tmp_path):
    response = image_response("PNG", (4097, 1))
    client = CoverArtArchiveClient(
        session=FakeSession([response]), address_resolver=public_addresses
    )

    result = client.fetch(artwork_match(release_count=0), str(tmp_path / "cover.jpg"))

    assert result.failure is CoverArtFailure.INVALID_IMAGE


def test_cover_art_rejects_pillow_decompression_bomb_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)
    response = image_response("PNG", (8, 8))
    client = CoverArtArchiveClient(
        session=FakeSession([response]), address_resolver=public_addresses
    )

    result = client.fetch(artwork_match(release_count=0), str(tmp_path / "cover.jpg"))

    assert result.failure is CoverArtFailure.INVALID_IMAGE


@pytest.mark.parametrize(
    ("status_code", "failure"),
    [
        (400, CoverArtFailure.HTTP),
        (429, CoverArtFailure.RATE_LIMITED),
        (500, CoverArtFailure.SERVER),
        (503, CoverArtFailure.SERVER),
    ],
)
def test_cover_art_http_failures_are_classified(tmp_path, status_code, failure):
    response = FakeResponse(status_code=status_code)
    client = CoverArtArchiveClient(
        session=FakeSession([response]), address_resolver=public_addresses
    )

    result = client.fetch(artwork_match(release_count=0), str(tmp_path / "cover.jpg"))

    assert result.failure is failure
    assert result.status_code == status_code
    assert response.closed


def test_cover_art_exposes_bounded_retry_after(tmp_path):
    response = FakeResponse(status_code=503, headers={"Retry-After": "999999"})
    client = CoverArtArchiveClient(
        session=FakeSession([response]), address_resolver=public_addresses
    )

    result = client.fetch(artwork_match(release_count=0), str(tmp_path / "cover.jpg"))

    assert result.failure is CoverArtFailure.SERVER
    assert result.retry_after == 24 * 60 * 60


@pytest.mark.parametrize(
    ("exception", "failure"),
    [
        (requests.exceptions.Timeout(), CoverArtFailure.TIMEOUT),
        (requests.exceptions.ConnectionError(), CoverArtFailure.CONNECTION),
        (requests.exceptions.SSLError(), CoverArtFailure.TLS),
    ],
)
def test_cover_art_network_failures_are_controlled(tmp_path, exception, failure):
    client = CoverArtArchiveClient(
        session=FakeSession(exception=exception), address_resolver=public_addresses
    )

    result = client.fetch(artwork_match(release_count=0), str(tmp_path / "cover.jpg"))

    assert result.failure is failure


def test_cover_art_publish_failure_is_controlled(tmp_path):
    destination = tmp_path / "missing" / "cover.jpg"
    client = CoverArtArchiveClient(
        session=FakeSession([image_response()]), address_resolver=public_addresses
    )

    result = client.fetch(artwork_match(release_count=0), str(destination))

    assert result.failure is CoverArtFailure.FILE_IO
    assert not destination.exists()


def _wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _recording_for(track):
    return {
        "id": "11111111-1111-4111-8111-111111111111",
        "score": 100,
        "title": track.title,
        "artist-credit": [{"name": track.artist}],
        "releases": [
            {
                "id": "22222222-2222-4222-8222-222222222222",
                "title": track.album or "Album",
                "status": "Official",
                "release-group": {
                    "id": "33333333-3333-4333-8333-333333333333",
                    "title": track.album or "Album",
                    "primary-type": "Album",
                },
            }
        ],
    }


class ResolverMusicBrainz:
    def __init__(self, search=None):
        self.calls = []
        self.closed = False
        self._search = search

    def search(self, track):
        self.calls.append(track)
        if self._search is not None:
            return self._search(track, len(self.calls))
        return MusicBrainzSearchResult(recordings=(_recording_for(track),), status_code=200)

    def close(self):
        self.closed = True


class ResolverCoverArt:
    def __init__(self, data_size=32):
        self.calls = []
        self.closed = False
        self.data_size = data_size

    def fetch(self, match, destination):
        self.calls.append((match, destination))
        data = b"\xff\xd8\xff" + bytes([len(self.calls)]) * self.data_size
        Path(destination).write_bytes(data)
        return CoverArtResult(fingerprint=hashlib.sha256(data).hexdigest(), status_code=200)

    def close(self):
        self.closed = True


def _make_resolver(tmp_path, musicbrainz=None, cover_art=None, **kwargs):
    return OnlineArtworkResolver(
        musicbrainz=musicbrainz or ResolverMusicBrainz(),
        cover_art=cover_art or ResolverCoverArt(),
        cache_directory=str(tmp_path / "cache"),
        publication_path=str(tmp_path / "bluetooth_cover.jpg"),
        **kwargs,
    )


def test_resolver_request_is_async_and_publishes_success(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def delayed_search(track, _call_count):
        started.set()
        release.wait(1)
        return MusicBrainzSearchResult(recordings=(_recording_for(track),), status_code=200)

    musicbrainz = ResolverMusicBrainz(delayed_search)
    resolver = _make_resolver(tmp_path, musicbrainz=musicbrainz)
    completed = []
    try:
        before = time.monotonic()
        assert resolver.request(TrackIdentity("Artist", "Song"), completed.append)
        assert time.monotonic() - before < 0.1
        assert started.wait(1)
        assert completed == []

        release.set()
        assert _wait_until(lambda: len(completed) == 1)
        assert completed[0].cover_path == str(tmp_path / "bluetooth_cover.jpg")
        assert Path(completed[0].cover_path).read_bytes().startswith(b"\xff\xd8\xff")
    finally:
        release.set()
        resolver.close()


def test_resolver_deduplicates_current_track(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def delayed_search(track, _call_count):
        started.set()
        release.wait(1)
        return MusicBrainzSearchResult(recordings=(_recording_for(track),), status_code=200)

    musicbrainz = ResolverMusicBrainz(delayed_search)
    resolver = _make_resolver(tmp_path, musicbrainz=musicbrainz)
    track = TrackIdentity("Artist", "Song")
    try:
        assert resolver.request(track, lambda _result: None)
        assert started.wait(1)
        assert not resolver.request(track, lambda _result: None)
        release.set()
        assert _wait_until(lambda: len(resolver._positive) == 1)
        assert len(musicbrainz.calls) == 1
    finally:
        release.set()
        resolver.close()


def test_resolver_keeps_only_latest_pending_request_and_discards_stale_completion(tmp_path):
    first_started = threading.Event()
    release_first = threading.Event()

    def search(track, call_count):
        if call_count == 1:
            first_started.set()
            release_first.wait(1)
        return MusicBrainzSearchResult(recordings=(_recording_for(track),), status_code=200)

    musicbrainz = ResolverMusicBrainz(search)
    cover_art = ResolverCoverArt()
    resolver = _make_resolver(tmp_path, musicbrainz=musicbrainz, cover_art=cover_art)
    completed = []
    first = TrackIdentity("Artist", "First")
    second = TrackIdentity("Artist", "Second")
    latest = TrackIdentity("Artist", "Latest")
    try:
        assert resolver.request(first, completed.append)
        assert first_started.wait(1)
        assert resolver.request(second, completed.append)
        assert resolver.request(latest, completed.append)
        release_first.set()

        assert _wait_until(lambda: len(completed) == 1)
        assert [track.title for track in musicbrainz.calls] == ["First", "Latest"]
        assert completed[0].track_key == latest.cache_key
        assert len(cover_art.calls) == 1
    finally:
        release_first.set()
        resolver.close()


def test_resolver_discards_download_that_finishes_after_track_change(tmp_path):
    download_started = threading.Event()
    release_download = threading.Event()

    class BlockingCoverArt(ResolverCoverArt):
        def fetch(self, match, destination):
            if not self.calls:
                download_started.set()
                release_download.wait(1)
            return super().fetch(match, destination)

    cover_art = BlockingCoverArt()
    resolver = _make_resolver(tmp_path, cover_art=cover_art)
    completed = []
    first = TrackIdentity("Artist", "First")
    latest = TrackIdentity("Artist", "Latest")
    try:
        assert resolver.request(first, completed.append)
        assert download_started.wait(1)
        assert resolver.request(latest, completed.append)
        release_download.set()

        assert _wait_until(lambda: len(completed) == 1)
        assert completed[0].track_key == latest.cache_key
        assert len(cover_art.calls) == 2
    finally:
        release_download.set()
        resolver.close()


def test_resolver_reuses_positive_cache_after_track_returns(tmp_path):
    musicbrainz = ResolverMusicBrainz()
    resolver = _make_resolver(tmp_path, musicbrainz=musicbrainz)
    track = TrackIdentity("Private Artist", "Secret Song")
    completed = []
    try:
        assert resolver.request(track, completed.append)
        assert _wait_until(lambda: len(completed) == 1)
        resolver.cancel()
        assert resolver.request(track, completed.append)
        assert _wait_until(lambda: len(completed) == 2)

        assert len(musicbrainz.calls) == 1
        cache_files = list((tmp_path / "cache").glob("*.jpg"))
        assert [path.name for path in cache_files] == [f"{track.cache_key}.jpg"]
        assert "Private" not in cache_files[0].name
        assert "Secret" not in cache_files[0].name
    finally:
        resolver.close()


def test_resolver_validates_and_reuses_cache_after_restart(tmp_path):
    track = TrackIdentity("Artist", "Song")
    cache_directory = tmp_path / "cache"
    cache_directory.mkdir()
    cached_data = make_image("JPEG", (40, 40))
    (cache_directory / f"{track.cache_key}.jpg").write_bytes(cached_data)
    (cache_directory / "unfinished.tmp").write_bytes(b"partial")
    musicbrainz = ResolverMusicBrainz()
    resolver = _make_resolver(tmp_path, musicbrainz=musicbrainz)
    completed = []
    try:
        assert resolver.request(track, completed.append)
        assert _wait_until(lambda: len(completed) == 1)

        assert musicbrainz.calls == []
        assert completed[0].fingerprint == hashlib.sha256(cached_data).hexdigest()
        assert not (cache_directory / "unfinished.tmp").exists()
    finally:
        resolver.close()


def test_resolver_negative_cache_is_bounded_and_avoids_repeat_lookup(tmp_path):
    def no_match(_track, _call_count):
        return MusicBrainzSearchResult(recordings=(), status_code=200)

    musicbrainz = ResolverMusicBrainz(no_match)
    resolver = _make_resolver(
        tmp_path,
        musicbrainz=musicbrainz,
        negative_ttl=60,
        max_negative_entries=2,
    )
    tracks = [TrackIdentity("Artist", f"Song {index}") for index in range(3)]
    try:
        for index, track in enumerate(tracks, start=1):
            assert resolver.request(track, lambda _result: None)
            assert _wait_until(lambda expected=index: len(musicbrainz.calls) == expected)
        assert len(resolver._negative) == 2

        resolver.cancel()
        assert resolver.request(tracks[-1], lambda _result: None)
        time.sleep(0.05)
        assert len(musicbrainz.calls) == 3
    finally:
        resolver.close()


def test_resolver_retries_transient_failure_after_monotonic_deadline(tmp_path):
    def fail_once(track, call_count):
        if call_count == 1:
            return MusicBrainzSearchResult(failure=MusicBrainzFailure.TIMEOUT)
        return MusicBrainzSearchResult(recordings=(_recording_for(track),), status_code=200)

    musicbrainz = ResolverMusicBrainz(fail_once)
    resolver = _make_resolver(tmp_path, musicbrainz=musicbrainz, transient_retry=0.02)
    completed = []
    try:
        assert resolver.request(TrackIdentity("Artist", "Song"), completed.append)
        assert _wait_until(lambda: len(completed) == 1)
        assert len(musicbrainz.calls) == 2
    finally:
        resolver.close()


def test_resolver_schedules_provider_retry_after_on_monotonic_clock(tmp_path):
    now = 100.0

    def rate_limited(_track, _call_count):
        return MusicBrainzSearchResult(
            failure=MusicBrainzFailure.RATE_LIMITED,
            status_code=429,
            retry_after=42,
        )

    musicbrainz = ResolverMusicBrainz(rate_limited)
    resolver = _make_resolver(tmp_path, musicbrainz=musicbrainz, clock=lambda: now)
    track = TrackIdentity("Artist", "Song")
    try:
        assert resolver.request(track, lambda _result: None)
        assert _wait_until(lambda: track.cache_key in resolver._negative)

        assert resolver._negative[track.cache_key].retry_at == 142
        assert resolver._pending is not None
        assert resolver._pending.not_before == 142
    finally:
        resolver.close()


def test_resolver_bounds_positive_file_count_and_total_size(tmp_path):
    musicbrainz = ResolverMusicBrainz()
    resolver = _make_resolver(
        tmp_path,
        musicbrainz=musicbrainz,
        cover_art=ResolverCoverArt(data_size=20),
        max_positive_files=2,
        max_cache_bytes=46,
    )
    completed = []
    tracks = [TrackIdentity("Artist", f"Song {index}") for index in range(3)]
    try:
        for index, track in enumerate(tracks, start=1):
            assert resolver.request(track, completed.append)
            assert _wait_until(lambda expected=index: len(completed) == expected)

        cache_files = list((tmp_path / "cache").glob("*.jpg"))
        assert len(cache_files) == 2
        assert sum(path.stat().st_size for path in cache_files) <= 46
        assert not (tmp_path / "cache" / f"{tracks[0].cache_key}.jpg").exists()
    finally:
        resolver.close()


def test_resolver_contains_provider_and_callback_exceptions(tmp_path):
    def raise_once(track, call_count):
        if call_count == 1:
            raise RuntimeError("private metadata must not be logged")
        return MusicBrainzSearchResult(recordings=(_recording_for(track),), status_code=200)

    musicbrainz = ResolverMusicBrainz(raise_once)
    resolver = _make_resolver(tmp_path, musicbrainz=musicbrainz, transient_retry=60)
    completed = threading.Event()
    try:
        assert resolver.request(
            TrackIdentity("Private Artist", "Secret Song"),
            lambda _result: (_ for _ in ()).throw(RuntimeError("callback failed")),
        )
        assert _wait_until(lambda: len(musicbrainz.calls) == 1)
        assert resolver.request(
            TrackIdentity("Artist", "Callback Song"),
            lambda _result: (_ for _ in ()).throw(RuntimeError("callback failed")),
        )
        assert _wait_until(lambda: len(resolver._positive) == 1)
        assert resolver.request(
            TrackIdentity("Artist", "Next Song"), lambda _result: completed.set()
        )
        assert completed.wait(1)
        assert resolver.worker_thread.is_alive()
    finally:
        resolver.close()


def test_resolver_contains_cover_art_exceptions_and_keeps_worker_alive(tmp_path):
    class RaisingCoverArt(ResolverCoverArt):
        def fetch(self, match, destination):
            if not self.calls:
                self.calls.append((match, destination))
                raise RuntimeError("download failed")
            return super().fetch(match, destination)

    cover_art = RaisingCoverArt()
    resolver = _make_resolver(tmp_path, cover_art=cover_art, transient_retry=60)
    completed = threading.Event()
    try:
        assert resolver.request(TrackIdentity("Artist", "First"), lambda _result: None)
        assert _wait_until(lambda: len(cover_art.calls) == 1)
        assert resolver.request(TrackIdentity("Artist", "Second"), lambda _result: completed.set())

        assert completed.wait(1)
        assert len(cover_art.calls) == 2
        assert resolver.worker_thread.is_alive()
    finally:
        resolver.close()


def test_resolver_recreates_volatile_cache_after_it_disappears(tmp_path):
    track = TrackIdentity("Artist", "Song")
    first_musicbrainz = ResolverMusicBrainz()
    first = _make_resolver(tmp_path, musicbrainz=first_musicbrainz)
    completed = []
    try:
        assert first.request(track, completed.append)
        assert _wait_until(lambda: len(completed) == 1)
    finally:
        first.close()

    shutil.rmtree(tmp_path / "cache")
    (tmp_path / "bluetooth_cover.jpg").unlink()

    second_musicbrainz = ResolverMusicBrainz()
    second = _make_resolver(tmp_path, musicbrainz=second_musicbrainz)
    completed = []
    try:
        assert second.request(track, completed.append)
        assert _wait_until(lambda: len(completed) == 1)

        assert len(second_musicbrainz.calls) == 1
        assert (tmp_path / "cache" / f"{track.cache_key}.jpg").is_file()
        assert (tmp_path / "bluetooth_cover.jpg").is_file()
    finally:
        second.close()


def test_resolver_close_interrupts_retry_wait_and_closes_clients(tmp_path):
    def timeout(_track, _call_count):
        return MusicBrainzSearchResult(failure=MusicBrainzFailure.TIMEOUT)

    musicbrainz = ResolverMusicBrainz(timeout)
    cover_art = ResolverCoverArt()
    resolver = _make_resolver(
        tmp_path,
        musicbrainz=musicbrainz,
        cover_art=cover_art,
        transient_retry=60,
    )
    assert resolver.request(TrackIdentity("Artist", "Song"), lambda _result: None)
    assert _wait_until(lambda: len(musicbrainz.calls) == 1)

    started = time.monotonic()
    resolver.close()

    assert time.monotonic() - started < 0.5
    assert not resolver.worker_thread.is_alive()
    assert musicbrainz.closed
    assert cover_art.closed
