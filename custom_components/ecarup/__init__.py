"""eCarUp Home Assistant integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ECarUpApi, ECarUpApiError, ECarUpAuthError
from .const import (
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    DOMAIN,
    HISTORY_BACKFILL_PAGES_PER_UPDATE,
    HISTORY_MAX_PAGES,
    HISTORY_STORE_VERSION,
    UPDATE_INTERVAL,
)

PLATFORMS = ["sensor"]
_LOGGER = logging.getLogger(__name__)


def _sanitize_history_item(item: dict) -> dict:
    """Drop driver identity and signed payload before persisting history locally."""
    station = item.get("station") or {}
    signed_transaction = item.get("signedTransaction") or {}
    return {
        "id": item.get("id"),
        "station": {
            "id": station.get("id"),
            "name": station.get("name"),
            "connectorNumber": station.get("connectorNumber"),
            "ownerId": station.get("ownerId"),
            "priceInformation": station.get("priceInformation"),
        },
        "startTime": item.get("startTime"),
        "durationS": item.get("durationS"),
        "consumptionKwh": item.get("consumptionKwh"),
        "driverCosts": item.get("driverCosts"),
        "stationOwnerEarnings": item.get("stationOwnerEarnings"),
        "signedTransaction": {"format": signed_transaction.get("format")},
        "provider": item.get("provider"),
    }


@dataclass
class ECarUpRuntimeData:
    """Runtime objects belonging to one eCarUp config entry."""

    coordinator: ECarUpCoordinator
    api: ECarUpApi


type ECarUpConfigEntry = ConfigEntry[ECarUpRuntimeData]


async def _async_get_active_chargings(
    api: ECarUpApi, station_id: str, connectors: list[dict]
) -> tuple[dict[str, dict], set[str]]:
    """Probe the active-charging endpoint for each known connector ID.

    The `/v1/stations` example does not include connector state, so state-based
    filtering can silently hide active sessions. A 404 from this endpoint means
    no active session for that connector.
    """
    active: dict[str, dict] = {}
    unavailable: set[str] = set()
    for connector in connectors:
        connector_id = str(connector.get("id", ""))
        if not connector_id:
            continue
        try:
            charging = await api.async_get_active_charging(station_id, connector_id)
        except ECarUpAuthError:
            raise
        except ECarUpApiError as err:
            # Keep station and connector state available when this optional
            # detail endpoint is temporarily unavailable.
            _LOGGER.debug(
                "Unable to fetch active charging details for station %s connector %s: %s",
                station_id,
                connector_id,
                err,
            )
            unavailable.add(connector_id)
            continue
        if charging and charging.get("status") in {"ACTIVE", "DEACTIVATION_PENDING"}:
            active[connector_id] = charging
    return active, unavailable


class ECarUpCoordinator(DataUpdateCoordinator[dict[str, dict]]):
    """Fetch stations, history, and currently active sessions."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: ECarUpApi,
        history_store: Store[dict[str, dict]],
        history_cache: dict[str, dict],
        config_entry: ECarUpConfigEntry,
    ) -> None:
        super().__init__(
            hass,
            logger=_LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=UPDATE_INTERVAL,
            always_update=False,
        )
        self.api = api
        self._history_store = history_store
        self._history_cache = history_cache

    async def _async_update_history(self, station_id: str) -> dict:
        """Continue a bounded initial backfill or fetch just the newest delta."""
        cached = self._history_cache.get(station_id, {})
        histories = list(cached.get("histories", []))
        known_ids = {str(item.get("id")) for item in histories if item.get("id")}
        cursor = cached.get("cursor")
        pages_fetched = int(cached.get("pages_fetched", 0))
        backfill_complete = bool(cached.get("backfill_complete", False))
        truncated = bool(cached.get("truncated", False))

        if backfill_complete:
            new_histories, truncated = await self.api.async_get_history_delta(
                station_id, known_ids
            )
            for item in new_histories:
                item_id = item.get("id")
                if item_id is None or str(item_id) not in known_ids:
                    safe_item = _sanitize_history_item(item)
                    histories.append(safe_item)
                    if item_id is not None:
                        known_ids.add(str(item_id))
        else:
            for _ in range(HISTORY_BACKFILL_PAGES_PER_UPDATE):
                if pages_fetched >= HISTORY_MAX_PAGES:
                    truncated = bool(cursor)
                    backfill_complete = True
                    break
                page, cursor = await self.api.async_get_history_page(station_id, cursor)
                pages_fetched += 1
                for item in page:
                    item_id = item.get("id")
                    if item_id is None or str(item_id) not in known_ids:
                        histories.append(_sanitize_history_item(item))
                        if item_id is not None:
                            known_ids.add(str(item_id))
                if not cursor:
                    backfill_complete = True
                    break
            if pages_fetched >= HISTORY_MAX_PAGES and cursor:
                truncated = True
                backfill_complete = True

        histories.sort(key=lambda item: item.get("startTime") or "", reverse=True)
        updated = {
            "histories": histories,
            "cursor": cursor,
            "pages_fetched": pages_fetched,
            "backfill_complete": backfill_complete,
            "truncated": truncated,
        }
        self._history_cache[station_id] = updated
        return updated

    async def _async_update_data(self) -> dict[str, dict]:
        try:
            stations = await self.api.async_get_stations()
            result: dict[str, dict] = {}
            for station in stations:
                station_id = str(station.get("id", ""))
                if not station_id:
                    continue
                try:
                    station = await self.api.async_get_station(station_id)
                except ECarUpAuthError:
                    raise
                except ECarUpApiError as err:
                    _LOGGER.debug(
                        "Unable to fetch station details for station %s: %s",
                        station_id,
                        err,
                    )
                connectors = station.get("connectors", [])
                history_data = await self._async_update_history(station_id)
                active, active_unavailable = await _async_get_active_chargings(
                    self.api, station_id, connectors
                )
                result[station_id] = {
                    "station": station,
                    "history": history_data["histories"],
                    "history_has_more": history_data["truncated"],
                    "history_backfill_complete": history_data["backfill_complete"],
                    "active": active,
                    "active_unavailable": active_unavailable,
                }
            await self._history_store.async_save(self._history_cache)
            return result
        except ECarUpAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ECarUpApiError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
            ) from err


async def async_setup_entry(hass: HomeAssistant, entry: ECarUpConfigEntry) -> bool:
    """Set up eCarUp from a config entry."""
    api = ECarUpApi(
        async_get_clientsession(hass),
        entry.data[CONF_CLIENT_ID],
        entry.data[CONF_CLIENT_SECRET],
    )
    history_store: Store[dict[str, dict]] = Store(
        hass, HISTORY_STORE_VERSION, f"{DOMAIN}_{entry.entry_id}_history"
    )
    history_cache = await history_store.async_load() or {}
    coordinator = ECarUpCoordinator(hass, api, history_store, history_cache, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = ECarUpRuntimeData(coordinator=coordinator, api=api)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ECarUpConfigEntry) -> bool:
    """Unload an eCarUp config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ECarUpConfigEntry) -> None:
    """Reload the integration after an options change."""
    await hass.config_entries.async_reload(entry.entry_id)
