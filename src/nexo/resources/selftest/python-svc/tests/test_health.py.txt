from svc import health


def test_health_ok() -> None:
    assert health() == {"status": "ok"}
