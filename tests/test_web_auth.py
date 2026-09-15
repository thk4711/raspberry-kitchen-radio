"""Unit tests for radio_web.auth and radio_web.config_store (Phase 4)."""

import os
import stat

import pytest

from radio_web import auth, config_store


@pytest.fixture(autouse=True)
def _secret_dir(monkeypatch, tmp_path):
    """Point the managed config store at a temp dir for every test here."""
    monkeypatch.setattr(config_store, "MANAGED_CONFIG_DIR", str(tmp_path))
    return tmp_path


class TestPasswordHashing:
    def test_hash_verify_roundtrip(self):
        stored = auth.hash_password("correct horse")
        assert auth.verify_password("correct horse", stored)

    def test_wrong_password_fails(self):
        stored = auth.hash_password("correct horse")
        assert not auth.verify_password("battery staple", stored)

    def test_stored_format_and_salt_uniqueness(self):
        a = auth.hash_password("same-password")
        b = auth.hash_password("same-password")
        # pbkdf2_sha256$<iters>$<salt>$<hash>
        assert a.startswith("pbkdf2_sha256$")
        assert a.count("$") == 3
        # Random salt => two hashes of the same password differ.
        assert a != b

    def test_malformed_stored_value_is_rejected(self):
        assert not auth.verify_password("x", "not-a-valid-hash")
        assert not auth.verify_password("x", "")


class TestSecretFile:
    def test_is_configured_transitions(self, _secret_dir):
        assert not auth.is_configured()
        auth.set_password("longenough")
        assert auth.is_configured()

    def test_check_password(self, _secret_dir):
        auth.set_password("longenough")
        assert auth.check_password("longenough")
        assert not auth.check_password("wrong")

    def test_check_password_unconfigured_is_false(self, _secret_dir):
        assert not auth.check_password("anything")

    def test_secret_file_mode_0600(self, _secret_dir):
        auth.set_password("longenough")
        path = config_store.admin_secret_path()
        mode = stat.S_IMODE(os.stat(path).st_mode)
        assert mode == 0o600

    def test_short_password_rejected(self, _secret_dir):
        with pytest.raises(ValueError):
            auth.set_password("short")
        assert not auth.is_configured()


class TestAtomicWrite:
    def test_atomic_write_creates_file_with_mode(self, tmp_path):
        target = tmp_path / "sub" / "file.txt"
        config_store.atomic_write(str(target), "hello", 0o600)
        assert target.read_text() == "hello"
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o600

    def test_atomic_write_leaves_no_temp_files(self, tmp_path):
        target = tmp_path / "file.txt"
        config_store.atomic_write(str(target), "data")
        leftovers = [p for p in os.listdir(tmp_path) if p.startswith(".tmp-")]
        assert leftovers == []

    def test_atomic_write_overwrites(self, tmp_path):
        target = tmp_path / "file.txt"
        config_store.atomic_write(str(target), "one")
        config_store.atomic_write(str(target), "two")
        assert target.read_text() == "two"


class _FakeClock:
    def __init__(self, start=0.0):
        self.now = start

    def __call__(self):
        return self.now


class TestSessions:
    def test_create_and_validate(self):
        clock = _FakeClock()
        store = auth.SessionStore(ttl_seconds=100, clock=clock)
        session = store.create()
        assert store.validate(session.token) is session

    def test_expiry(self):
        clock = _FakeClock()
        store = auth.SessionStore(ttl_seconds=100, clock=clock)
        session = store.create()
        clock.now = 101
        assert store.validate(session.token) is None

    def test_destroy(self):
        store = auth.SessionStore()
        session = store.create()
        store.destroy(session.token)
        assert store.validate(session.token) is None

    def test_validate_none_and_unknown(self):
        store = auth.SessionStore()
        assert store.validate(None) is None
        assert store.validate("nope") is None

    def test_sweep_drops_expired(self):
        clock = _FakeClock()
        store = auth.SessionStore(ttl_seconds=10, clock=clock)
        store.create()
        clock.now = 20
        store.sweep()
        # A fresh session created after sweep is still valid.
        fresh = store.create()
        assert store.validate(fresh.token) is fresh


class TestCsrf:
    def test_valid_token_accepted(self):
        session = auth.SessionStore().create()
        assert auth.check_csrf(session, session.csrf_token)

    def test_wrong_token_rejected(self):
        session = auth.SessionStore().create()
        assert not auth.check_csrf(session, "tampered")

    def test_missing_inputs_rejected(self):
        session = auth.SessionStore().create()
        assert not auth.check_csrf(None, session.csrf_token)
        assert not auth.check_csrf(session, None)


class TestRateLimiter:
    def test_blocks_after_max_attempts(self):
        clock = _FakeClock()
        rl = auth.RateLimiter(max_attempts=3, window_seconds=60, clock=clock)
        for _ in range(3):
            assert not rl.is_blocked("ip")
            rl.register_failure("ip")
        assert rl.is_blocked("ip")

    def test_success_clears(self):
        rl = auth.RateLimiter(max_attempts=2, window_seconds=60)
        rl.register_failure("ip")
        rl.register_failure("ip")
        assert rl.is_blocked("ip")
        rl.register_success("ip")
        assert not rl.is_blocked("ip")

    def test_window_resets(self):
        clock = _FakeClock()
        rl = auth.RateLimiter(max_attempts=1, window_seconds=30, clock=clock)
        rl.register_failure("ip")
        assert rl.is_blocked("ip")
        clock.now = 31
        assert not rl.is_blocked("ip")


class TestRequireAuth:
    def test_gate(self):
        assert not auth.require_auth(None)
        session = auth.SessionStore().create()
        assert auth.require_auth(session)
