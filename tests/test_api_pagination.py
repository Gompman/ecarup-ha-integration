"""Pagination behavior based on the captured eCarUp response shape."""

from typing import Any

import pytest

from custom_components.ecarup.api import ECarUpApi


class PaginatedApi(ECarUpApi):
    """Fake the endpoint responses while exercising the real cursor loop."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        super().__init__(session=None, client_id="unused", client_secret="unused")
        self.responses = iter(responses)
        self.requests: list[dict[str, str] | None] = []
        self.paths: list[str] = []

    async def async_get(
        self,
        path: str,
        *,
        not_found_ok: bool = False,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.paths.append(path)
        self.requests.append(params)
        return next(self.responses)


@pytest.mark.asyncio
async def test_history_follows_pagination_cursor_until_last_page() -> None:
    api = PaginatedApi(
        [
            {"histories": [{"id": "first"}], "paginationCursor": "cursor-1"},
            {"histories": [{"id": "second"}]},
        ]
    )

    result = await api.async_get_history("station-1")

    assert [item["id"] for item in result] == ["first", "second"]
    assert api.requests == [None, {"paginationCursor": "cursor-1"}]


@pytest.mark.asyncio
async def test_history_stops_if_cursor_repeats() -> None:
    api = PaginatedApi(
        [
            {"histories": [{"id": "first"}], "paginationCursor": "cursor-1"},
            {"histories": [{"id": "second"}], "paginationCursor": "cursor-1"},
        ]
    )

    result = await api.async_get_history("station-1")

    assert [item["id"] for item in result] == ["first", "second"]
    assert len(api.requests) == 2


@pytest.mark.asyncio
async def test_history_reports_when_page_limit_truncates_response(monkeypatch) -> None:
    import custom_components.ecarup.api as api_module

    monkeypatch.setattr(api_module, "HISTORY_MAX_PAGES", 2)
    api = PaginatedApi(
        [
            {"histories": [{"id": "first"}], "paginationCursor": "cursor-1"},
            {"histories": [{"id": "second"}], "paginationCursor": "cursor-2"},
        ]
    )

    histories, truncated = await api.async_get_history_with_status("station-1")

    assert [item["id"] for item in histories] == ["first", "second"]
    assert truncated is True
    assert len(api.requests) == 2


@pytest.mark.asyncio
async def test_history_delta_stops_at_first_known_session() -> None:
    api = PaginatedApi(
        [
            {
                "histories": [{"id": "new-2"}, {"id": "new-1"}, {"id": "cached"}],
                "paginationCursor": "older-page",
            },
            {"histories": [{"id": "older"}]},
        ]
    )

    result, truncated = await api.async_get_history_delta("station-1", {"cached"})

    assert [item["id"] for item in result] == ["new-2", "new-1"]
    assert truncated is False
    assert api.requests == [None]


@pytest.mark.asyncio
async def test_driver_history_page_uses_driver_endpoint_and_cursor() -> None:
    api = PaginatedApi(
        [
            {"histories": [{"id": "first"}], "paginationCursor": "driver-next"},
            {"histories": [{"id": "second"}]},
        ]
    )

    first_page, cursor = await api.async_get_driver_history_page()
    second_page, next_cursor = await api.async_get_driver_history_page(cursor)

    assert [item["id"] for item in first_page + second_page] == ["first", "second"]
    assert next_cursor is None
    assert api.paths == ["/v1/history/driver", "/v1/history/driver"]
    assert api.requests == [None, {"paginationCursor": "driver-next"}]
