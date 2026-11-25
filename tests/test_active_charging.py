"""Tests for active session discovery based on connector IDs."""

import pytest

from custom_components.ecarup import _async_get_active_chargings, _sanitize_history_item
from custom_components.ecarup.sensor import _connector_status


class ActiveChargingApi:
    """Minimal fake active-charging API."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def async_get_active_charging(self, station_id: str, connector_id: str):
        self.calls.append((station_id, connector_id))
        if connector_id == "connector-active":
            return {
                "id": "9ea156d3-f95b-49fa-ba89-bfa56107d282",
                "driverIdentifier": "private-driver-id",
                "status": "ACTIVE",
                "startTime": "2026-11-24T16:33:34.241231Z",
                "startMeterValueWh": 1875635,
                "price": {
                    "currency": "EUR",
                    "consumptionPerKwh": 0.95,
                    "timePerH": 0,
                },
                "transaction": {
                    "transactionId": "538014247",
                    "meterStartWh": 9083089,
                    "meterActivePowerW": 1.989,
                    "meterActiveEnergyWh": 9080464,
                },
                "stationGroups": [],
            }
        return None


@pytest.mark.asyncio
async def test_active_sessions_probe_every_known_connector_even_without_state() -> None:
    api = ActiveChargingApi()
    station_id = "station-id"
    connectors = [
        {"id": "connector-active", "number": 1},
        {"id": "connector-idle", "number": 2},
    ]

    active, unavailable = await _async_get_active_chargings(api, station_id, connectors)

    assert api.calls == [
        (station_id, "connector-active"),
        (station_id, "connector-idle"),
    ]
    assert active["connector-active"]["status"] == "ACTIVE"
    assert active["connector-active"]["transaction"]["meterActivePowerW"] == 1.989
    assert unavailable == set()


@pytest.mark.asyncio
async def test_active_session_api_error_is_reported_without_losing_other_connectors() -> None:
    class PartiallyFailingApi(ActiveChargingApi):
        async def async_get_active_charging(self, station_id: str, connector_id: str):
            if connector_id == "connector-failing":
                from custom_components.ecarup.api import ECarUpConnectionError

                raise ECarUpConnectionError("temporary API failure")
            return await super().async_get_active_charging(station_id, connector_id)

    api = PartiallyFailingApi()
    active, unavailable = await _async_get_active_chargings(
        api,
        "station-id",
        [{"id": "connector-active"}, {"id": "connector-failing"}],
    )

    assert "connector-active" in active
    assert unavailable == {"connector-failing"}


def test_connector_id_from_stations_response_is_used_for_active_endpoint() -> None:
    station = {
        "id": "sample-station-id",
        "connectors": [
            {"id": "sample-station-id", "number": 1}
        ],
    }
    station_id = station["id"]
    connector_id = station["connectors"][0]["id"]

    assert (
        f"/v1/station/{station_id}/connectors/{connector_id}/active-charging"
        == "/v1/station/sample-station-id/connectors/"
        "sample-station-id/active-charging"
    )


def test_history_cache_strips_private_and_large_payload_fields() -> None:
    item = {
        "id": "charge-id",
        "driverIdentifier": "private-driver-id",
        "signedTransaction": {"format": "OCMF", "data": "large-signed-payload"},
        "station": {"id": "station-id", "name": "Station"},
        "consumptionKwh": 12.5,
    }

    cached = _sanitize_history_item(item)

    assert "driverIdentifier" not in cached
    assert cached["signedTransaction"] == {"format": "OCMF"}
    assert cached["consumptionKwh"] == 12.5


def test_connector_status_sensor_exposes_raw_api_state() -> None:
    assert _connector_status({"state": "CHARGING"}) == "CHARGING"
    assert _connector_status({"state": "AVAILABLE"}) == "AVAILABLE"
    assert _connector_status({"state": "PREPARING"}) == "PREPARING"
    assert _connector_status({}) is None
