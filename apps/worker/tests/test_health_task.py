from app.tasks.health import ping


def test_ping_task() -> None:
    assert ping.run() == {"status": "ok"}
