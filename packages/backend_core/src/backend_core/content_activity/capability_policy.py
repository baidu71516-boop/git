"""Immutable, source-controlled TikHub XHS capability-policy artifacts.

The artifact IDs below are a closed V1 registry.  They deliberately verify a
hard-coded content digest at runtime: changing a schema, success predicate, or
fixture digest under an existing ID must fail closed rather than silently widen
what a historical observation means.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from importlib.resources import files
from importlib.resources.abc import Traversable
from types import MappingProxyType
from typing import Any, Final

TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1: Final = "TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1"
TIKHUB_XHS_APP_V2_GET_USER_INFO_RESPONSE_SCHEMA_V1: Final = (
    "TIKHUB_XHS_APP_V2_GET_USER_INFO_RESPONSE_SCHEMA_V1"
)
TIKHUB_XHS_APP_V2_GET_USER_POSTED_NOTES_RESPONSE_SCHEMA_V1: Final = (
    "TIKHUB_XHS_APP_V2_GET_USER_POSTED_NOTES_RESPONSE_SCHEMA_V1"
)
TIKHUB_XHS_APP_V2_CURRENT_PUBLIC_VISIBILITY_POLICY_V1: Final = (
    "TIKHUB_XHS_APP_V2_CURRENT_PUBLIC_VISIBILITY_POLICY_V1"
)

TIKHUB_PROVIDER: Final = "TIKHUB"
XHS_APP_V2_PRODUCT: Final = "XIAOHONGSHU_APP_V2"
XHS_GET_USER_INFO_ENDPOINT: Final = "/api/v1/xiaohongshu/app_v2/get_user_info"
XHS_GET_USER_POSTED_NOTES_ENDPOINT: Final = "/api/v1/xiaohongshu/app_v2/get_user_posted_notes"
XHS_SEMANTIC_SUCCESS_CODE: Final = 200


class CapabilityArtifactIntegrityError(RuntimeError):
    """An immutable V1 artifact was missing, malformed, or unexpectedly changed."""


@dataclass(frozen=True, slots=True)
class CapabilityArtifact:
    """One closed contract artifact and its review-pinned digests."""

    contract_id: str
    filename: str
    sha256_digest: str
    fixture_digests: Mapping[str, str]


# IMPORTANT: Changing either an artifact or the accepted fixture digest under
# these IDs requires a new subordinate ID and a new capability-policy version.
_ARTIFACTS: Final[tuple[CapabilityArtifact, ...]] = (
    CapabilityArtifact(
        contract_id=TIKHUB_XHS_APP_V2_GET_USER_INFO_RESPONSE_SCHEMA_V1,
        filename="tikhub_xhs_get_user_info_response_schema_v1.json",
        sha256_digest="16128b89a5c6b645d6d0dd6a5d58d17f1de933610948a91b292f12e17f6841a4",
        fixture_digests=MappingProxyType(
            {
                "positive": "86c9240edcb3c0858cc7c56eeeba6fd4b0826424a1b931954b6a4fbe37eba805",
                "negative": "234055e40a4ffb2d81a61e4f6d559992dc8b620effa70f47c2ef322cb64de4a4",
            }
        ),
    ),
    CapabilityArtifact(
        contract_id=TIKHUB_XHS_APP_V2_GET_USER_POSTED_NOTES_RESPONSE_SCHEMA_V1,
        filename="tikhub_xhs_get_user_posted_notes_response_schema_v1.json",
        sha256_digest="38f76759f8bf7aa1675038632f9be297cf6043ca1fb85e6ee8b5d7eef0779b6e",
        fixture_digests=MappingProxyType(
            {
                "positive": "d6fb4eebb79fd3654d231513cde8031f880fdc607d7e6dcf8703c81a6ebeb122",
                "negative_semantic_failure": (
                    "775bbce4bbe92442323e8818a151700caaf6126bb58fe58de978f8e6b6194aac"
                ),
                "negative_unknown_type": (
                    "dd7028a6d20ff6a3c8381146949a0b16b573776ae699b094af004ed6239d4f83"
                ),
                "negative_has_more": (
                    "04cc72160dfd62807807f59747dac15c695a741d2417a082b503abcdcedcac78"
                ),
            }
        ),
    ),
    CapabilityArtifact(
        contract_id=TIKHUB_XHS_APP_V2_CURRENT_PUBLIC_VISIBILITY_POLICY_V1,
        filename="tikhub_xhs_current_public_visibility_policy_v1.json",
        sha256_digest="663db2ee2f9b80093e4e0a0277d8587a04b29ee6aa419618461222be9a8ef415",
        fixture_digests=MappingProxyType(
            {
                "positive_membership": (
                    "d6fb4eebb79fd3654d231513cde8031f880fdc607d7e6dcf8703c81a6ebeb122"
                ),
                "negative_unknown_visibility": (
                    "e7fd46c666aa260202e8521e555601e61faf3101780f2887c87bd176696b76ce"
                ),
            }
        ),
    ),
)

XHS_V1_ARTIFACTS: Final[Mapping[str, CapabilityArtifact]] = MappingProxyType(
    {artifact.contract_id: artifact for artifact in _ARTIFACTS}
)


def artifact_content_digest(contract_id: str) -> str:
    """Return the pinned SHA-256 digest for one accepted V1 artifact."""

    try:
        return XHS_V1_ARTIFACTS[contract_id].sha256_digest
    except KeyError as exc:
        raise CapabilityArtifactIntegrityError("unregistered capability artifact") from exc


def read_capability_artifact(contract_id: str) -> Mapping[str, Any]:
    """Read a registry artifact only after its immutable digest is verified."""

    artifact = _artifact_for(contract_id)
    raw = _artifact_resource(artifact.filename).read_bytes()
    if sha256(raw).hexdigest() != artifact.sha256_digest:
        raise CapabilityArtifactIntegrityError("capability artifact digest mismatch")
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise CapabilityArtifactIntegrityError("capability artifact is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise CapabilityArtifactIntegrityError("capability artifact has invalid root shape")
    _verify_artifact_metadata(parsed, artifact)
    return MappingProxyType(parsed)


def assert_xhs_v1_registry_integrity() -> None:
    """Fail closed unless every closed V1 artifact still matches its pinned digest."""

    for artifact in _ARTIFACTS:
        read_capability_artifact(artifact.contract_id)


def _artifact_for(contract_id: str) -> CapabilityArtifact:
    try:
        return XHS_V1_ARTIFACTS[contract_id]
    except KeyError as exc:
        raise CapabilityArtifactIntegrityError("unregistered capability artifact") from exc


def _artifact_resource(filename: str) -> Traversable:
    return files("backend_core.content_activity").joinpath("artifacts", filename)


def _verify_artifact_metadata(parsed: Mapping[str, Any], artifact: CapabilityArtifact) -> None:
    if parsed.get("contract_id") != artifact.contract_id:
        raise CapabilityArtifactIntegrityError("capability artifact contract ID mismatch")
    if parsed.get("capability_policy_version") != TIKHUB_CONTENT_ACTIVITY_CAPABILITY_POLICY_V1:
        raise CapabilityArtifactIntegrityError("capability policy version mismatch")
    source_reference = parsed.get("static_source_reference")
    if not isinstance(source_reference, dict) or not all(
        isinstance(source_reference.get(key), str) and source_reference[key].strip()
        for key in ("source", "reference_id", "assertion")
    ):
        raise CapabilityArtifactIntegrityError("capability artifact source reference missing")
    fixtures = parsed.get("sanitized_fixture_digests")
    if not isinstance(fixtures, dict):
        raise CapabilityArtifactIntegrityError("capability artifact fixture digests missing")
    actual_fixture_digests = _fixture_digests(fixtures)
    if actual_fixture_digests != dict(artifact.fixture_digests):
        raise CapabilityArtifactIntegrityError("capability artifact fixture digest mismatch")
    _verify_artifact_contract_details(parsed, artifact.contract_id)


def _verify_artifact_contract_details(parsed: Mapping[str, Any], contract_id: str) -> None:
    if (
        parsed.get("provider") != TIKHUB_PROVIDER
        or parsed.get("provider_product") != XHS_APP_V2_PRODUCT
    ):
        raise CapabilityArtifactIntegrityError("capability artifact provider/product mismatch")

    expected_endpoints = {
        TIKHUB_XHS_APP_V2_GET_USER_INFO_RESPONSE_SCHEMA_V1: XHS_GET_USER_INFO_ENDPOINT,
        TIKHUB_XHS_APP_V2_GET_USER_POSTED_NOTES_RESPONSE_SCHEMA_V1: (
            XHS_GET_USER_POSTED_NOTES_ENDPOINT
        ),
        TIKHUB_XHS_APP_V2_CURRENT_PUBLIC_VISIBILITY_POLICY_V1: XHS_GET_USER_POSTED_NOTES_ENDPOINT,
    }
    if parsed.get("endpoint") != expected_endpoints[contract_id]:
        raise CapabilityArtifactIntegrityError("capability artifact endpoint mismatch")

    if contract_id == TIKHUB_XHS_APP_V2_GET_USER_INFO_RESPONSE_SCHEMA_V1:
        _verify_success_marker(parsed)
        _verify_request_query(
            parsed,
            expected={"share_text": "bounded_ephemeral_bootstrap_input"},
        )
    elif contract_id == TIKHUB_XHS_APP_V2_GET_USER_POSTED_NOTES_RESPONSE_SCHEMA_V1:
        _verify_success_marker(parsed)
        _verify_request_query(
            parsed,
            expected={"user_id": "verified_current_xiaohongshu_userid", "cursor": ""},
        )
        request_contract = parsed.get("request_contract")
        if not isinstance(request_contract, dict) or request_contract.get("page_budget") != 1:
            raise CapabilityArtifactIntegrityError("capability artifact page budget mismatch")
    else:
        completeness = parsed.get("completeness")
        if (
            not isinstance(completeness, dict)
            or completeness.get("first_response_has_more") is not False
            or completeness.get("has_more_type") != "boolean"
            or completeness.get("page_budget") != 1
        ):
            raise CapabilityArtifactIntegrityError("capability artifact completeness mismatch")


def _verify_success_marker(parsed: Mapping[str, Any]) -> None:
    semantic_success = parsed.get("semantic_success")
    if not isinstance(semantic_success, dict) or semantic_success != {
        "field_path": "$.code",
        "accepted_type": "integer",
        "accepted_value": XHS_SEMANTIC_SUCCESS_CODE,
    }:
        raise CapabilityArtifactIntegrityError("capability artifact semantic-success mismatch")


def _verify_request_query(parsed: Mapping[str, Any], *, expected: Mapping[str, str]) -> None:
    request_contract = parsed.get("request_contract")
    if not isinstance(request_contract, dict) or request_contract.get("method") != "GET":
        raise CapabilityArtifactIntegrityError("capability artifact request method mismatch")
    if request_contract.get("query") != dict(expected):
        raise CapabilityArtifactIntegrityError("capability artifact request mapping mismatch")


def _fixture_digests(fixtures: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, fixture in fixtures.items():
        if not isinstance(name, str) or not isinstance(fixture, dict):
            raise CapabilityArtifactIntegrityError("capability artifact fixture entry invalid")
        path = fixture.get("path")
        digest = fixture.get("sha256")
        if not isinstance(path, str) or not path.startswith(
            "packages/backend_core/tests/fixtures/"
        ):
            raise CapabilityArtifactIntegrityError("capability artifact fixture path invalid")
        if not isinstance(digest, str) or len(digest) != 64:
            raise CapabilityArtifactIntegrityError("capability artifact fixture digest invalid")
        result[name] = digest
    return result
