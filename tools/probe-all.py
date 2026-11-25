#!/usr/bin/env python3
"""Read-only cloud capability and telemetry probe; no control commands."""
from __future__ import annotations

import argparse
import asyncio
import getpass
import importlib.metadata
import json
import logging
import sys
from typing import Any

GROUPS = {
    1: ("target_compressor_frequency", "compressor_frequency", "compressor_current",
        "compressor_voltage", "indoor_temperature", "outdoor_temperature",
        "indoor_coil_temperature", "outdoor_coil_temperature", "discharge_pipe_temperature"),
    2: ("target_indoor_fan_speed", "indoor_fan_speed", "water_pump_running"),
    4: ("total_energy", "current_energy", "real_time_power",
        "total_energy_binary", "current_energy_binary", "real_time_power_binary"),
    5: ("humidity", "defrost", "outdoor_fan_speed"),
    7: ("outdoor_unit_power",),
    11: ("horizontal_louvers_angle", "vertical_louvers_angle"),
}
STATE = ("power_on", "operational_mode", "target_temperature", "indoor_temperature",
         "outdoor_temperature", "fan_speed", "swing_mode", "eco", "turbo", "sleep",
         "display_on", "filter_alert", "freeze_protection", "follow_me", "purifier",
         "target_humidity", "aux_heat", "independent_aux_heat", "error_code")
CAPABILITIES = (
    "additional_capabilities", "anion", "fan_silent", "fan_low", "fan_medium",
    "fan_high", "fan_auto", "fan_custom", "breeze_away", "breeze_away_direction",
    "breeze_control", "breezeless", "cascade", "flash", "fresh_air", "sound",
    "swing_horizontal_angle", "swing_vertical_angle", "swing_horizontal",
    "swing_vertical", "swing_both", "dry_mode", "cool_mode", "heat_mode",
    "auto_mode", "eco", "ieco_cool", "ieco_heat", "turbo_cool", "turbo_heat",
    "freeze_protection", "display_control", "filter_reminder", "min_temperature",
    "max_temperature", "energy_stats", "humidity", "target_humidity", "self_clean",
    "rate_select_levels", "out_silent",
)
CLOUDS = ("Ariston Clima", "NetHome Plus", "Midea Air")


def extract(response: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    values = {}
    for field in fields:
        try:
            value = getattr(response, field, None)
        except Exception:
            value = None
        # Do not dump raw frames, tokens, or arbitrary response objects.
        values[field] = value if isinstance(value, (bool, int, float, str)) else None
    return values


def response_info(response):
    if response is None:
        return {"response_type": "None"}
    return {"response_type": type(response).__name__,
            "response_id": getattr(response, "id", None),
            "payload_length": len(getattr(response, "payload", b""))}


async def probe_command(send, command, expected, fields, timeout):
    """A failed optional request does not invalidate the other groups."""
    try:
        response = await asyncio.wait_for(send(command), timeout)
        if not isinstance(response, expected):
            return {"status": "no_reply" if response is None else "unexpected_response",
                    **response_info(response), "values": {}}
        values = extract(response, fields)
        return {"status": "ok" if any(v is not None for v in values.values())
                else "no_decoded_values", **response_info(response), "values": values}
    except Exception as exc:
        # Exception text may contain account information or raw cloud data.
        return {"status": "failed", "error_type": type(exc).__name__, "values": {}}


async def run(args):
    # Import after argument parsing so --help works without dependencies.
    from probe import MideaCloudClient
    from _mcc.protocol import transparent_send
    from msmart.device.AC import command as ac

    user = args.user
    if not user:
        print("App account email: ", end="", file=sys.stderr, flush=True)
        user = input().strip()
    password = getpass.getpass("App password: ")
    client = MideaCloudClient(user, password, args.cloud)
    try:
        await asyncio.wait_for(client.connect(), args.timeout)
        appliances = await asyncio.wait_for(client.list_appliances(), args.timeout)
        ids = list(appliances)
        if not ids:
            print("No appliances found.", file=sys.stderr)
            return 1
        if args.id is not None:
            matches = [aid for aid in ids if str(aid) == str(args.id)]
            if len(matches) != 1:
                print("Requested appliance not found on this account.", file=sys.stderr)
                return 1
            aid = matches[0]
        elif len(ids) == 1:
            aid = ids[0]
        else:
            for i, aid in enumerate(ids, 1):
                print(f"{i}: {appliances[aid].get('name') or 'Appliance'}", file=sys.stderr)
            print("Select appliance number: ", end="", file=sys.stderr, flush=True)
            choice = int(input())
            if not 1 <= choice <= len(ids):
                print("Invalid selection.", file=sys.stderr)
                return 1
            aid = ids[choice - 1]

        async def send(command):
            frame = await transparent_send(client._cloud, int(aid),
                                           command.tobytes(), client._app_key)
            return ac.Response.construct(frame) if frame is not None else None

        report = {"library_versions": {
            name: importlib.metadata.version(name)
            for name in ("msmart-ng", "midea-local")
        }, "state": {}, "capabilities": {}, "groups": {}}
        report["state"] = await probe_command(
            send, ac.GetStateCommand(), ac.StateResponse, STATE, args.timeout)
        await asyncio.sleep(args.delay)

        cap_command = getattr(ac, "GetCapabilitiesCommand", None)
        cap_response = getattr(ac, "CapabilitiesResponse", None)
        if cap_command and cap_response:
            try:
                first = await asyncio.wait_for(send(cap_command()), args.timeout)
                if isinstance(first, cap_response):
                    values = extract(first, CAPABILITIES)
                    extra_status = "not_requested"
                    if values.get("additional_capabilities"):
                        await asyncio.sleep(args.delay)
                        try:
                            extra = await asyncio.wait_for(send(cap_command(True)), args.timeout)
                            if isinstance(extra, cap_response):
                                first.merge(extra)
                                values = extract(first, CAPABILITIES)
                                extra_status = "ok"
                            else:
                                extra_status = response_info(extra)
                        except Exception as exc:
                            extra_status = type(exc).__name__
                    report["capabilities"] = {"status": "ok", "additional_status": extra_status,
                                              "values": values}
                else:
                    report["capabilities"] = {"status": "no_reply" if first is None else "unexpected_response",
                                              **response_info(first), "values": {}}
            except Exception as exc:
                report["capabilities"] = {"status": "failed", "error_type": type(exc).__name__,
                                          "values": {}}
        else:
            report["capabilities"] = {"status": "library_unsupported", "values": {}}

        for group in args.groups:
            await asyncio.sleep(args.delay)
            print(f"Reading group {group}...", file=sys.stderr)
            command = getattr(ac, "GetGroupDataCommand", None)
            response = getattr(ac, f"Group{group}Response", None)
            if command is None or response is None:
                report["groups"][str(group)] = {"status": "library_unsupported", "values": {}}
                continue
            report["groups"][str(group)] = await probe_command(
                send, command(group), response, GROUPS[group], args.timeout)

        await asyncio.sleep(args.delay)
        report["state_after"] = await probe_command(
            send, ac.GetStateCommand(), ac.StateResponse, STATE, args.timeout)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["state"]["status"] == "ok" else 2
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", help="App account email (otherwise prompted)")
    parser.add_argument("--cloud", choices=CLOUDS, default=CLOUDS[0])
    parser.add_argument("--id", type=int, help="Optional appliance ID; auto-select a single appliance")
    parser.add_argument("--groups", nargs="+", type=int, choices=tuple(GROUPS),
                        default=list(GROUPS))
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between requests (min 2)")
    parser.add_argument("--timeout", type=float, default=20.0, help="Timeout per request in seconds")
    args = parser.parse_args()
    if not 2 <= args.delay <= 60 or not 5 <= args.timeout <= 120:
        parser.error("delay must be 2-60 s and timeout 5-120 s")
    args.groups = list(dict.fromkeys(args.groups))
    logging.disable(logging.CRITICAL)  # no accidental credentials in third-party logs
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Probe stopped: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
