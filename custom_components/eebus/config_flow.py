"""UI configuration and certificate pairing for EEBUS Direct."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from functools import partial
from ipaddress import ip_address
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import AbortFlow

from ._vendor.eebus_sdk import (
    CertificateMismatchError,
    EebusError,
    HemsClient,
    PairingRejectedError,
    ShipService,
    TrustStore,
    detect_interface_ip,
    discover_ship_services,
)
from .const import (
    CONF_IDENTITY_PATH,
    CONF_INTERFACE_IP,
    CONF_PEER_SKI,
    CONF_SCAN_SECONDS,
    CONF_SERVICE,
    CONF_UPDATE_INTERVAL,
    DEFAULT_SCAN_SECONDS,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PAIRING_WAIT_SECONDS,
)
from .identity import create_or_load_identity, normalize_peer_ski

_LOGGER = logging.getLogger(__name__)


def _validate_interface(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = ip_address(value)
    if parsed.version != 4 or parsed.is_loopback:
        raise vol.Invalid("An IPv4 LAN address is required")
    return value


def _service_from_zeroconf(info: Any) -> ShipService:
    properties = {
        str(key).lower(): str(value)
        for key, value in dict(getattr(info, "properties", {}) or {}).items()
    }
    address = str(getattr(info, "ip_address", "") or "")
    addresses = [address] if address and ":" not in address else []
    name = str(getattr(info, "name", "EEBUS._ship._tcp.local."))
    target = str(getattr(info, "hostname", "") or "") or None
    raw_register = properties.get("register")
    return ShipService(
        service_name=name.rstrip("."),
        target=target.rstrip(".") if target else None,
        port=getattr(info, "port", None),
        path=properties.get("path", "/ship/"),
        ship_id=properties.get("id"),
        ski=(properties.get("ski") or "").replace(":", "").replace(" ", "").lower()
        or None,
        brand=properties.get("brand"),
        model=properties.get("model"),
        device_type=properties.get("type"),
        register=raw_register.lower() == "true" if raw_register is not None else None,
        addresses={"ipv4": addresses, "ipv6": []},
        txt=properties,
    )


def _service_label(service: ShipService) -> str:
    product = " ".join(value for value in (service.brand, service.model) if value)
    host = "-"
    try:
        host = service.preferred_host()
    except EebusError as exc:
        _LOGGER.debug("Discovered EEBUS service has no usable host: %s", exc)
    name = product or service.service_name.removesuffix("._ship._tcp.local")
    return f"{name} — {host}"


async def _validate_pairing(
    service: ShipService, identity: Any, interface_ip: str
) -> dict[str, Any]:
    if not service.ski:
        raise ValueError("peer_ski_missing")
    trust = TrustStore.from_server_ski(service.ski, verify_tls=False)
    client = await HemsClient.connect(
        service,
        identity,
        trust,
        interface_ip=interface_ip,
        pairing_wait_seconds=PAIRING_WAIT_SECONDS,
        timeout=12.0,
        profile="default",
    )
    try:
        await client.bootstrap_spine(timeout=2.0)
        payloads = await client.request_remote_detailed_discovery(timeout=5.0)
        discovery = next(
            (
                payload
                for payload in reversed(payloads)
                if "featureInformation" in payload
            ),
            client._last_remote_discovery,
        )
        if not isinstance(discovery, dict) or "featureInformation" not in discovery:
            raise ValueError("no_spine_discovery")
        entities = [
            item.get("description", {}).get("entityType")
            for item in discovery.get("entityInformation", [])
            if isinstance(item, dict)
        ]
        return {"entities": entities, "remote_device": client._remote_device_address}
    finally:
        await client.close()


class EebusConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pair an EEBUS peer without YAML, certificates, or a bridge."""

    VERSION = 2

    def __init__(self) -> None:
        self._interface_ip = ""
        self._services: list[ShipService] = []
        self._service: ShipService | None = None
        self._identity: Any = None
        self._identity_path = ""
        self._pair_task: asyncio.Task[dict[str, Any]] | None = None
        self._pair_error: str | None = None
        self._entered_peer_ski = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return EebusOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select the LAN interface and discover SHIP peers."""
        errors: dict[str, str] = {}
        suggested = ""
        try:
            suggested = await self.hass.async_add_executor_job(detect_interface_ip)
        except (EebusError, OSError) as exc:
            _LOGGER.debug(
                "Could not auto-detect the Home Assistant LAN interface: %s", exc
            )

        if user_input is not None:
            self._interface_ip = user_input.get(CONF_INTERFACE_IP) or suggested
            if not self._interface_ip:
                errors["base"] = "interface_required"
            else:
                try:
                    self._services = await self.hass.async_add_executor_job(
                        partial(
                            discover_ship_services,
                            self._interface_ip,
                            timeout=float(user_input[CONF_SCAN_SECONDS]),
                        )
                    )
                except Exception as exc:
                    _LOGGER.debug("EEBUS discovery failed", exc_info=exc)
                    errors["base"] = "discovery_failed"
                else:
                    self._services = [
                        service for service in self._services if service.port
                    ]
                    if self._services:
                        return await self.async_step_select()
                    errors["base"] = "no_devices_found"

        schema = vol.Schema(
            {
                vol.Required(CONF_INTERFACE_IP, default=suggested): _validate_interface,
                vol.Required(CONF_SCAN_SECONDS, default=DEFAULT_SCAN_SECONDS): vol.All(
                    vol.Coerce(int), vol.Range(min=2, max=15)
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose one discovered EEBUS peer."""
        choices = {
            str(index): _service_label(service)
            for index, service in enumerate(self._services)
        }
        if user_input is not None:
            self._service = self._services[int(user_input[CONF_SERVICE])]
            (
                self._identity,
                self._identity_path,
            ) = await self.hass.async_add_executor_job(
                create_or_load_identity, self.hass.config.config_dir
            )
            return await self.async_step_pair()
        return self.async_show_form(
            step_id="select",
            data_schema=vol.Schema({vol.Required(CONF_SERVICE): vol.In(choices)}),
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Accept the wallbox SKI and establish the pinned SHIP connection."""
        assert self._service is not None
        if user_input is not None:
            self._entered_peer_ski = str(user_input.get(CONF_PEER_SKI, ""))
            try:
                peer_ski = normalize_peer_ski(self._entered_peer_ski)
            except ValueError:
                self._pair_error = "invalid_ski"
                return await self.async_step_pair()
            self._entered_peer_ski = peer_ski
            self._service.ski = self._entered_peer_ski
            await self.async_set_unique_id(self._entered_peer_ski)
            self._abort_if_unique_id_configured()
            self._pair_error = None
            self._pair_task = self.hass.async_create_task(
                _validate_pairing(
                    self._service,
                    self._identity,
                    self._interface_ip,
                ),
                name=f"eebus_pair_{(self._service.ski or 'unknown')[:8]}",
            )
            return await self.async_step_pair_progress()

        return self.async_show_form(
            step_id="pair",
            data_schema=vol.Schema(
                {vol.Required(CONF_PEER_SKI, default=self._entered_peer_ski): str}
            ),
            errors={"base": self._pair_error} if self._pair_error else {},
            description_placeholders={
                "device": _service_label(self._service),
            },
        )

    async def async_step_pair_progress(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Keep the UI responsive while SHIP waits for peer-side trust."""
        assert self._service is not None
        assert self._pair_task is not None
        if not self._pair_task.done():
            return self.async_show_progress(
                step_id="pair_progress",
                progress_action="wait_for_pairing",
                description_placeholders={
                    "device": _service_label(self._service),
                    "peer_ski": self._entered_peer_ski.upper(),
                },
                progress_task=self._pair_task,
            )

        error = self._pair_task.exception()
        if isinstance(error, CertificateMismatchError):
            _LOGGER.warning("Wallbox certificate does not match entered SKI: %s", error)
            self._pair_error = "ski_mismatch"
            self._pair_task = None
            return self.async_show_progress_done(next_step_id="pair")
        if isinstance(error, PairingRejectedError):
            _LOGGER.warning("Wallbox did not accept the EEBUS connection: %s", error)
            self._pair_error = "pairing_not_approved"
            self._pair_task = None
            return self.async_show_progress_done(next_step_id="pair")
        if error is not None:
            _LOGGER.warning("EEBUS pairing failed: %s", error)
            self._pair_error = "cannot_connect"
            self._pair_task = None
            return self.async_show_progress_done(next_step_id="pair")
        return self.async_show_progress_done(next_step_id="pair_finish")

    async def async_step_pair_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the config entry after SHIP and SPINE validation."""
        assert self._service is not None
        title = (
            " ".join(
                value for value in (self._service.brand, self._service.model) if value
            )
            or "EEBUS Wallbox"
        )
        return self.async_create_entry(
            title=title,
            data={
                CONF_INTERFACE_IP: self._interface_ip,
                CONF_PEER_SKI: self._service.ski,
                CONF_SERVICE: asdict(self._service),
                CONF_IDENTITY_PATH: self._identity_path,
            },
        )

    async def async_step_zeroconf(self, discovery_info: Any) -> ConfigFlowResult:
        """Handle native Home Assistant zeroconf discovery."""
        service = _service_from_zeroconf(discovery_info)
        if not service.port:
            raise AbortFlow("invalid_discovery_info")
        self._service = service
        self._services = [service]
        try:
            self._interface_ip = await self.hass.async_add_executor_job(
                detect_interface_ip
            )
        except Exception as exc:
            raise AbortFlow("interface_required") from exc
        self.context["title_placeholders"] = {"name": _service_label(service)}
        self._identity, self._identity_path = await self.hass.async_add_executor_job(
            create_or_load_identity, self.hass.config.config_dir
        )
        return await self.async_step_pair()


class EebusOptionsFlow(OptionsFlowWithReload):
    """Configure refresh timing and the LAN interface."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            data = dict(self.config_entry.data)
            data[CONF_INTERFACE_IP] = user_input[CONF_INTERFACE_IP]
            self.hass.config_entries.async_update_entry(self.config_entry, data=data)
            return self.async_create_entry(
                title="",
                data={CONF_UPDATE_INTERVAL: user_input[CONF_UPDATE_INTERVAL]},
            )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_INTERFACE_IP,
                        default=self.config_entry.data.get(CONF_INTERFACE_IP, ""),
                    ): _validate_interface,
                    vol.Required(
                        CONF_UPDATE_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=5, max=60)),
                }
            ),
        )
