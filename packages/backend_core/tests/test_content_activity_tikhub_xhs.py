import asyncio
import json
import logging
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from io import StringIO
from pathlib import Path

import httpx
import pytest
from backend_core.common.logging import JsonFormatter, configure_logging
from backend_core.config.settings import Settings
from backend_core.content_activity.capability_policy import (
    XHS_GET_USER_INFO_ENDPOINT,
    XHS_GET_USER_POSTED_NOTES_ENDPOINT,
    XHS_V1_ARTIFACTS,
    assert_xhs_v1_registry_integrity,
    read_capability_artifact,
)
from backend_core.content_activity.enums import (
    ContentActivityCoverageStatus,
    ContentActivityObservationStatus,
    ContentActivityProviderErrorClass,
    ContentActivityPublicationType,
    ContentActivityResult,
)
from backend_core.content_activity.tikhub_xhs import (
    HttpRequestLimits,
    HttpTransportResponse,
    HttpxAsyncTransport,
    ProviderResponseTooLarge,
    TikHubXhsClient,
    XhsSchemaViolation,
    XhsSemanticFailure,
    parse_xhs_posted_notes_response,
    parse_xhs_user_info_response,
)
from pydantic import SecretStr

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "content_activity"
OBSERVED_AT = datetime(2024, 6, 29, 0, 0, tzinfo=UTC)


class RecordingTransport:
    def __init__(
        self,
        *responses: HttpTransportResponse,
        exception: BaseException | None = None,
    ) -> None:
        self._responses = list(responses)
        self._exception = exception
        self.calls: list[dict[str, object]] = []

    async def get(
        self,
        *,
        path: str,
        params: Mapping[str, str],
        headers: Mapping[str, str],
        limits: HttpRequestLimits,
    ) -> HttpTransportResponse:
        self.calls.append(
            {
                "path": path,
                "params": dict(params),
                "headers": dict(headers),
                "limits": limits,
            }
        )
        if self._exception is not None:
            raise self._exception
        if not self._responses:
            raise AssertionError("unexpected second provider call")
        return self._responses.pop(0)


class DelayedByteStream(httpx.AsyncByteStream):
    """Deterministic drip stream that records cancellation-driven cleanup."""

    def __init__(self, chunks: tuple[bytes, ...], *, delay_seconds: float) -> None:
        self._chunks = chunks
        self._delay_seconds = delay_seconds
        self.first_chunk_ready = asyncio.Event()
        self.closed = False
        self.yielded_chunks = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            await asyncio.sleep(self._delay_seconds)
            self.yielded_chunks += 1
            self.first_chunk_ready.set()
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def _fixture_body(name: str) -> bytes:
    return (FIXTURE_DIR / name).read_bytes()


def _response(name: str, status_code: int = 200) -> HttpTransportResponse:
    return HttpTransportResponse(status_code=status_code, body=_fixture_body(name))


def _enabled_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "content_activity_enabled": True,
        "content_activity_xhs_enabled": True,
        "content_activity_provider_governance_approved": True,
        "content_activity_provider_max_calls_per_run": 1,
        "tikhub_base_url": "https://tikhub.test",
        "tikhub_api_key": SecretStr("synthetic-test-token"),
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def _client(transport: RecordingTransport) -> TikHubXhsClient:
    return TikHubXhsClient(
        _enabled_settings(),
        transport=transport,
        clock=lambda: OBSERVED_AT,
    )


def _activity_payload(*, has_more: bool, notes: list[dict[str, object]]) -> bytes:
    return json.dumps(
        {"code": 200, "data": {"data": {"has_more": has_more, "notes": notes}}},
        separators=(",", ":"),
    ).encode()


def _drip_chunks(body: bytes, *, count: int) -> tuple[bytes, ...]:
    """Split a bounded test body into non-empty ordered streaming chunks."""

    chunk_size = max(1, (len(body) + count - 1) // count)
    return tuple(body[index : index + chunk_size] for index in range(0, len(body), chunk_size))


def test_v1_registry_artifacts_and_source_controlled_fixture_digests_are_intact() -> None:
    assert_xhs_v1_registry_integrity()

    for contract_id, expected in XHS_V1_ARTIFACTS.items():
        artifact = read_capability_artifact(contract_id)
        assert artifact["contract_id"] == contract_id
        source = artifact["static_source_reference"]["source"]
        assert source in {
            "TikHub official public API documentation",
            "TikHub official Xiaohongshu App V2 guide",
        }
        actual_fixture_digests = {
            name: descriptor["sha256"]
            for name, descriptor in artifact["sanitized_fixture_digests"].items()
        }
        assert actual_fixture_digests == dict(expected.fixture_digests)
        for descriptor in artifact["sanitized_fixture_digests"].values():
            fixture = FIXTURE_DIR / Path(descriptor["path"]).name
            assert sha256(fixture.read_bytes()).hexdigest() == descriptor["sha256"]


def test_identity_schema_accepts_only_the_pinned_semantic_success_envelope() -> None:
    assert parse_xhs_user_info_response(_fixture_body("xhs_get_user_info_success_v1.json")) == (
        "synthetic-xhs-userid-001"
    )
    with pytest.raises(XhsSemanticFailure):
        parse_xhs_user_info_response(_fixture_body("xhs_get_user_info_semantic_failure_v1.json"))
    with pytest.raises(XhsSchemaViolation):
        parse_xhs_user_info_response(
            b'{"code":200,"data":{"data":{"userid":"synthetic"},"message":"drift"}}'
        )


def test_identity_client_maps_semantic_failure_to_untrusted_not_verified_identity() -> None:
    transport = RecordingTransport(_response("xhs_get_user_info_semantic_failure_v1.json"))

    result = asyncio.run(
        _client(transport).resolve_user(bootstrap_input="https://xhs.test/profile/synthetic")
    )

    assert not result.usable
    assert result.observation_status is ContentActivityObservationStatus.RESULT_UNTRUSTED
    assert result.provider_error_class is ContentActivityProviderErrorClass.SEMANTIC_FAILURE


def test_identity_client_missing_userid_fails_closed_without_a_binding() -> None:
    transport = RecordingTransport(
        HttpTransportResponse(status_code=200, body=b'{"code":200,"data":{"data":{}}}')
    )

    result = asyncio.run(
        _client(transport).resolve_user(bootstrap_input="https://xhs.test/profile/synthetic")
    )

    assert not result.usable
    assert result.userid is None
    assert result.observation_status is ContentActivityObservationStatus.RESULT_UNTRUSTED
    assert result.provider_error_class is ContentActivityProviderErrorClass.SCHEMA_DRIFT
    assert result.provider_error_code == "SCHEMA_KEYS_UNRECOGNIZED"


def test_activity_schema_uses_max_not_provider_order_and_preserves_tied_count() -> None:
    parsed = parse_xhs_posted_notes_response(
        _fixture_body("xhs_get_user_posted_notes_success_v1.json"),
        observed_at=OBSERVED_AT,
    )
    assert not parsed.has_more
    assert parsed.raw_item_count == 3
    assert [publication.publication_type for publication in parsed.publications] == [
        ContentActivityPublicationType.VIDEO,
        ContentActivityPublicationType.IMAGE_TEXT,
        ContentActivityPublicationType.VIDEO,
    ]

    transport = RecordingTransport(_response("xhs_get_user_posted_notes_success_v1.json"))
    attempt = asyncio.run(
        _client(transport).observe_posted_notes(verified_userid="synthetic-userid")
    )

    assert attempt.trusted
    assert attempt.activity_result is ContentActivityResult.PUBLICATION_FOUND
    assert attempt.last_publication_at == datetime.fromtimestamp(1719579078, UTC)
    assert attempt.latest_publication_id == "synthetic-normal-latest"
    assert attempt.latest_publication_type is ContentActivityPublicationType.IMAGE_TEXT
    assert attempt.co_latest_publication_count == 2
    assert attempt.item_count == 3
    assert attempt.request_count == 1
    assert transport.calls == [
        {
            "path": XHS_GET_USER_POSTED_NOTES_ENDPOINT,
            "params": {"user_id": "synthetic-userid", "cursor": ""},
            "headers": {"Authorization": "Bearer synthetic-test-token"},
            "limits": HttpRequestLimits(
                connect_timeout_seconds=2.0,
                read_timeout_seconds=8.0,
                pool_timeout_seconds=2.0,
                total_timeout_seconds=10.0,
                max_response_bytes=256 * 1024,
            ),
        }
    ]


def test_has_more_true_stops_after_one_call_and_never_preserves_partial_maximum() -> None:
    transport = RecordingTransport(
        _response("xhs_get_user_posted_notes_has_more_v1.json"),
        _response("xhs_get_user_posted_notes_success_v1.json"),
    )

    attempt = asyncio.run(
        _client(transport).observe_posted_notes(verified_userid="synthetic-userid")
    )

    assert attempt.observation_status is ContentActivityObservationStatus.RESULT_INCOMPLETE
    assert attempt.coverage_status is ContentActivityCoverageStatus.INCOMPLETE
    assert attempt.activity_result is ContentActivityResult.UNDETERMINED
    assert attempt.last_publication_at is None
    assert attempt.latest_publication_id is None
    assert attempt.request_count == 1
    assert len(transport.calls) == 1
    assert len(transport._responses) == 1


def test_has_more_false_is_not_sufficient_for_trusted_activity() -> None:
    transport = RecordingTransport(_response("xhs_get_user_posted_notes_semantic_failure_v1.json"))

    attempt = asyncio.run(
        _client(transport).observe_posted_notes(verified_userid="synthetic-userid")
    )

    assert attempt.observation_status is ContentActivityObservationStatus.RESULT_UNTRUSTED
    assert attempt.coverage_status is ContentActivityCoverageStatus.UNKNOWN
    assert attempt.activity_result is ContentActivityResult.UNDETERMINED
    assert attempt.provider_error_class is ContentActivityProviderErrorClass.SEMANTIC_FAILURE
    assert attempt.provider_error_code == "SEMANTIC_CODE_REJECTED"


@pytest.mark.parametrize(
    "fixture_name",
    [
        "xhs_get_user_posted_notes_unknown_type_v1.json",
        "xhs_get_user_posted_notes_unknown_visibility_v1.json",
    ],
)
def test_unknown_content_or_visibility_values_fail_closed(fixture_name: str) -> None:
    transport = RecordingTransport(_response(fixture_name))

    attempt = asyncio.run(
        _client(transport).observe_posted_notes(verified_userid="synthetic-userid")
    )

    assert attempt.observation_status is ContentActivityObservationStatus.RESULT_UNTRUSTED
    assert attempt.coverage_status is ContentActivityCoverageStatus.UNKNOWN
    assert attempt.activity_result is ContentActivityResult.UNDETERMINED
    assert attempt.provider_error_class is ContentActivityProviderErrorClass.SCHEMA_DRIFT


def test_future_malformed_and_conflicting_publications_fail_closed() -> None:
    future_transport = RecordingTransport(
        HttpTransportResponse(
            status_code=200,
            body=_activity_payload(
                has_more=False,
                notes=[
                    {
                        "note": {
                            "note_id": "future-note",
                            "type": "normal",
                            "create_time": 1719705600,
                        }
                    }
                ],
            ),
        )
    )
    future_attempt = asyncio.run(
        _client(future_transport).observe_posted_notes(verified_userid="synthetic-userid")
    )
    assert future_attempt.observation_status is ContentActivityObservationStatus.RESULT_UNTRUSTED
    assert future_attempt.provider_error_code == "CREATE_TIME_FUTURE"

    conflict_transport = RecordingTransport(
        HttpTransportResponse(
            status_code=200,
            body=_activity_payload(
                has_more=False,
                notes=[
                    {
                        "note": {
                            "note_id": "duplicate-note",
                            "type": "normal",
                            "create_time": 1719579000,
                        }
                    },
                    {
                        "note": {
                            "note_id": "duplicate-note",
                            "type": "normal",
                            "create_time": 1719579078,
                        }
                    },
                ],
            ),
        )
    )
    conflict_attempt = asyncio.run(
        _client(conflict_transport).observe_posted_notes(verified_userid="synthetic-userid")
    )
    assert conflict_attempt.observation_status is ContentActivityObservationStatus.RESULT_UNTRUSTED
    assert conflict_attempt.provider_error_code == "DUPLICATE_PUBLICATION_CONFLICT"

    malformed_transport = RecordingTransport(
        HttpTransportResponse(
            status_code=200,
            body=_activity_payload(
                has_more=False,
                notes=[
                    {
                        "note": {
                            "note_id": "malformed-time",
                            "type": "video",
                            "create_time": "1719579078",
                        }
                    }
                ],
            ),
        )
    )
    malformed_attempt = asyncio.run(
        _client(malformed_transport).observe_posted_notes(verified_userid="synthetic-userid")
    )
    assert malformed_attempt.observation_status is ContentActivityObservationStatus.RESULT_UNTRUSTED
    assert malformed_attempt.provider_error_code == "CREATE_TIME_INVALID"

    forbidden_timestamp_transport = RecordingTransport(
        HttpTransportResponse(
            status_code=200,
            body=_activity_payload(
                has_more=False,
                notes=[
                    {
                        "note": {
                            "note_id": "forbidden-update-time",
                            "type": "normal",
                            "create_time": 1719579078,
                            "last_update_time": 1719579079,
                        }
                    }
                ],
            ),
        )
    )
    forbidden_timestamp_attempt = asyncio.run(
        _client(forbidden_timestamp_transport).observe_posted_notes(
            verified_userid="synthetic-userid"
        )
    )
    assert (
        forbidden_timestamp_attempt.observation_status
        is ContentActivityObservationStatus.RESULT_UNTRUSTED
    )
    assert forbidden_timestamp_attempt.provider_error_code == "SCHEMA_KEYS_UNRECOGNIZED"


def test_empty_complete_schema_is_trusted_no_public_content_with_null_publication_fields() -> None:
    transport = RecordingTransport(
        HttpTransportResponse(status_code=200, body=_activity_payload(has_more=False, notes=[]))
    )

    attempt = asyncio.run(
        _client(transport).observe_posted_notes(verified_userid="synthetic-userid")
    )

    assert attempt.trusted
    assert attempt.activity_result is ContentActivityResult.NO_PUBLIC_CONTENT
    assert attempt.last_publication_at is None
    assert attempt.latest_publication_id is None
    assert attempt.latest_publication_type is None
    assert attempt.co_latest_publication_count is None


def test_identity_resolution_uses_bounded_ephemeral_share_text_and_never_exposes_it() -> None:
    transport = RecordingTransport(_response("xhs_get_user_info_success_v1.json"))
    bootstrap = (
        "Synthetic creator share text\nhttps://www.xiaohongshu.com/user/profile/synthetic-id"
    )

    result = asyncio.run(_client(transport).resolve_user(bootstrap_input=bootstrap))

    assert result.usable
    assert result.userid == "synthetic-xhs-userid-001"
    assert result.request_count == 1
    assert transport.calls[0]["path"] == XHS_GET_USER_INFO_ENDPOINT
    assert transport.calls[0]["params"] == {"share_text": bootstrap}
    assert bootstrap not in repr(result)


def test_invalid_identity_input_or_disabled_config_fails_closed_without_transport_call() -> None:
    invalid_transport = RecordingTransport(_response("xhs_get_user_posted_notes_success_v1.json"))
    invalid_attempt = asyncio.run(
        _client(invalid_transport).observe_posted_notes(verified_userid=" import-only-id ")
    )
    assert (
        invalid_attempt.observation_status is ContentActivityObservationStatus.IDENTITY_UNRESOLVED
    )
    assert invalid_attempt.request_count == 0
    assert invalid_transport.calls == []

    disabled_transport = RecordingTransport(_response("xhs_get_user_posted_notes_success_v1.json"))
    disabled_client = TikHubXhsClient(Settings(_env_file=None), transport=disabled_transport)
    disabled_attempt = asyncio.run(
        disabled_client.observe_posted_notes(
            verified_userid="synthetic-userid",
            observed_at=OBSERVED_AT,
        )
    )
    assert disabled_attempt.observation_status is ContentActivityObservationStatus.UNKNOWN
    assert disabled_attempt.activity_result is ContentActivityResult.UNDETERMINED
    assert disabled_attempt.request_count == 0
    assert disabled_transport.calls == []

    disabled_resolution = asyncio.run(
        disabled_client.resolve_user(bootstrap_input="https://xhs.test/profile/synthetic")
    )
    assert not disabled_resolution.usable
    assert (
        disabled_resolution.observation_status
        is ContentActivityObservationStatus.IDENTITY_UNRESOLVED
    )
    assert disabled_resolution.request_count == 0
    assert disabled_transport.calls == []


def test_http_429_and_timeout_are_sanitized_without_retry_or_raw_body() -> None:
    rate_limited_transport = RecordingTransport(
        HttpTransportResponse(status_code=429, body=b'{"message":"synthetic provider body"}')
    )
    rate_limited_attempt = asyncio.run(
        _client(rate_limited_transport).observe_posted_notes(verified_userid="synthetic-userid")
    )
    assert (
        rate_limited_attempt.observation_status
        is ContentActivityObservationStatus.PROVIDER_RATE_LIMITED
    )
    assert rate_limited_attempt.provider_error_code == "HTTP_RATE_LIMITED"
    assert "synthetic provider body" not in repr(rate_limited_attempt)
    assert len(rate_limited_transport.calls) == 1

    timeout_transport = RecordingTransport(exception=TimeoutError())
    timeout_attempt = asyncio.run(
        _client(timeout_transport).observe_posted_notes(verified_userid="synthetic-userid")
    )
    assert timeout_attempt.observation_status is ContentActivityObservationStatus.PROVIDER_ERROR
    assert timeout_attempt.provider_error_class is ContentActivityProviderErrorClass.TIMEOUT
    assert timeout_attempt.provider_error_code == "TRANSPORT_TIMEOUT"
    assert len(timeout_transport.calls) == 1


def test_parser_rejects_non_boolean_has_more_and_duplicate_json_keys() -> None:
    with pytest.raises(XhsSchemaViolation):
        parse_xhs_posted_notes_response(
            b'{"code":200,"data":{"data":{"has_more":1,"notes":[]}}}', observed_at=OBSERVED_AT
        )
    with pytest.raises(XhsSchemaViolation):
        parse_xhs_posted_notes_response(
            b'{"code":200,"code":200,"data":{"data":{"has_more":false,"notes":[]}}}',
            observed_at=OBSERVED_AT,
        )


def test_httpx_transport_streams_a_bounded_body_and_does_not_retry() -> None:
    calls: list[httpx.Request] = []

    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, content=b"x" * 64)

        client = httpx.AsyncClient(
            base_url="https://tikhub.test",
            transport=httpx.MockTransport(handler),
        )
        transport = HttpxAsyncTransport("https://tikhub.test", client=client)
        with pytest.raises(ProviderResponseTooLarge):
            await transport.get(
                path=XHS_GET_USER_POSTED_NOTES_ENDPOINT,
                params={"user_id": "synthetic-userid", "cursor": ""},
                headers={"Authorization": "Bearer synthetic-test-token"},
                limits=HttpRequestLimits(
                    connect_timeout_seconds=1,
                    read_timeout_seconds=1,
                    pool_timeout_seconds=1,
                    total_timeout_seconds=1,
                    max_response_bytes=16,
                ),
            )
        await client.aclose()

    asyncio.run(scenario())
    assert len(calls) == 1


def test_slow_drip_hits_total_wall_clock_deadline_and_closes_stream() -> None:
    """Frequent reads cannot extend the provider-call deadline indefinitely."""

    stream = DelayedByteStream(
        _drip_chunks(_fixture_body("xhs_get_user_posted_notes_success_v1.json"), count=10),
        delay_seconds=0.06,
    )

    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, request=request, stream=stream)

        http_client = httpx.AsyncClient(
            base_url="https://tikhub.test",
            transport=httpx.MockTransport(handler),
        )
        transport = HttpxAsyncTransport("https://tikhub.test", client=http_client)
        client = TikHubXhsClient(
            _enabled_settings(
                content_activity_http_connect_timeout_seconds=0.05,
                content_activity_http_read_timeout_seconds=0.15,
                content_activity_http_pool_timeout_seconds=0.05,
                content_activity_http_total_timeout_seconds=0.20,
            ),
            transport=transport,
            clock=lambda: OBSERVED_AT,
        )
        try:
            started_at = asyncio.get_running_loop().time()
            attempt = await client.observe_posted_notes(verified_userid="synthetic-userid")
            elapsed = asyncio.get_running_loop().time() - started_at

            assert attempt.observation_status is ContentActivityObservationStatus.PROVIDER_ERROR
            assert attempt.coverage_status is ContentActivityCoverageStatus.UNKNOWN
            assert attempt.activity_result is ContentActivityResult.UNDETERMINED
            assert attempt.provider_error_class is ContentActivityProviderErrorClass.TIMEOUT
            assert attempt.provider_error_code == "TRANSPORT_TIMEOUT"
            # Each 60 ms chunk beats the 150 ms socket read limit. The call must
            # still take roughly its 200 ms wall-clock budget, not all 600 ms.
            assert elapsed >= 0.15
            assert elapsed < 0.50
            assert stream.closed
            assert 0 < stream.yielded_chunks < 10

            yielded_at_timeout = stream.yielded_chunks
            await asyncio.sleep(0.20)
            assert stream.yielded_chunks == yielded_at_timeout
        finally:
            await http_client.aclose()

    asyncio.run(scenario())


def test_fast_drip_completes_inside_total_wall_clock_deadline() -> None:
    """The deadline does not reject a streamed, valid response that finishes in budget."""

    stream = DelayedByteStream(
        _drip_chunks(_fixture_body("xhs_get_user_posted_notes_success_v1.json"), count=4),
        delay_seconds=0.01,
    )

    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, request=request, stream=stream)

        http_client = httpx.AsyncClient(
            base_url="https://tikhub.test",
            transport=httpx.MockTransport(handler),
        )
        transport = HttpxAsyncTransport("https://tikhub.test", client=http_client)
        client = TikHubXhsClient(
            _enabled_settings(
                content_activity_http_connect_timeout_seconds=0.05,
                content_activity_http_read_timeout_seconds=0.15,
                content_activity_http_pool_timeout_seconds=0.05,
                content_activity_http_total_timeout_seconds=0.20,
            ),
            transport=transport,
            clock=lambda: OBSERVED_AT,
        )
        try:
            attempt = await client.observe_posted_notes(verified_userid="synthetic-userid")

            assert attempt.trusted
            assert stream.closed
            assert stream.yielded_chunks == 4
        finally:
            await http_client.aclose()

    asyncio.run(scenario())


def test_httpx_read_timeout_still_precedes_later_total_wall_clock_deadline() -> None:
    """A socket-read stall remains an HTTPX timeout when the total budget remains."""

    async def scenario() -> None:
        handler_finished = asyncio.Event()

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await reader.readuntil(b"\r\n\r\n")
                writer.write(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Transfer-Encoding: chunked\r\n"
                    b"Connection: close\r\n\r\n"
                    b"2\r\n{}\r\n"
                )
                await writer.drain()
                await asyncio.sleep(0.15)
                writer.write(b"2\r\n{}\r\n0\r\n\r\n")
                await writer.drain()
            except (ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass
                handler_finished.set()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        http_client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}")
        transport = HttpxAsyncTransport(f"http://127.0.0.1:{port}", client=http_client)
        client = TikHubXhsClient(
            _enabled_settings(
                content_activity_http_connect_timeout_seconds=0.05,
                content_activity_http_read_timeout_seconds=0.05,
                content_activity_http_pool_timeout_seconds=0.05,
                content_activity_http_total_timeout_seconds=0.30,
            ),
            transport=transport,
            clock=lambda: OBSERVED_AT,
        )
        try:
            started_at = asyncio.get_running_loop().time()
            attempt = await client.observe_posted_notes(verified_userid="synthetic-userid")
            elapsed = asyncio.get_running_loop().time() - started_at

            assert attempt.observation_status is ContentActivityObservationStatus.PROVIDER_ERROR
            assert attempt.provider_error_class is ContentActivityProviderErrorClass.TIMEOUT
            assert attempt.provider_error_code == "TRANSPORT_TIMEOUT"
            assert elapsed >= 0.04
            assert elapsed < 0.20
            await asyncio.wait_for(handler_finished.wait(), timeout=0.50)
        finally:
            await http_client.aclose()
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_external_cancellation_is_not_normalized_as_provider_timeout() -> None:
    """Only this adapter's deadline becomes TimeoutError; caller cancellation escapes."""

    stream = DelayedByteStream(
        _drip_chunks(_fixture_body("xhs_get_user_posted_notes_success_v1.json"), count=10),
        delay_seconds=0.05,
    )

    async def scenario() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, request=request, stream=stream)

        http_client = httpx.AsyncClient(
            base_url="https://tikhub.test",
            transport=httpx.MockTransport(handler),
        )
        transport = HttpxAsyncTransport("https://tikhub.test", client=http_client)
        client = TikHubXhsClient(
            _enabled_settings(
                content_activity_http_connect_timeout_seconds=0.05,
                content_activity_http_read_timeout_seconds=0.15,
                content_activity_http_pool_timeout_seconds=0.05,
                content_activity_http_total_timeout_seconds=0.50,
            ),
            transport=transport,
            clock=lambda: OBSERVED_AT,
        )
        try:
            task = asyncio.create_task(
                client.observe_posted_notes(verified_userid="synthetic-userid")
            )
            await asyncio.wait_for(stream.first_chunk_ready.wait(), timeout=0.20)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert stream.closed
        finally:
            await http_client.aclose()

    asyncio.run(scenario())


def test_httpx_transport_never_logs_provider_query_values_at_root_info_level() -> None:
    """The production logging bootstrap cannot re-enable HTTPX URL logging."""

    bootstrap_sentinel = "SENTINEL_BOOTSTRAP_MUST_NOT_LOG"
    userid_sentinel = "SENTINEL_USERID_MUST_NOT_LOG"
    root_logger = logging.getLogger()
    original_handlers = list(root_logger.handlers)
    original_level = root_logger.level
    original_logger_levels = {
        logger_name: logging.getLogger(logger_name).level for logger_name in ("httpx", "httpcore")
    }
    capture = StringIO()

    async def scenario() -> None:
        client = httpx.AsyncClient(
            base_url="https://unit-test.invalid",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request)),
        )
        transport = HttpxAsyncTransport("https://unit-test.invalid", client=client)
        try:
            for params in (
                {"share_text": bootstrap_sentinel},
                {"user_id": userid_sentinel, "cursor": ""},
            ):
                await transport.get(
                    path="/safe-test",
                    params=params,
                    headers={"Authorization": "Bearer synthetic-test-token"},
                    limits=HttpRequestLimits(
                        connect_timeout_seconds=1,
                        read_timeout_seconds=1,
                        pool_timeout_seconds=1,
                        total_timeout_seconds=1,
                        max_response_bytes=1_024,
                    ),
                )
        finally:
            await client.aclose()

    try:
        for logger_name in original_logger_levels:
            logging.getLogger(logger_name).setLevel(logging.NOTSET)
        configure_logging("INFO")
        capture_handler = logging.StreamHandler(capture)
        capture_handler.setFormatter(JsonFormatter())
        root_logger.addHandler(capture_handler)

        asyncio.run(scenario())

        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
        assert bootstrap_sentinel not in capture.getvalue()
        assert userid_sentinel not in capture.getvalue()
    finally:
        root_logger.handlers.clear()
        root_logger.handlers.extend(original_handlers)
        root_logger.setLevel(original_level)
        for logger_name, level in original_logger_levels.items():
            logging.getLogger(logger_name).setLevel(level)


def test_content_activity_settings_default_closed_and_enabled_gate_is_explicit() -> None:
    disabled = Settings(_env_file=None)
    assert not disabled.content_activity_enabled
    assert disabled.content_activity_trusted_freshness_days == 7
    assert disabled.content_activity_provider_max_calls_per_run == 0

    with pytest.raises(ValueError, match="CONTENT_ACTIVITY_ENABLED requires"):
        Settings(content_activity_enabled=True, _env_file=None)

    enabled = _enabled_settings()
    assert enabled.content_activity_xhs_max_calls_per_account == 1

    with pytest.raises(ValueError, match="TIKHUB_BASE_URL must be an HTTPS origin"):
        _enabled_settings(tikhub_base_url="https://embedded-secret@tikhub.test/path")
