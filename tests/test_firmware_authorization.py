from radio_web import firmware_authorization as grants


def test_grant_is_session_bound_and_one_use():
    grants.clear()
    grant = grants.issue("session-a", "tracking-a", clock=lambda: 10.0)
    assert grants.consume(grant, "session-b", clock=lambda: 11.0) is None
    assert grants.consume(grant, "session-a", clock=lambda: 11.0) is None


def test_grant_expires():
    grants.clear()
    grant = grants.issue("session-a", "tracking-a", clock=lambda: 10.0)
    assert grants.consume(grant, "session-a", clock=lambda: 10.0 + grants.GRANT_TTL_SECONDS) is None


def test_valid_grant_is_consumed_once():
    grants.clear()
    grant = grants.issue("session-a", "tracking-a", clock=lambda: 10.0)
    assert grants.consume(grant, "session-a", clock=lambda: 11.0) == "tracking-a"
    assert grants.consume(grant, "session-a", clock=lambda: 11.0) is None
