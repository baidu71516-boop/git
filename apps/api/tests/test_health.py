import asyncio

from app.main import app
from httpx import ASGITransport, AsyncClient, Response


def test_live_health_uses_response_envelope() -> None:
    async def make_request() -> Response:
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.get(
                    "/health/live",
                    headers={"X-Request-ID": "test-request"},
                )

    response = asyncio.run(make_request())

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "test-request"
    assert response.json() == {
        "success": True,
        "data": {"status": "ok"},
        "error": None,
        "request_id": "test-request",
    }
