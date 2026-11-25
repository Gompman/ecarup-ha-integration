"""Small unit tests for integration data conversions."""

import json
from pathlib import Path

from custom_components.ecarup.diagnostics import _diagnostic_station_data
from custom_components.ecarup.sensor import (
    _connector_attributes,
    _energy_source,
    _history_item,
    _station_total_energy_kwh,
    _sum_active_power_kw,
    _sum_history_energy_kwh,
    _sum_import_energy_kwh,
    _sum_owner_earnings,
    _wh_to_kwh,
)


def test_wh_to_kwh() -> None:
    assert _wh_to_kwh(1234) == 1.234
    assert _wh_to_kwh(None) is None


def test_connector_attributes_keep_details_compact_and_live_units_correct() -> None:
    result = _connector_attributes(
        {
            "number": 1,
            "plugtype": "PLUG_TYPE_TYPE2",
            "maxpower": 1800,
            "state": "CHARGING",
            "activePowerTotal": 1.805,
            "access": {"type": "PUBLIC", "specialUsers": [{"email": "private"}]},
        }
    )

    assert result == {
        "active_power_kw": 1.805,
        "connector_number": 1,
        "plug_type": "PLUG_TYPE_TYPE2",
        "max_power_w": 1800,
        "access_type": "PUBLIC",
    }
    assert "specialUsers" not in result


def test_history_item_does_not_expose_driver_identifier() -> None:
    item = {
        "id": "charge-1",
        "driverIdentifier": "private@example.test",
        "signedTransaction": {"format": "OCMF", "data": "large-payload"},
        "station": {"name": "Garage", "connectorNumber": 1},
        "consumptionKwh": 12.5,
    }

    result = _history_item(item)

    assert result["station_name"] == "Garage"
    assert result["consumption_kwh"] == 12.5
    assert result["signed_transaction_format"] == "OCMF"
    assert "driverIdentifier" not in result
    assert "data" not in result


def test_sum_import_energy_converts_wh_to_kwh() -> None:
    connectors = [
        {"activeEnergyImport": 1500},
        {"activeEnergyImport": 2250},
        {"activeEnergyImport": 0},
    ]

    assert _sum_import_energy_kwh(connectors) == 3.75
    assert _sum_import_energy_kwh([{"activeEnergyImport": None}]) is None
    assert _sum_import_energy_kwh(
        [{"activeEnergyImport": 1500}, {"activeEnergyImport": None}]
    ) is None


def test_sum_active_power_keeps_missing_values_unknown() -> None:
    assert _sum_active_power_kw([{"activePowerTotal": 1.805}, {"activePowerTotal": 0.2}]) == 2.005
    assert _sum_active_power_kw([{"activePowerTotal": None}]) is None
    assert _sum_active_power_kw([]) is None


def test_diagnostic_station_data_excludes_coordinates_and_history_payloads() -> None:
    result = _diagnostic_station_data(
        {
            "station-1": {
                "station": {
                    "id": "station-1",
                    "name": "Home charger",
                    "latitude": 1.0,
                    "longitude": 2.0,
                },
                "active": {},
                "history": [{"driverIdentifier": "private", "id": "charge-1"}],
                "history_has_more": False,
                "history_backfill_complete": True,
            }
        }
    )

    assert "latitude" not in result["station-1"]["station"]
    assert "longitude" not in result["station-1"]["station"]
    assert result["station-1"]["history_count"] == 1
    assert "history" not in result["station-1"]


def test_sum_history_energy_kwh() -> None:
    histories = [
        {"consumptionKwh": 8.25},
        {"consumptionKwh": 3.5},
        {"consumptionKwh": None},
    ]

    assert _sum_history_energy_kwh(histories) == 11.75
    assert _sum_history_energy_kwh([{"consumptionKwh": None}]) is None
    assert _sum_history_energy_kwh([]) == 0


def test_station_energy_prefers_meter_and_falls_back_to_history() -> None:
    histories = [{"consumptionKwh": 4.25}]

    assert _station_total_energy_kwh([{"activeEnergyImport": 12345}], histories) == 12.345
    assert _station_total_energy_kwh([{"activeEnergyImport": None}], histories) == 4.25
    assert _station_total_energy_kwh([{"activeEnergyImport": 2000}], histories) == 4.25
    assert _energy_source(12.345, 4.25) == "connector_meter"
    assert _energy_source(None, 4.25) == "charging_history"
    assert _energy_source(None, None) == "unavailable"


def test_sum_owner_earnings_by_currency() -> None:
    histories = [
        {"stationOwnerEarnings": {"currency": "CHF", "energy": 2.1, "time": 0.5}},
        {"stationOwnerEarnings": {"currency": "CHF", "energy": 3.4, "penalty": 0.1}},
        {"stationOwnerEarnings": {"currency": "EUR", "energy": 4}},
    ]

    assert _sum_owner_earnings(histories) == {"CHF": 6.1, "EUR": 4.0}


def test_example_ecarup_history_response_totals() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "charging_history_sample.json"
    response = json.loads(fixture_path.read_text())
    histories = response["histories"]

    assert len(histories) == 5
    assert _sum_history_energy_kwh(histories) == 99.0
    assert _sum_owner_earnings(histories) == {"EUR": 39.6}
    assert {item["provider"] for item in histories} == {"CHARGING_PROVIDER_HUBJECT"}
    assert histories[0]["station"]["priceInformation"]["pricePerKwh"] == 0.4
