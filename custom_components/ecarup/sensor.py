"""Sensors for eCarUp stations and charging history."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ECarUpConfigEntry, ECarUpCoordinator
from .const import DOMAIN

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass, entry: ECarUpConfigEntry, async_add_entities
) -> None:
    coordinator: ECarUpCoordinator = entry.runtime_data.coordinator
    known_entity_ids: set[str] = set()

    @callback
    def _add_new_entities() -> None:
        entities = _build_entities(coordinator)
        new_entities = [
            entity for entity in entities if entity.unique_id not in known_entity_ids
        ]
        if new_entities:
            known_entity_ids.update(entity.unique_id for entity in new_entities)
            async_add_entities(new_entities)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


def _build_entities(coordinator: ECarUpCoordinator) -> list[Entity]:
    """Build station and connector entities for the current API snapshot."""
    entities: list[Entity] = []
    for station_id, data in coordinator.data.items():
        station = data["station"]
        entities.extend(
            [
                ECarUpEnergySensor(coordinator, station_id, station),
                ECarUpRevenueSensor(coordinator, station_id, station),
                ECarUpLastChargingSensor(coordinator, station_id, station),
            ]
        )
        for connector in station.get("connectors", []):
            connector_id = str(connector.get("id", ""))
            if connector_id:
                entities.append(
                    ECarUpConnectorStatusSensor(
                        coordinator, station_id, station, connector
                    )
                )
    return entities


class ECarUpStationEntity(CoordinatorEntity[ECarUpCoordinator], SensorEntity):
    """Base entity associated with one station."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: ECarUpCoordinator, station_id: str, station: dict[str, Any]
    ) -> None:
        super().__init__(coordinator)
        self.station_id = station_id
        self.station_name = station.get("name") or station_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, station_id)},
            name=self.station_name,
            manufacturer="eCarUp",
            model="Charging station",
        )

    @property
    def available(self) -> bool:
        """Unavailable when this station disappeared from the latest response."""
        return super().available and self.station_id in self.coordinator.data


class ECarUpEnergySensor(ECarUpStationEntity):
    """Sum the import energy counters of all station connectors."""

    entity_description = SensorEntityDescription(
        key="energy_total",
        translation_key="energy_total",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
    )
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL

    @property
    def unique_id(self) -> str:
        return f"{self.station_id}_energy_total"

    @property
    def native_value(self) -> float | None:
        station_data = self.coordinator.data[self.station_id]
        connectors = station_data["station"].get("connectors", [])
        return _station_total_energy_kwh(connectors, station_data["history"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        station_data = self.coordinator.data[self.station_id]
        connectors = station_data["station"].get("connectors", [])
        meter_total = _sum_import_energy_kwh(connectors)
        history_total = _sum_history_energy_kwh(station_data["history"])
        source = _energy_source(meter_total, history_total)
        return {
            "station_id": self.station_id,
            "serial_number": station_data["station"].get("serialnumber"),
            "connector_count": len(connectors),
            "energy_source": source,
            "connector_energy_total_kwh": meter_total,
            "history_energy_total_kwh": history_total,
            "history_entries": len(station_data["history"]),
            "station_groups": station_data["station"].get("stationGroups", []),
            "history_truncated": station_data.get("history_has_more", False),
            "history_backfill_complete": station_data.get(
                "history_backfill_complete", False
            ),
            "total_is_lower_bound": station_data.get("history_has_more", False)
            and source == "charging_history",
            "connector_energy_wh": {
                connector.get("name") or connector.get("id"): connector.get(
                    "activeEnergyImport"
                )
                for connector in connectors
            },
            "active_power_kw": _sum_active_power_kw(connectors),
        }


class ECarUpRevenueSensor(ECarUpStationEntity):
    """Sum station-owner earnings from all charging-history pages."""

    _attr_translation_key = "revenue_total"

    @property
    def unique_id(self) -> str:
        return f"{self.station_id}_revenue_total"

    @property
    def _earnings_by_currency(self) -> dict[str, float]:
        return _sum_owner_earnings(self.coordinator.data[self.station_id]["history"])

    @property
    def native_unit_of_measurement(self) -> str | None:
        currencies = self._earnings_by_currency
        return next(iter(currencies)) if len(currencies) == 1 else None

    @property
    def device_class(self) -> SensorDeviceClass | None:
        if len(self._earnings_by_currency) == 1:
            return SensorDeviceClass.MONETARY
        return None

    @property
    def native_value(self) -> float | None:
        currencies = self._earnings_by_currency
        if len(currencies) != 1:
            return None
        return next(iter(currencies.values()))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "earnings_by_currency": self._earnings_by_currency,
            "charging_count": len(self.coordinator.data[self.station_id]["history"]),
            "note": "Sum of station-owner energy, time, and penalty earnings in API history",
        }


class ECarUpLastChargingSensor(ECarUpStationEntity):
    """Expose the newest charging session's energy and details."""

    _attr_translation_key = "last_charging"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    @property
    def unique_id(self) -> str:
        return f"{self.station_id}_last_charging"

    @property
    def _latest(self) -> dict[str, Any] | None:
        histories = self.coordinator.data[self.station_id]["history"]
        if not histories:
            return None
        return max(histories, key=lambda item: item.get("startTime") or "")

    @property
    def native_value(self) -> float | None:
        latest = self._latest
        if latest is None or latest.get("consumptionKwh") is None:
            return None
        return float(latest["consumptionKwh"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        latest = self._latest
        if latest is None:
            return {"recent_sessions": []}
        station = latest.get("station") or {}
        return {
            "charging_id": latest.get("id"),
            "start_time": latest.get("startTime"),
            "duration_s": latest.get("durationS"),
            "connector_number": station.get("connectorNumber"),
            "price_information": station.get("priceInformation"),
            "driver_costs": latest.get("driverCosts"),
            "station_owner_earnings": latest.get("stationOwnerEarnings"),
            "provider": latest.get("provider"),
            "signed_transaction_format": (latest.get("signedTransaction") or {}).get(
                "format"
            ),
            "recent_sessions": [
                _history_item(item)
                for item in self.coordinator.data[self.station_id]["history"][:10]
            ],
        }


class ECarUpConnectorStatusSensor(ECarUpStationEntity):
    """Expose the raw eCarUp connector status as a text sensor."""

    def __init__(
        self,
        coordinator: ECarUpCoordinator,
        station_id: str,
        station: dict[str, Any],
        connector: dict[str, Any],
    ) -> None:
        super().__init__(coordinator, station_id, station)
        self.station_id = station_id
        self.connector_id = str(connector["id"])
        connector_number = connector.get("number")
        connector_name = connector.get("name") or (
            f"Connector {connector_number}" if connector_number is not None else "Connector"
        )
        self.entity_description = SensorEntityDescription(
            key="connector_status",
            translation_key="connector_status",
        )
        self._attr_translation_placeholders = {"connector": connector_name}
        self._attr_unique_id = f"{station_id}_{self.connector_id}_status"

    @property
    def available(self) -> bool:
        """A removed connector is unavailable until it reappears in the API."""
        if not super().available:
            return False
        connectors = self.coordinator.data[self.station_id]["station"].get(
            "connectors", []
        )
        return any(str(item.get("id")) == self.connector_id for item in connectors)

    @property
    def _connector(self) -> dict[str, Any]:
        connectors = self.coordinator.data[self.station_id]["station"].get("connectors", [])
        return next(
            (item for item in connectors if str(item.get("id")) == self.connector_id),
            {},
        )

    @property
    def _charging(self) -> dict[str, Any] | None:
        return self.coordinator.data[self.station_id]["active"].get(self.connector_id)

    @property
    def native_value(self) -> str | None:
        """Return API state such as CHARGING or AVAILABLE, without boolean mapping."""
        return _connector_status(self._connector)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        connector = self._connector
        charging = self._charging
        attributes = _connector_attributes(connector)
        station_data = self.coordinator.data[self.station_id]
        if self.connector_id in station_data.get("active_unavailable", set()):
            attributes["active_session_details_unavailable"] = True
        if charging is None:
            return attributes
        transaction = charging.get("transaction") or {}
        attributes.update(
            {
                "charging_id": charging.get("id"),
                "charging_status": charging.get("status"),
                "start_time": charging.get("startTime"),
                "start_meter_value_wh": charging.get("startMeterValueWh"),
                "price": charging.get("price"),
                "transaction_id": transaction.get("transactionId"),
                "meter_start_wh": transaction.get("meterStartWh"),
                "meter_active_power_kw": transaction.get("meterActivePowerW"),
                "meter_register_wh": transaction.get("meterActiveEnergyWh"),
                "meter_register_kwh": _wh_to_kwh(
                    transaction.get("meterActiveEnergyWh")
                ),
                "soc_percent": transaction.get("socPercent"),
            }
        )
        return attributes


def _connector_status(connector: dict[str, Any]) -> str | None:
    """Get the connector state without converting it to a binary value."""
    status = connector.get("state")
    return str(status) if status is not None else None


def _history_item(item: dict[str, Any]) -> dict[str, Any]:
    """Return useful recent-history fields without driver or signed payload data."""
    station = item.get("station") or {}
    return {
        "id": item.get("id"),
        "station_name": station.get("name"),
        "connector_number": station.get("connectorNumber"),
        "start_time": item.get("startTime"),
        "duration_s": item.get("durationS"),
        "consumption_kwh": item.get("consumptionKwh"),
        "price_information": station.get("priceInformation"),
        "driver_costs": item.get("driverCosts"),
        "station_owner_earnings": item.get("stationOwnerEarnings"),
        "provider": item.get("provider"),
        "signed_transaction_format": (item.get("signedTransaction") or {}).get("format"),
    }


def _connector_attributes(connector: dict[str, Any]) -> dict[str, Any]:
    """Expose useful connector details without creating extra entities."""
    attributes = {
        # eCarUp documents this as W, but live examples report 1.805 for a
        # connector whose maximum power is 1800 W, so the value is in kW.
        "active_power_kw": connector.get("activePowerTotal"),
        "connector_number": connector.get("number"),
        "plug_type": connector.get("plugtype"),
        "max_power_w": connector.get("maxpower"),
        "access_type": (connector.get("access") or {}).get("type"),
    }
    description = connector.get("description")
    if description:
        attributes["description"] = description
    return {key: value for key, value in attributes.items() if value is not None}


def _sum_active_power_kw(connectors: list[dict[str, Any]]) -> float | None:
    """Sum reported connector power without turning missing readings into zero."""
    readings = [
        float(connector["activePowerTotal"])
        for connector in connectors
        if connector.get("activePowerTotal") is not None
    ]
    if not readings:
        return None
    return round(sum(readings), 3)


def _wh_to_kwh(value: Any) -> float | None:
    """Convert a watt-hour value to kWh."""
    if value is None:
        return None
    return round(float(value) / 1000, 3)


def _sum_import_energy_kwh(connectors: list[dict[str, Any]]) -> float | None:
    """Convert connector import counters from Wh, only when all are available."""
    if not connectors:
        return None
    readings = [
        float(connector["activeEnergyImport"])
        for connector in connectors
        if connector.get("activeEnergyImport") is not None
    ]
    if len(readings) != len(connectors):
        return None
    return round(sum(readings) / 1000, 3)


def _sum_history_energy_kwh(histories: list[dict[str, Any]]) -> float | None:
    """Sum delivered charging-session energy as a fallback for missing meters."""
    if not histories:
        return 0.0
    readings = [
        float(item["consumptionKwh"])
        for item in histories
        if item.get("consumptionKwh") is not None
    ]
    if not readings:
        return None
    return round(sum(readings), 3)


def _station_total_energy_kwh(
    connectors: list[dict[str, Any]], histories: list[dict[str, Any]]
) -> float | None:
    """Use the larger complete counter/history total; history fills missing meters."""
    meter_total = _sum_import_energy_kwh(connectors)
    history_total = _sum_history_energy_kwh(histories)
    totals = [value for value in (meter_total, history_total) if value is not None]
    return max(totals) if totals else None


def _energy_source(meter_total: float | None, history_total: float | None) -> str:
    """Describe the data source currently determining the displayed total."""
    if meter_total is None and history_total is None:
        return "unavailable"
    if history_total is None or (meter_total is not None and meter_total >= history_total):
        return "connector_meter"
    return "charging_history"


def _sum_owner_earnings(histories: list[dict[str, Any]]) -> dict[str, float]:
    """Sum history earnings separately by currency to avoid mixing currencies."""
    totals: dict[str, float] = {}
    for charging in histories:
        earnings = charging.get("stationOwnerEarnings") or {}
        currency = earnings.get("currency")
        if not currency:
            continue
        amount = sum(
            float(earnings.get(component) or 0)
            for component in ("energy", "time", "penalty")
        )
        totals[currency] = totals.get(currency, 0) + amount
    return {currency: round(amount, 2) for currency, amount in totals.items()}
