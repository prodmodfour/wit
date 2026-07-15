"""Mocked contracts for complete, read-only Sonarr queue inspection."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import SecretStr

from wit.clients import (
    InvalidSonarrResponseError,
    SonarrClient,
    SonarrQueueItem,
    SonarrQueueState,
)
from wit.transport import HttpStatusError

_CREDENTIAL = "sonarr-queue-" + ("x" * 24)
_PRIVATE_RESPONSE_VALUE = "private-upstream-queue-value"
_PAGE_SIZE = 100


def _queue_record(
    queue_id: int,
    *,
    series_id: int | None = 42,
    episode_id: int | None = 101,
    status: str = "queued",
    tracked_download_status: str | None = "ok",
    tracked_download_state: str | None = "downloading",
) -> dict[str, object]:
    return {
        "id": queue_id,
        "seriesId": series_id,
        "episodeId": episode_id,
        "status": status,
        "trackedDownloadStatus": tracked_download_status,
        "trackedDownloadState": tracked_download_state,
        "title": "Release title not retained by Wit",
        "outputPath": "/private/download/path",
        "errorMessage": _PRIVATE_RESPONSE_VALUE,
        "statusMessages": [
            {
                "title": _PRIVATE_RESPONSE_VALUE,
                "messages": [_PRIVATE_RESPONSE_VALUE],
            }
        ],
    }


def _queue_page(
    page: int,
    records: list[dict[str, object]],
    *,
    total_records: int,
    page_size: int = _PAGE_SIZE,
) -> dict[str, object]:
    return {
        "page": page,
        "pageSize": page_size,
        "sortKey": "timeleft",
        "sortDirection": "ascending",
        "totalRecords": total_records,
        "records": records,
    }


def test_retrieves_every_paginated_queue_record_without_truncation() -> None:
    requests: list[httpx.Request] = []
    first_page = [
        _queue_record(queue_id, episode_id=1_000 + queue_id)
        for queue_id in range(1, _PAGE_SIZE + 1)
    ]
    second_page = [_queue_record(_PAGE_SIZE + 1, episode_id=2_001)]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == "/sonarr/api/v3/queue"
        assert request.headers["X-Api-Key"] == _CREDENTIAL
        assert request.content == b""
        assert request.url.params["pageSize"] == str(_PAGE_SIZE)
        assert request.url.params["includeUnknownSeriesItems"] == "true"

        page = int(request.url.params["page"])
        if page == 1:
            return httpx.Response(
                200,
                json=_queue_page(1, first_page, total_records=_PAGE_SIZE + 1),
            )
        if page == 2:
            return httpx.Response(
                200,
                json=_queue_page(2, second_page, total_records=_PAGE_SIZE + 1),
            )
        raise AssertionError(f"unexpected queue page: {page}")

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test/sonarr",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            queue = await client.list_queue()

        assert len(queue) == _PAGE_SIZE + 1
        assert queue[0] == SonarrQueueItem(
            queue_id=1,
            series_id=42,
            episode_id=1_001,
            state=SonarrQueueState.QUEUED,
        )
        assert queue[-1] == SonarrQueueItem(
            queue_id=_PAGE_SIZE + 1,
            series_id=42,
            episode_id=2_001,
            state=SonarrQueueState.QUEUED,
        )
        assert _PRIVATE_RESPONSE_VALUE not in repr(queue)
        assert _CREDENTIAL not in repr(queue)

    asyncio.run(scenario())
    assert [request.url.params["page"] for request in requests] == ["1", "2"]


def test_normalises_mixed_download_and_import_states() -> None:
    records = [
        _queue_record(1, episode_id=101, status="queued"),
        _queue_record(2, episode_id=102, status="paused"),
        _queue_record(3, episode_id=103, status="downloading"),
        _queue_record(
            4,
            episode_id=104,
            status="completed",
            tracked_download_state="importing",
        ),
        _queue_record(
            5,
            episode_id=105,
            status="completed",
            tracked_download_status="warning",
            tracked_download_state="importBlocked",
        ),
        _queue_record(6, episode_id=106, status="warning"),
        _queue_record(7, episode_id=107, status="failed"),
        _queue_record(
            8,
            episode_id=108,
            status="completed",
            tracked_download_status="error",
            tracked_download_state="failedPending",
        ),
    ]

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json=_queue_page(1, records, total_records=len(records)),
                )
            ),
        ) as client:
            queue = await client.list_queue()

        assert tuple(item.state for item in queue) == (
            SonarrQueueState.QUEUED,
            SonarrQueueState.QUEUED,
            SonarrQueueState.DOWNLOADING,
            SonarrQueueState.IMPORTING,
            SonarrQueueState.WARNING,
            SonarrQueueState.WARNING,
            SonarrQueueState.FAILED,
            SonarrQueueState.FAILED,
        )

    asyncio.run(scenario())


def test_preserves_available_series_and_episode_ids_and_allows_missing_ids() -> None:
    records = [
        _queue_record(1, series_id=42, episode_id=101),
        _queue_record(2, series_id=42, episode_id=None),
        _queue_record(
            3,
            series_id=None,
            episode_id=None,
            status="delay",
            tracked_download_status=None,
            tracked_download_state=None,
        ),
    ]
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=_queue_page(1, records, total_records=len(records)),
        )

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            queue = await client.list_queue()

        assert [(item.series_id, item.episode_id) for item in queue] == [
            (42, 101),
            (42, None),
            (None, None),
        ]
        assert queue[0].model_dump() == {
            "queue_id": 1,
            "series_id": 42,
            "episode_id": 101,
            "state": SonarrQueueState.QUEUED,
        }
        assert queue[2].state is SonarrQueueState.QUEUED

    asyncio.run(scenario())
    assert len(requests) == 1
    assert requests[0].url.params["includeUnknownSeriesItems"] == "true"


def test_propagates_a_redacted_api_failure_on_a_later_page() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = int(request.url.params["page"])
        if page == 1:
            return httpx.Response(
                200,
                json=_queue_page(
                    1,
                    [_queue_record(1)],
                    total_records=2,
                    page_size=1,
                ),
            )
        return httpx.Response(503, json={"message": _PRIVATE_RESPONSE_VALUE})

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            await client.list_queue()

    with pytest.raises(HttpStatusError) as captured:
        asyncio.run(scenario())

    assert str(captured.value) == "Sonarr returned HTTP status 503"
    assert _PRIVATE_RESPONSE_VALUE not in str(captured.value)
    assert _CREDENTIAL not in str(captured.value)
    assert [request.url.params["page"] for request in requests] == ["1", "2"]


def test_rejects_incomplete_pagination_instead_of_returning_partial_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        records = [_queue_record(1)] if page == 1 else []
        return httpx.Response(
            200,
            json=_queue_page(page, records, total_records=2, page_size=1),
        )

    async def scenario() -> None:
        async with SonarrClient(
            base_url="https://sonarr.example.test",
            api_key=SecretStr(_CREDENTIAL),
            http_transport=httpx.MockTransport(handler),
        ) as client:
            await client.list_queue()

    with pytest.raises(InvalidSonarrResponseError) as captured:
        asyncio.run(scenario())

    assert str(captured.value) == "Sonarr returned an invalid queue response"
    assert _PRIVATE_RESPONSE_VALUE not in str(captured.value)
    assert _CREDENTIAL not in str(captured.value)
    assert captured.value.__cause__ is None
