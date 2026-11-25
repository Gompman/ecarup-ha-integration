# eCarUp for Home Assistant

Home Assistant custom integration for the eCarUp Public API. It retrieves the charging stations linked to an eCarUp account and provides energy, revenue, the latest charging session, and the status of each connector.

> According to the [eCarUp Wiki](https://sites.google.com/smart-me.com/ecarup-wiki/funktionen/ecarup-api), access to the eCarUp API requires a Premium licence. The OAuth client needs at least the `user.read` and `device.read` permissions.

## Installation

### Manual installation

1. Copy `custom_components/ecarup` to `<config>/custom_components/ecarup`.
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration** and select **eCarUp**.
4. Enter your client ID and client secret.

### HACS

Add this repository to [HACS as a custom repository](https://www.hacs.xyz/docs/faq/custom_repositories/) with the category **Integration**. After downloading it, restart Home Assistant and set up the integration through the UI.

## OAuth credentials

Create an API client using the Client Credentials flow in the [smart-me web portal](https://api.smart-me.com/oauth/login), with the `user.read` and `device.read` permissions. The integration uses the OAuth Client Credentials flow with the smart-me Identity Server and stores the credentials in a Home Assistant config entry—not in YAML or in the repository. Never share credentials in issues, logs, or commits. If credentials expire or are rotated, Home Assistant will request new credentials through **Re-authenticate**; existing devices and entity IDs are retained.

## Entities

The integration creates the following entities for each charging station:

- **Total energy**: The higher of the sum of the connectors' import counters (API values in Wh) and the reported charging sessions (kWh). Session history is used as a fallback when no connector counter is available and also helps account for station counters that have been reset. Attributes show both totals, the selected source, and whether the history has been truncated by the page limit.
- **Total revenue**: The sum of revenue from energy, time, and penalty fees across all history pages returned by the API. The currency is used as the unit.
- **Latest charging session**: Energy used in the most recent session. Attributes include its time, duration, connector, costs/revenue, and up to ten recent sessions.

In addition, the integration creates a **status sensor** for each connector. It displays the status reported by eCarUp as text (for example, `CHARGING`, `AVAILABLE`, or `FAULTED`) instead of representing it as a binary on/off state. Connector number, plug type, maximum power, access type, and current power are available as attributes; transaction details are added while charging. The live sample data reports `activePowerTotal` in kW (for example, `1.805` for a maximum power of 1800 W), although the API documentation describes the value in W. The integration therefore exposes it explicitly as `active_power_kw`. Missing API values remain unknown instead of being shown as zero or an invented status.

The integration updates data every five minutes. It fetches the station list and station details separately because the list endpoint does not include current connector statuses. Active charging is checked for each connector through the Active Charging endpoint. Stations and connectors that appear while the integration is running are dynamically added as devices and entities. On the first run, history is fetched in small batches of five pages and cached in Home Assistant storage. Subsequent updates fetch only the newest pages, stopping at the first already-cached session; the cursor and history are retained across restarts. History retrieval is capped at 100 pages for the initial backfill and 20 pages per subsequent update. Attributes indicate whether a backfill is still running or a limit has been reached. Session details include pricing, the charging provider (such as eCarUp or Hubject), and the transaction format. Identifying driver fields and signed payloads are not stored in the cache or session attributes. Home Assistant Recorder stores state changes according to its own configuration; the `recent_sessions` attribute is a current snapshot, not a substitute for unlimited history.

If the station or history endpoint fails, the affected entities are marked unavailable. Temporary failures of the Active Charging endpoint leave the connector status available and only mark session details as missing. Sanitised diagnostic data for support cases can be downloaded from **Settings → Devices & services → eCarUp → Download diagnostics**. Credentials, driver identifiers, and coordinates are removed from diagnostics.

## Removing the integration

Remove the config entry from **Settings → Devices & services → eCarUp → ⋮ → Delete**. Home Assistant removes the associated devices and entities. The locally stored history may remain as an integration-specific storage file after removal.

## Integration images

Home Assistant 2026.3 and later use the local `brand/icon.png` and `brand/logo.png` files. The wide eCarUp logo is used on the integration details page. If it does not appear, update or restart Home Assistant and perform a hard refresh of the browser cache.

## Development

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
ruff check custom_components tests
pytest
```

## API specification

`assets/ecarup-openapi.json` is the downloaded OpenAPI specification for the [eCarUp Public API](https://public-api.ecarup.com/swagger/index.html). It is maintained by the upstream provider and can be updated with `scripts/update_openapi.sh`.

The JSON files in `assets/` and `tests/fixtures/` are synthetic examples of eCarUp API responses, not live account data. They contain only five shortened, rounded, and fabricated charging-history entries; identifying fields and precise usage details have been omitted or replaced.

## Brand assets

The official eCarUp brand assets are in `custom_components/ecarup/brand/`. The round logo is used as the icon, and the wide logo is used as the integration logo. Sources: [icon from the eCarUp website](https://web.ecarup.com/wp-content/uploads/2020/02/eCarUp-Logo_rund_blauaufweiss.png) and [wide logo from the eCarUp Wiki](https://sites.google.com/sitesv-images-rt/APXyLQcpI8gapDC12LDXweGkslatZDiTMBr5TesnPTaeOQRUol78KZL478Td51mCOX2k1YtBF_NHyqq9M4v2mZR_z1voJqm0aYAEBaaeESwwHfivQR7XNJr1MMD7omArTjsxe39kt6e60koEweAYDyiTeDhfD82MwsvUL98OletDeaMSRQ2tqzp4ANZY5YR6=w16383).

## Links

- [eCarUp API Swagger UI](https://public-api.ecarup.com/swagger/index.html)
- [eCarUp API Wiki](https://sites.google.com/smart-me.com/ecarup-wiki/funktionen/ecarup-api)
- [Official eCarUp API examples](https://github.com/eCarUp/ecarup-api-examples)
- [HACS custom repository documentation](https://www.hacs.xyz/docs/faq/custom_repositories/)
- [smart-me web portal](https://api.smart-me.com/oauth/login)
