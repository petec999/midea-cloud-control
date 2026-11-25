"""Cloud-relayed control for Midea-protocol air conditioners.

Works with any Midea white-label cloud that still serves the legacy
mapp.appsmb.com API -- Ariston Clima, NetHome Plus and Midea Air.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
from midealocal.cloud import SUPPORTED_CLOUDS, get_midea_cloud
from msmart.device.AC.command import GetStateCommand, Response, SetStateCommand, StateResponse

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

    async def __aenter__(self) -> MideaCloudClient:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def connect(self) -> None:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        self._cloud = get_midea_cloud(
            self._cloud_name, self._session, self._account, self._password)
        if not await self._cloud.login():
            raise CloudError(f"login failed for {self._account} on {self._cloud_name}")
        _LOGGER.debug("Logged in to %s", self._cloud_name)

    async def close(self) -> None:
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def list_appliances(self) -> dict[int, dict[str, Any]]:
        return await self._cloud.list_appliances(None) or {}

    async def query(self, appliance_id: int) -> StateResponse:
        """Read current state of the appliance."""
        reply = await transparent_send(
            self._cloud, appliance_id, GetStateCommand().tobytes(), self._app_key)
        if reply is None:
            raise CloudError(f"no reply from appliance {appliance_id}")
        response = Response.construct(reply)
        if not isinstance(response, StateResponse):
            raise CloudError(f"unexpected response type {type(response).__name__}")
        return response

    async def set_state(self, appliance_id: int, *, beep: bool = False,
                        **changes: Any) -> StateResponse:
        """Apply `changes` on top of current state, then return the new state.

        Midea set commands carry the *entire* state, so current values are read
        first and merged -- otherwise setting one field resets the others.
        """
        unknown = set(changes) - set(_CARRIED_STATE)
        if unknown:
            raise ValueError(f"unsupported field(s): {sorted(unknown)}")

        current = await self.query(appliance_id)

        command = SetStateCommand()
        command.beep_on = beep
        for field in _CARRIED_STATE:
            value = getattr(current, field, None)
            # Short state responses omit optional fields (e.g. target_humidity).
            # Keep the command encoder default rather than copying None.
            if value is not None:
                setattr(command, field, value)
        for field, value in changes.items():
            setattr(command, field, value)

        reply = await transparent_send(
            self._cloud, appliance_id, command.tobytes(), self._app_key)
        if reply is None:
            raise CloudError(f"appliance {appliance_id} did not acknowledge command")
        return Response.construct(reply)
