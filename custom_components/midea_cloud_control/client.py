"""Cloud-relayed control for Midea-protocol air conditioners.

Works with any Midea white-label cloud that still serves the legacy
mapp.appsmb.com API -- Ariston Clima, NetHome Plus and Midea Air.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import aiohttp
from midealocal.cloud import SUPPORTED_CLOUDS, get_midea_cloud
from msmart.device.AC.command import GetStateCommand, Response, SetStateCommand, StateResponse, ToggleDisplayCommand

from .protocol import transparent_send

_LOGGER = logging.getLogger(__name__)

# Clouds sharing the legacy mapp.appsmb.com transparent-send endpoint.
SUPPORTED = ("Ariston Clima", "NetHome Plus", "Midea Air")

# Fields copied verbatim from current state when issuing a set command, so a
# partial change never silently resets unrelated settings.
_CARRIED_STATE = (
    "power_on", "target_temperature", "operational_mode", "fan_speed",
    "eco", "swing_mode", "turbo", "fahrenheit", "sleep",
    "freeze_protection", "follow_me", "purifier", "target_humidity",
    "aux_heat", "independent_aux_heat",
)


class CloudError(Exception):
    """Raised when the cloud rejects a request."""


class MideaCloudClient:
    """Control a Midea appliance through its vendor cloud."""

    def __init__(self, account: str, password: str,
                 cloud_name: str = "Ariston Clima",
                 session: aiohttp.ClientSession | None = None) -> None:
        if cloud_name not in SUPPORTED:
            raise ValueError(
                f"{cloud_name!r} does not serve the transparent-send endpoint; "
                f"expected one of {SUPPORTED}"
            )
        self._account = account
        self._password = password
        self._cloud_name = cloud_name
        self._app_key = str(SUPPORTED_CLOUDS[cloud_name]["app_key"])
        self._session = session
        self._owns_session = session is None
        self._cloud: Any = None
        self._lock = asyncio.Lock()
        self._last_reconnect = float('-inf')

    async def __aenter__(self) -> MideaCloudClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        try:
            cloud = get_midea_cloud(
                self._cloud_name, self._session, self._account, self._password)
            if not await asyncio.wait_for(cloud.login(), 30):
                raise CloudError("Cloud login failed")
        except Exception as err:
            raise CloudError(f"Cloud login failed ({type(err).__name__})") from None
        self._cloud = cloud
        _LOGGER.debug("Logged in to %s", self._cloud_name)

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def list_appliances(self) -> dict[int, dict[str, Any]]:
        return await self._cloud.list_appliances(None) or {}

    async def _query_once(self, appliance_id: int) -> StateResponse:
        """Read current state of the appliance."""
        response = await self._send(appliance_id, GetStateCommand())
        if not isinstance(response, StateResponse):
            raise CloudError(f"unexpected response type {type(response).__name__}")
        return response

    async def _send(self, appliance_id: int, command: Any) -> Response:
        """Send a single request. Never automatically repeat a write."""
        try:
            reply = await asyncio.wait_for(transparent_send(
                self._cloud, appliance_id, command.tobytes(), self._app_key), 25)
            if reply is None:
                raise CloudError("No appliance reply")
            return Response.construct(reply)
        except CloudError:
            raise
        except Exception as err:
            raise CloudError(f"Cloud request failed ({type(err).__name__})") from None

    async def _query_locked(self, appliance_id: int) -> StateResponse:
        try:
            return await self._query_once(appliance_id)
        except CloudError:
            # A missing reply can also indicate an offline appliance. Limit
            # reconnect attempts and retry only the read, never a write.
            now = time.monotonic()
            if now - self._last_reconnect < 120:
                raise
            self._last_reconnect = now
            await self.connect()
            return await self._query_once(appliance_id)

    async def query(self, appliance_id: int) -> StateResponse:
        async with self._lock:
            return await self._query_locked(appliance_id)

    async def set_state(self, appliance_id: int, *, beep: bool = False,
                        **changes: Any) -> StateResponse:
        """Serialize read/merge/write; preserve non-null reported fields."""
        unknown = set(changes) - set(_CARRIED_STATE)
        if unknown or any(value is None for value in changes.values()):
            raise ValueError("Unsupported or missing state value")

        async with self._lock:
            current = await self._query_locked(appliance_id)
            command = SetStateCommand()
            command.beep_on = beep
            for field in _CARRIED_STATE:
                value = getattr(current, field, None)
                if value is not None:
                    setattr(command, field, value)
            for field, value in changes.items():
                setattr(command, field, value)
            response = await self._send(appliance_id, command)
            if not isinstance(response, StateResponse):
                # Some devices acknowledge without a complete state.
                return await self._query_locked(appliance_id)
            return response

    async def set_display(self, appliance_id: int, enabled: bool) -> StateResponse:
        """Toggle only when necessary, and never retry an ambiguous toggle."""
        async with self._lock:
            current = await self._query_locked(appliance_id)
            if current.display_on is None:
                raise CloudError("Display state unavailable")
            if current.display_on == enabled:
                return current
            command = ToggleDisplayCommand()
            command.beep_on = False
            # An unanswered toggle may still have taken effect.
            try:
                await self._send(appliance_id, command)
            except CloudError:
                pass
            result = await self._query_locked(appliance_id)
            if result.display_on != enabled:
                raise CloudError("Display change not confirmed")
            return result
