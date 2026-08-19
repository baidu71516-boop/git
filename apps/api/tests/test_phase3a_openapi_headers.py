"""OpenAPI regressions for Phase 3A mutation-header truthfulness."""

from __future__ import annotations

from app.main import app

PHASE3A_MUTATION_IDEMPOTENCY_REQUIREMENTS = {
    ("/api/v1/candidate-pools", "post"): True,
    ("/api/v1/candidate-pools/{pool_id}/policies", "post"): True,
    ("/api/v1/candidate-pools/{pool_id}/runs", "post"): True,
    ("/api/v1/campaigns", "post"): True,
    ("/api/v1/campaigns/{campaign_id}", "put"): False,
    ("/api/v1/campaigns/{campaign_id}/lifecycle", "post"): False,
    ("/api/v1/campaigns/{campaign_id}/members/bulk-add", "post"): True,
    ("/api/v1/campaigns/{campaign_id}/members/from-candidate-run", "post"): True,
    ("/api/v1/campaigns/{campaign_id}/members/{member_id}/remove", "post"): False,
    ("/api/v1/campaigns/{campaign_id}/outreach-targets", "post"): True,
    ("/api/v1/outreach-targets/{target_id}", "put"): False,
    ("/api/v1/outreach-targets/{target_id}/tasks", "post"): True,
    ("/api/v1/outreach-tasks/{task_id}/transitions", "post"): True,
}


def test_phase3a_mutation_headers_match_runtime_requirements_in_openapi() -> None:
    document = app.openapi()

    for (path, method), requires_idempotency in PHASE3A_MUTATION_IDEMPOTENCY_REQUIREMENTS.items():
        operation = document["paths"][path][method]
        headers = {
            parameter["name"]: parameter
            for parameter in operation["parameters"]
            if parameter["in"] == "header"
        }

        assert headers["X-CSRF-Token"]["required"] is True
        assert headers["X-Department-ID"]["required"] is False
        if requires_idempotency:
            assert headers["Idempotency-Key"]["required"] is True
        else:
            assert "Idempotency-Key" not in headers
