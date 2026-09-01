"""Frozen Task 4A route-to-module inventory for Permissions V1."""

from collections import Counter
from dataclasses import dataclass
from typing import Literal

from app.main import app
from backend_core.auth import ModuleKey, ModuleRequirementKind
from fastapi.routing import APIRoute

type Operation = tuple[str, str]
type RouteOwner = Literal[
    "admin",
    "data_collection",
    "candidate_pools",
    "campaigns",
    "today_outreach",
    "influencer_library",
    "imports",
    "data_updates",
]


@dataclass(frozen=True, slots=True)
class GuardExpectation:
    kind: ModuleRequirementKind
    modules: frozenset[ModuleKey]
    write: bool
    owner: RouteOwner


def _guard(
    kind: ModuleRequirementKind,
    *modules: ModuleKey,
    write: bool,
    owner: RouteOwner,
) -> GuardExpectation:
    return GuardExpectation(kind, frozenset(modules), write, owner)


def _add(
    expected: dict[Operation, GuardExpectation | None],
    guard: GuardExpectation,
    *operations: Operation,
) -> None:
    for operation in operations:
        assert operation not in expected, f"duplicate frozen operation {operation!r}"
        expected[operation] = guard


def _frozen_task_4a_map() -> dict[Operation, GuardExpectation | None]:
    """Keep this independently declared from router source and OpenAPI metadata."""

    expected: dict[Operation, GuardExpectation | None] = {
        ("GET", "/health/live"): None,
        ("GET", "/health/ready"): None,
        ("GET", "/api/v1/departments"): None,
        ("POST", "/api/v1/auth/login"): None,
        ("GET", "/api/v1/auth/me"): None,
        ("POST", "/api/v1/auth/select-operator"): None,
        ("POST", "/api/v1/auth/logout"): None,
        ("GET", "/api/v1/operators"): None,
        ("POST", "/api/v1/admin/content-activity/douyin/runtime-captures/ingest"): None,
    }

    _add(
        expected,
        _guard(
            ModuleRequirementKind.EXACT,
            ModuleKey.ADMIN,
            write=True,
            owner="admin",
        ),
        ("POST", "/api/v1/admin/departments/{department_id}/reset-password"),
        ("POST", "/api/v1/admin/operators"),
        ("PATCH", "/api/v1/admin/operators/{operator_id}"),
        ("POST", "/api/v1/admin/content-activity/xiaohongshu/identities/resolve"),
        ("POST", "/api/v1/admin/content-activity/xiaohongshu/refreshes"),
        ("POST", "/api/v1/admin/content-activity/douyin/runtime-captures"),
    )
    _add(
        expected,
        _guard(
            ModuleRequirementKind.EXACT,
            ModuleKey.ADMIN,
            write=False,
            owner="admin",
        ),
        ("GET", "/api/v1/admin/operators"),
        ("GET", "/api/v1/admin/operators/{operator_id}"),
    )

    _add(
        expected,
        _guard(
            ModuleRequirementKind.EXACT,
            ModuleKey.DATA_COLLECTION,
            write=True,
            owner="data_collection",
        ),
        ("POST", "/api/v1/collection-jobs"),
        ("PUT", "/api/v1/collection-jobs/{collection_job_id}/screening-rules"),
    )
    _add(
        expected,
        _guard(
            ModuleRequirementKind.EXACT,
            ModuleKey.DATA_COLLECTION,
            write=False,
            owner="data_collection",
        ),
        ("GET", "/api/v1/collection-jobs"),
        ("GET", "/api/v1/collection-jobs/{collection_job_id}"),
    )

    _add(
        expected,
        _guard(
            ModuleRequirementKind.EXACT,
            ModuleKey.CANDIDATE_POOLS,
            write=True,
            owner="candidate_pools",
        ),
        ("POST", "/api/v1/candidate-pools"),
        ("POST", "/api/v1/candidate-pools/{pool_id}/policies"),
        ("POST", "/api/v1/candidate-pools/{pool_id}/runs"),
    )
    _add(
        expected,
        _guard(
            ModuleRequirementKind.EXACT,
            ModuleKey.CANDIDATE_POOLS,
            write=False,
            owner="candidate_pools",
        ),
        ("GET", "/api/v1/candidate-pools"),
        ("GET", "/api/v1/candidate-pools/{pool_id}"),
        ("GET", "/api/v1/candidate-pools/{pool_id}/policies"),
        ("GET", "/api/v1/candidate-pools/{pool_id}/policies/{policy_id}"),
        ("GET", "/api/v1/candidate-pools/{pool_id}/runs"),
        ("GET", "/api/v1/candidate-pools/{pool_id}/runs/{run_id}"),
        ("GET", "/api/v1/candidate-pools/{pool_id}/runs/{run_id}/members"),
    )

    _add(
        expected,
        _guard(
            ModuleRequirementKind.EXACT,
            ModuleKey.CAMPAIGNS,
            write=True,
            owner="campaigns",
        ),
        ("POST", "/api/v1/campaigns"),
        ("PUT", "/api/v1/campaigns/{campaign_id}"),
        ("POST", "/api/v1/campaigns/{campaign_id}/lifecycle"),
        ("POST", "/api/v1/campaigns/{campaign_id}/members/bulk-add"),
        ("POST", "/api/v1/campaigns/{campaign_id}/members/{member_id}/remove"),
        ("POST", "/api/v1/campaigns/{campaign_id}/outreach-targets"),
        ("PUT", "/api/v1/outreach-targets/{target_id}"),
        ("POST", "/api/v1/outreach-targets/{target_id}/tasks"),
    )
    _add(
        expected,
        _guard(
            ModuleRequirementKind.EXACT,
            ModuleKey.CAMPAIGNS,
            write=False,
            owner="campaigns",
        ),
        ("GET", "/api/v1/campaigns"),
        ("GET", "/api/v1/campaigns/{campaign_id}"),
        ("GET", "/api/v1/campaigns/{campaign_id}/members"),
        ("GET", "/api/v1/campaigns/{campaign_id}/members/{member_id}"),
        ("GET", "/api/v1/outreach-targets/{target_id}"),
    )
    _add(
        expected,
        _guard(
            ModuleRequirementKind.ALL_OF,
            ModuleKey.CAMPAIGNS,
            ModuleKey.CANDIDATE_POOLS,
            write=True,
            owner="campaigns",
        ),
        ("POST", "/api/v1/campaigns/{campaign_id}/members/from-candidate-run"),
    )

    _add(
        expected,
        _guard(
            ModuleRequirementKind.EXACT,
            ModuleKey.TODAY_OUTREACH,
            write=True,
            owner="today_outreach",
        ),
        ("POST", "/api/v1/outreach-tasks/{task_id}/transitions"),
    )
    _add(
        expected,
        _guard(
            ModuleRequirementKind.EXACT,
            ModuleKey.TODAY_OUTREACH,
            write=False,
            owner="today_outreach",
        ),
        ("GET", "/api/v1/outreach-tasks/today"),
        ("GET", "/api/v1/outreach-tasks/{task_id}"),
        ("GET", "/api/v1/outreach-tasks/{task_id}/events"),
    )

    _add(
        expected,
        _guard(
            ModuleRequirementKind.EXACT,
            ModuleKey.INFLUENCER_LIBRARY,
            write=False,
            owner="influencer_library",
        ),
        ("GET", "/api/v1/influencers"),
        ("GET", "/api/v1/influencers/filter-options"),
        ("GET", "/api/v1/influencers/{influencer_id}"),
        ("GET", "/api/v1/influencers/{influencer_id}/metric-snapshots"),
    )

    _add(
        expected,
        _guard(
            ModuleRequirementKind.EXACT,
            ModuleKey.DATA_COLLECTION,
            write=True,
            owner="imports",
        ),
        ("POST", "/api/v1/import-jobs"),
        ("POST", "/api/v1/import-jobs/bulk"),
        ("POST", "/api/v1/import-jobs/{import_job_id}/files"),
        ("PATCH", "/api/v1/import-jobs/{import_job_id}/files/{import_job_file_id}"),
        ("PUT", "/api/v1/import-jobs/{import_job_id}/files/{import_job_file_id}/mapping"),
        ("POST", "/api/v1/import-jobs/{import_job_id}/files/{import_job_file_id}/retry"),
        ("POST", "/api/v1/import-jobs/{import_job_id}/files/{import_job_file_id}/exclude"),
        ("PUT", "/api/v1/import-jobs/{import_job_id}/mapping"),
        ("POST", "/api/v1/import-jobs/{import_job_id}/preview"),
        ("POST", "/api/v1/import-jobs/{import_job_id}/retry"),
        ("POST", "/api/v1/import-jobs/{import_job_id}/confirm"),
        ("POST", "/api/v1/import-jobs/{import_job_id}/cancel"),
    )
    _add(
        expected,
        _guard(
            ModuleRequirementKind.EXACT,
            ModuleKey.IMPORT_HISTORY,
            write=False,
            owner="imports",
        ),
        ("GET", "/api/v1/import-jobs"),
    )
    _add(
        expected,
        _guard(
            ModuleRequirementKind.ANY_OF,
            ModuleKey.DATA_COLLECTION,
            ModuleKey.IMPORT_HISTORY,
            write=False,
            owner="imports",
        ),
        ("GET", "/api/v1/import-jobs/{import_job_id}"),
        ("GET", "/api/v1/import-jobs/{import_job_id}/files"),
        ("GET", "/api/v1/import-jobs/{import_job_id}/rows"),
    )

    _add(
        expected,
        _guard(
            ModuleRequirementKind.EXACT,
            ModuleKey.DATA_UPDATES,
            write=True,
            owner="data_updates",
        ),
        ("POST", "/api/v1/refresh-queues"),
        ("POST", "/api/v1/refresh-queues/{refresh_queue_id}/export"),
        ("POST", "/api/v1/refresh-queues/{refresh_queue_id}/cancel"),
    )
    _add(
        expected,
        _guard(
            ModuleRequirementKind.EXACT,
            ModuleKey.DATA_UPDATES,
            write=False,
            owner="data_updates",
        ),
        ("GET", "/api/v1/refresh-queues"),
        ("GET", "/api/v1/refresh-queues/{refresh_queue_id}"),
        ("GET", "/api/v1/refresh-queues/{refresh_queue_id}/items"),
    )

    return expected


def _mounted_operations() -> dict[Operation, APIRoute]:
    operations: dict[Operation, APIRoute] = {}
    for included_router in app.routes:
        router = getattr(included_router, "original_router", None)
        for route in getattr(router, "routes", ()):
            if not isinstance(route, APIRoute):
                continue
            for method in route.methods:
                operation = (method, route.path)
                assert operation not in operations, f"duplicate mounted operation {operation!r}"
                operations[operation] = route
    return operations


def _declared_module_guards(route: APIRoute) -> list[object]:
    guards: list[object] = []
    pending = [route.dependant]
    seen: set[int] = set()
    while pending:
        dependant = pending.pop()
        if id(dependant) in seen:
            continue
        seen.add(id(dependant))
        call = getattr(dependant, "call", None)
        if getattr(call, "permissions_v1_requirement", None) is not None:
            guards.append(call)
        pending.extend(dependant.dependencies)
    return guards


def test_mounted_api_matches_the_frozen_permissions_v1_task_4a_map() -> None:
    expected = _frozen_task_4a_map()
    actual = _mounted_operations()

    assert len(expected) == 75
    assert set(actual) == set(expected)
    exempt = {operation for operation, guard in expected.items() if guard is None}
    guarded = {operation: guard for operation, guard in expected.items() if guard is not None}
    assert len(exempt) == 9
    assert len(guarded) == 66
    assert Counter(guard.owner for guard in guarded.values()) == {
        "admin": 8,
        "data_collection": 4,
        "candidate_pools": 10,
        "campaigns": 14,
        "today_outreach": 4,
        "influencer_library": 4,
        "imports": 16,
        "data_updates": 6,
    }

    closed_module_keys = frozenset(ModuleKey)
    for operation, route in actual.items():
        expected_guard = expected[operation]
        declarations = _declared_module_guards(route)
        if expected_guard is None:
            assert declarations == [], f"exempt route gained a Human guard: {operation!r}"
            continue

        assert len(declarations) == 1, f"Human route guard drift: {operation!r}"
        declared = declarations[0]
        requirement = declared.permissions_v1_requirement
        assert requirement.kind is expected_guard.kind
        assert requirement.modules == expected_guard.modules
        assert requirement.modules <= closed_module_keys
        assert declared.permissions_v1_write is expected_guard.write

    ingest = ("POST", "/api/v1/admin/content-activity/douyin/runtime-captures/ingest")
    assert expected[ingest] is None
    assert _declared_module_guards(actual[ingest]) == []
