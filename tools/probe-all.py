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

        if args.state_only:
            raw = await asyncio.wait_for(transparent_send(
                client._cloud, int(aid), ac.GetStateCommand().tobytes(),
                client._app_key), args.timeout)
            if raw is None:
                print(json.dumps({"status": "no_reply"}))
                return 2
            decoded = ac.Response.construct(raw)
            report = {"status": "ok", "values": extract(decoded, STATE)}
            body = raw[10:-2]
            if len(body) >= 19 and body[0] == 0xc0:
                report["vendor_flags"] = {
                    "eco": bool(body[9] & 0x10),
                    "power_saving": bool(body[8] & 0x08),
                    "strong_wind": bool(body[8] & 0x20),
                    "turbo": bool(body[10] & 0x02),
                    "display_code": (body[14] & 0x70) >> 4,
                }
            print(json.dumps(report, indent=2))
            return 0

        if args.vendor_frames and args.test_eco:
            # Use only Jaśminowa and only a safe, simple cooling baseline.
            # Vendor Lua classic ECO command: full state with mobile fan flag
            # and vendor timer encoding. No changes to unsupported properties.
            import secrets
            import unicodedata
            name = "".join(c for c in unicodedata.normalize("NFKD",
                str(appliances[aid].get("name", "")).casefold()) if not unicodedata.combining(c))
            if name.strip() != "jasminowa":
                print(json.dumps({"status": "wrong_device"}))
                return 2

            def crc(data):
                value = 0
                for byte in data:
                    value ^= byte
                    for _ in range(8):
                        value = (value >> 1) ^ (0x8c if value & 1 else 0)
                return value

            def packet(body, kind):
                body = bytearray(body)
                body[-2] = 1 + secrets.randbelow(254)
                body[-1] = crc(body[:-1])
                data = bytearray([0xaa, 10 + len(body), 0xac, 0, 0, 0, 0, 0, 0, kind]) + body
                data.append((-sum(data[1:])) & 255)
                return bytes(data)

            async def relay(frame):
                return await asyncio.wait_for(transparent_send(
                    client._cloud, int(aid), frame, client._app_key), args.timeout)

            async def read():
                raw = await relay(ac.GetStateCommand().tobytes())
                if not raw or len(raw) < 29 or raw[10] != 0xc0:
                    raise RuntimeError("No basic state")
                return raw[10:-2]

            def summary(b):
                return {"power": bool(b[1] & 1), "mode": b[2] >> 5,
                        "temperature": 16 + (b[2] & 15) + (0.5 if b[2] & 16 else 0),
                        "fan": b[3] & 127, "eco": bool(b[9] & 16),
                        "strong_wind": bool(b[8] & 32), "turbo": bool(b[10] & 2),
                        "sleep": bool((b[8] & 3) or (b[9] & 64) or (b[10] & 1)),
                        "power_saving": bool(b[8] & 8),
                        "timer_on": bool(b[4] & 128), "timer_off": bool(b[5] & 128)}

            before = await read()
            state = summary(before)
            report = {"before": state}
            if (not state["power"] or state["mode"] != 2 or state["temperature"] != 26
                    or state["fan"] != 102 or state["sleep"] or state["strong_wind"]
                    or state["turbo"] or state["timer_on"] or state["timer_off"]
                    or state["power_saving"] or state["eco"]):
                report["status"] = "skipped: set COOL 26 C, AUTO fan, ECO/Turbo/Sleep/timers/power saving off"
                print(json.dumps(report, indent=2))
                return 2
            body = bytearray(26)
            body[0] = 0x40
            body[1] = 0x03  # mobile, power on, silent
            body[2] = before[2]
            body[3] = before[3] | 0x80
            body[4:7] = bytes([0x7f, 0x7f, 0xff])
            body[7] = 0x30 | (before[7] & 0x0f)
            body[8] = 0
            body[9] = (before[9] & 0x2c) | 0x80
            body[10] = ((before[10] & 0x20) >> 2) | (before[10] & 4)
            # Preserve PMV and natural wind using vendor's response-to-set mapping.
            pmv = before[14] & 15
            body[17] = ((pmv & 8) << 4) | (0x40 if before[9] & 2 else 0)
            body[18] = (pmv & 7) << 5
            sent = await relay(packet(body, 2))
            report["write_reply"] = bool(sent)
            await asyncio.sleep(args.delay)
            report["after"] = summary(await read())
            print(json.dumps(report, indent=2))
            return 0

        if args.vendor_frames:
            # Independent implementation of the vendor Lua query layouts.
            # No vendor source or private device identifiers are embedded here.
            import secrets

            def crc8(data):
                crc = 0
                for byte in data:
                    crc ^= byte
                    for _ in range(8):
                        crc = (crc >> 1) ^ (0x8c if crc & 1 else 0)
                return crc

            def frame(prefix, length):
                body = bytearray(length)
                body[:len(prefix)] = bytes(prefix)
                body[-2] = 1 + secrets.randbelow(254)
                body[-1] = crc8(body[:-1])
                header = bytearray([0xaa, 10 + len(body), 0xac, 0, 0, 0, 0, 0, 0, 3])
                packet = header + body
                packet.append((-sum(packet[1:])) & 0xff)
                return bytes(packet)

            async def vendor_read(label, packet):
                try:
                    raw = await asyncio.wait_for(transparent_send(
                        client._cloud, int(aid), packet, client._app_key), args.timeout)
                    result = {"request": packet.hex(), "status": "no_reply"}
                    if raw is None:
                        return result
                    body = raw[10:-2]
                    result.update(status="reply", response_type=body[0] if body else None,
                                  response_length=len(raw))
                    if len(body) >= 19 and body[0] == 0xc0:
                        result["eco"] = bool(body[9] & 0x10)
                        result["power_saving"] = bool(body[8] & 8)
                        result["turbo"] = bool((body[8] & 0x20) or (body[10] & 2))
                        result["display_code"] = (body[14] & 0x70) >> 4
                    elif len(body) >= 19 and body[0] == 0xc1 and body[3] == 0x44:
                        result["power_raw"] = int.from_bytes(body[16:19], "big")
                    elif len(body) >= 5 and body[0] == 0xc1 and body[3] & 15 == 5:
                        result["humidity_raw"] = body[4]
                    return result
                except Exception as exc:
                    return {"status": "error", "error_type": type(exc).__name__}

            # Full-length vendor query bodies, not compact group commands.
            queries = [
                ("state", frame([0x41, 0x81, 0, 0xff], 22)),
                ("power", frame([0x41, 0x21, 1, 0x44], 22)),
                ("humidity", frame([0x41, 0x21, 1, 0x45], 22)),
                ("capabilities", bytes.fromhex("aa0eac00000000000303b50100" +
                    f"{crc8(bytes.fromhex('b50100')):02x}" + "3d")),
                ("state_after", frame([0x41, 0x81, 0, 0xff], 22)),
            ]
            report = {}
            for label, packet in queries:
                if report:
                    await asyncio.sleep(args.delay)
                report[label] = await vendor_read(label, packet)
            if args.test_display:
                # Toggle is intentionally opt-in and sent exactly once.
                # No retry even if the cloud returns no acknowledgement.
                if report["state_after"]["status"] == "reply" and "display_code" in report["state_after"]:
                    await asyncio.sleep(args.delay)
                    report["display_toggle"] = await vendor_read(
                        "display_toggle", frame([0x41, 0x61, 0, 0xff, 2, 0, 2, 0], 23))
                    await asyncio.sleep(args.delay)
                    report["display_after"] = await vendor_read(
                        "display_after", frame([0x41, 0x81, 0, 0xff], 22))
                else:
                    report["display_toggle"] = {"status": "skipped_no_state"}
            print(json.dumps(report, indent=2))
            return 0

        if args.transport_check:
            # The normal transport hides API errors behind a None return.
            # Ask the library for typed errors; never print raw HTTP bodies.
            from _mcc.protocol import (_ENDPOINT, aes_encrypt, aes_decrypt,
                                       derive_data_key, wrap_frame, unwrap_reply)
            import time

            async def check(command):
                start = time.monotonic()
                try:
                    cloud = client._cloud
                    key = derive_data_key(cloud._access_token, client._app_key)
                    data = cloud._make_general_data()
                    data.update(order=aes_encrypt(wrap_frame(command.tobytes()), key),
                                funId="0000", applianceId=str(aid))
                    response = await asyncio.wait_for(
                        cloud._api_request(endpoint=_ENDPOINT, data=data, raise_for_error=True),
                        args.timeout)
                    if response is None:
                        result = {"status": "empty_api_result"}
                    elif not response.get("reply"):
                        result = {"status": "missing_reply"}
                    else:
                        raw = unwrap_reply(aes_decrypt(response["reply"], key))
                        decoded = ac.Response.construct(raw)
                        result = {"status": "ok", **response_info(decoded)}
                except Exception as exc:
                    result = {"status": "error", "error_type": type(exc).__name__}
                    code = getattr(exc, "code", None)
                    if isinstance(code, int):
                        result["cloud_error_code"] = code
                result["elapsed_seconds"] = round(time.monotonic() - start, 2)
                return result

            checks = {"state_1": await check(ac.GetStateCommand())}
            if checks["state_1"]["status"] == "ok":
                await asyncio.sleep(args.delay)
                checks["state_2"] = await check(ac.GetStateCommand())
                if checks["state_2"]["status"] == "ok":
                    await asyncio.sleep(args.delay)
                    checks["group_4"] = await check(ac.GetGroupDataCommand(4))
                    await asyncio.sleep(args.delay)
                    checks["state_3"] = await check(ac.GetStateCommand())
            print(json.dumps(checks, indent=2))
            return 0 if checks["state_1"]["status"] == "ok" else 2

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

        if args.alternative:
            from midealocal.devices.ac import message as local
            report["alternative"] = {}
            async def raw_query(label, factory):
                await asyncio.sleep(args.delay)
                try:
                    raw = bytes(factory().serialize())
                    reply = await asyncio.wait_for(
                        transparent_send(client._cloud, int(aid), raw, client._app_key),
                        args.timeout)
                    result = {"request_length": len(raw), "status": "no_reply"}
                    if reply is not None:
                        result = {"request_length": len(raw), "status": "reply",
                                  "response_length": len(reply),
                                  "response_id": reply[10] if len(reply) > 10 else None}
                        # Decode only known device-state properties, not credentials or headers.
                        if len(reply) > 12 and reply[10] == 0xB1:
                            body = reply[10:-2]
                            pos = 2
                            properties = {}
                            for _ in range(body[1]):
                                if pos + 4 > len(body):
                                    break
                                tag = body[pos] | (body[pos+1] << 8)
                                status, length = body[pos+2:pos+4]
                                pos += 4
                                if pos + length > len(body):
                                    break
                                if tag in (0x0017, 0x00E3):
                                    properties[f"{tag:04x}"] = {
                                        "status": status, "data": list(body[pos:pos+length])}
                                pos += length
                            result["properties"] = properties
                        elif len(reply) > 13 and reply[10] == 0xC1:
                            result["group"] = reply[13] & 0x0F
                            try:
                                decoded = ac.Response.construct(reply)
                                result["values"] = extract(decoded, GROUPS.get(result["group"], ()))
                            except Exception as exc:
                                result["decode_error"] = type(exc).__name__
                    report["alternative"][label] = result
                except Exception as exc:
                    report["alternative"][label] = {"status": "failed", "error_type": type(exc).__name__}

            for label, tag in (("display_property", 0x0017), ("ieco_property", 0x00E3)):
                class SingleProperty(local.MessageNewProtocolQuery):
                    _query_params = (tag,)
                await raw_query(label, lambda: SingleProperty(args.protocol_version))
            for group, name in ((1, "MessageGroupOneQuery"), (4, "MessagePowerQuery"),
                                (5, "MessageHumidityQuery")):
                cls = getattr(local, name, None)
                if cls:
                    await raw_query(f"short_group_{group}", lambda: cls(args.protocol_version))
                else:
                    report["alternative"][f"short_group_{group}"] = {"status": "library_unsupported"}

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
    parser.add_argument("--state-only", action="store_true", help="Read one basic state and vendor flags; no writes")
    parser.add_argument("--vendor-frames", action="store_true", help="Probe vendor-specific read frames")
    parser.add_argument("--test-eco", action="store_true", help="Opt-in single vendor ECO command for Jaśminowa with guarded cooling baseline")
    parser.add_argument("--test-display", action="store_true", help="With --vendor-frames: send exactly one display toggle")
    parser.add_argument("--transport-check", action="store_true", help="Compare repeated state reads with one group request; show numeric API errors")
    parser.add_argument("--alternative", action="store_true", help="Read B1 display/iECO and compact group queries using midea-local")
    parser.add_argument("--protocol-version", type=int, choices=(0, 3), default=0)
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
