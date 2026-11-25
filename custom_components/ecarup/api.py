"""Async client for eCarUp's OAuth-protected REST API."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from aiohttp import ClientError, ClientSession

from .const import (
    API_BASE_URL,
    HISTORY_DELTA_MAX_PAGES,
    HISTORY_MAX_PAGES,
    OAUTH_SCOPE,
    TOKEN_URL,
)


class ECarUpApiError(Exception):
    """Base error for API communication failures."""


class ECarUpAuthError(ECarUpApiError):
    """OAuth credentials were rejected."""


class ECarUpConnectionError(ECarUpApiError):
    """The API could not be reached or returned an invalid response."""


class ECarUpApi:
    """Minimal API client with cached client-credentials access token."""

    def __init__(self, session: ClientSession, client_id: str, client_secret: str) -> None:
        self._session = session
        self._client_id = client_id
        self._client_secret = client_secret
        self._access_token: str | None = None
        self._token_expires_at = datetime.now().astimezone()
        self._token_lock = asyncio.Lock()
        # The API's edge protection rejects aiohttp's default Python user-agent
        # (HTTP 403 / Error 1010); a browser-like UA is accepted consistently.
        self._headers = {
            "Accept": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        }

    @property
    def authenticated(self) -> bool:
        """Return whether an access token is currently cached."""
        return self._access_token is not None

    async def async_validate(self) -> None:
        """Validate credentials by requesting the station list."""
        await self.async_get("/v1/stations")

    async def async_get_stations(self) -> list[dict[str, Any]]:
        response = await self.async_get("/v1/stations")
        return response.get("stations", [])

    async def async_get_station(self, station_id: str) -> dict[str, Any]:
        """Get one station's details."""
        return await self.async_get(f"/v1/station/{quote(station_id, safe='')}")

    async def async_get_history(self, station_id: str) -> list[dict[str, Any]]:
        histories, _has_more = await self.async_get_history_with_status(station_id)
        return histories

    async def async_get_history_with_status(
        self, station_id: str
    ) -> tuple[list[dict[str, Any]], bool]:
        """Get history and report whether the page safety limit truncated it."""
        histories: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _page in range(HISTORY_MAX_PAGES):
            params = {"paginationCursor": cursor} if cursor else None
            response = await self.async_get(
                f"/v1/history/station/{quote(station_id, safe='')}", params=params
            )
            histories.extend(response.get("histories", []))
            next_cursor = response.get("paginationCursor")
            if not next_cursor:
                break
            if next_cursor in seen_cursors:
                return histories, True
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            return histories, bool(cursor)
        return histories, False

    async def async_get_history_page(
        self, station_id: str, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Fetch a single history page and its next opaque cursor."""
        params = {"paginationCursor": cursor} if cursor else None
        response = await self.async_get(
            f"/v1/history/station/{quote(station_id, safe='')}", params=params
        )
        return response.get("histories", []), response.get("paginationCursor")

    async def async_get_driver_history_page(
        self, cursor: str | None = None
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Get one page of the authenticated user's charging history."""
        params = {"paginationCursor": cursor} if cursor else None
        response = await self.async_get("/v1/history/driver", params=params)
        return response.get("histories", []), response.get("paginationCursor")

    async def async_get_history_delta(
        self, station_id: str, known_ids: set[str]
    ) -> tuple[list[dict[str, Any]], bool]:
        """Fetch newest sessions until an already-cached session is reached."""
        new_histories: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _page in range(HISTORY_DELTA_MAX_PAGES):
            histories, next_cursor = await self.async_get_history_page(station_id, cursor)
            known_found = False
            for item in histories:
                item_id = item.get("id")
                if item_id is not None and str(item_id) in known_ids:
                    known_found = True
                    break
                else:
                    new_histories.append(item)
            if known_found or not next_cursor:
                return new_histories, False
            if next_cursor in seen_cursors:
                return new_histories, True
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return new_histories, bool(cursor)

    async def async_get_active_charging(
        self, station_id: str, connector_id: str
    ) -> dict[str, Any] | None:
        response = await self.async_get(
            f"/v1/station/{quote(station_id, safe='')}/connectors/"
            f"{quote(connector_id, safe='')}/active-charging",
            not_found_ok=True,
        )
        return response if response.get("id") else None

    async def async_get(
        self,
        path: str,
        *,
        not_found_ok: bool = False,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        token = await self._async_get_token()
        try:
            async with self._session.get(
                f"{API_BASE_URL}{path}",
                headers={**self._headers, "Authorization": f"Bearer {token}"},
                params=params,
            ) as response:
                if response.status == 401:
                    self._access_token = None
                    token = await self._async_get_token()
                    async with self._session.get(
                        f"{API_BASE_URL}{path}",
                        headers={**self._headers, "Authorization": f"Bearer {token}"},
                        params=params,
                    ) as retry:
                        if not_found_ok and retry.status == 404:
                            return {}
                        return await self._async_read_response(retry)
                if not_found_ok and response.status == 404:
                    return {}
                return await self._async_read_response(response)
        except ClientError as err:
            raise ECarUpConnectionError("Unable to connect to eCarUp API") from err

    async def _async_get_token(self) -> str:
        async with self._token_lock:
            if self._access_token and datetime.now().astimezone() < self._token_expires_at:
                return self._access_token

            try:
                async with self._session.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                        "scope": OAUTH_SCOPE,
                    },
                    headers={"Accept": "application/json"},
                ) as response:
                    if response.status in (400, 401, 403):
                        raise ECarUpAuthError("OAuth credentials were rejected")
                    if response.status >= 400:
                        raise ECarUpConnectionError(
                            f"OAuth server returned HTTP {response.status}"
                        )
                    payload = await response.json(content_type=None)
            except ClientError as err:
                raise ECarUpConnectionError("Unable to connect to OAuth server") from err
            except ValueError as err:
                raise ECarUpConnectionError("OAuth server returned invalid JSON") from err

            access_token = payload.get("access_token")
            if not access_token:
                raise ECarUpAuthError("OAuth response did not contain an access token")

            expires_in = int(payload.get("expires_in", 3600))
            self._access_token = access_token
            self._token_expires_at = datetime.now().astimezone() + timedelta(
                seconds=max(0, expires_in - 60)
            )
            return access_token

    @staticmethod
    async def _async_read_response(response: Any) -> dict[str, Any]:
        if response.status == 401:
            raise ECarUpAuthError("API rejected the access token")
        if response.status >= 400:
            raise ECarUpConnectionError(f"eCarUp API returned HTTP {response.status}")
        try:
            payload = await response.json(content_type=None)
        except (ValueError, ClientError) as err:
            raise ECarUpConnectionError("eCarUp API returned invalid JSON") from err
        if not isinstance(payload, dict):
            raise ECarUpConnectionError("eCarUp API returned an unexpected response")
        return payload
