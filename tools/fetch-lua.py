#!/usr/bin/env python3
"""Fetch the vendor's Lua protocol definition for Jaśminowa, without device commands.

The source is saved locally, never executed or uploaded. No credentials, serial
numbers, appliance IDs, URLs or cloud response bodies are printed.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
import unicodedata

import aiohttp
from midealocal.cloud import get_midea_cloud


def normalized(name):
    return "".join(c for c in unicodedata.normalize("NFKD", str(name).casefold())
                   if not unicodedata.combining(c)).strip()


async def run(args):
    account = args.user or input("NetHome Plus email: ").strip()
    password = getpass.getpass("NetHome Plus password: ")
    # Library debug output can contain private account and device metadata.
    logging.getLogger("midealocal").setLevel(logging.CRITICAL)
    stages = {}
    trace = aiohttp.TraceConfig()

    async def response_end(session, context, params):
        if stages.get("download_started"):
            stages["download_http_status"] = params.response.status

    trace.on_request_end.append(response_end)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=40),
                                     trace_configs=[trace]) as session:
        cloud = get_midea_cloud("NetHome Plus", session, account, password)
        if not await asyncio.wait_for(cloud.login(), 60):
            print('{"status":"login_failed"}')
            return 1
        devices = await asyncio.wait_for(cloud.list_appliances(None), 60) or {}
        matches = [d for d in devices.values()
                   if normalized(d.get("name", "")) == "jasminowa"]
        if len(matches) != 1:
            print('{"status":"jasminowa_not_uniquely_found"}')
            return 1
        device = matches[0]
        serial = device.get("sn")
        dtype = device.get("type")
        if dtype != 0xAC or not isinstance(serial, str) or not serial:
            print('{"status":"missing_ac_metadata"}')
            return 1
        report = {"device": "Jaśminowa", "type": "0xac",
                  "model_number": device.get("model_number"),
                  "online": device.get("online"),
                  "serial_length": len(serial),
                  "serial_is_hex": bool(re.fullmatch(r"[0-9a-fA-F]+", serial))}
        original_api = cloud._api_request
        original_fetch = cloud._fetch_lua_file

        async def diagnostic_api(*args, **kwargs):
            endpoint = kwargs.get("endpoint", args[0] if args else "")
            if "luaGet" not in endpoint:
                return await original_api(*args, **kwargs)
            kwargs["raise_for_error"] = True
            stages["lua_api_called"] = True
            response = await original_api(*args, **kwargs)
            stages["lua_api_result"] = "empty" if response is None else "received"
            if isinstance(response, dict):
                stages["has_url"] = bool(response.get("url"))
                stages["has_filename"] = bool(response.get("fileName"))
            return response

        async def diagnostic_fetch(*args, **kwargs):
            stages["download_started"] = True
            result = await original_fetch(*args, **kwargs)
            stages["download_result"] = "saved" if result else "empty"
            return result

        cloud._api_request = diagnostic_api
        cloud._fetch_lua_file = diagnostic_fetch
        report["stages"] = stages
        # Use the library's vendor endpoint and decryption implementation.
        # Keep the original download private and move only to a fixed safe name.
        out = Path.home() / "midea-lua"
        out.mkdir(mode=0o700, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="download-", dir=out) as tmp:
            try:
                path = await asyncio.wait_for(cloud.download_lua(
                tmp, dtype, serial,
                model_number=str(device["model_number"]) if device.get("model_number") else None,
                manufacturer_code=device.get("manufacturer_code") or "0000",
                ), 90)
            except Exception as exc:
                report.update(status="error", error_type=type(exc).__name__)
                if isinstance(getattr(exc, "code", None), int):
                    report["cloud_error_code"] = exc.code
                print(json.dumps(report, indent=2))
                return 2
            if not path:
                report["status"] = "no_lua_returned"
                print(json.dumps(report, indent=2))
                return 2
            source = Path(path).resolve()
            if source.parent != Path(tmp).resolve() or not source.is_file():
                raise RuntimeError("Unexpected download path")
            if source.stat().st_size > 10_000_000:
                raise RuntimeError("Unexpected Lua file size")
            data = source.read_bytes()
            destination = out / "jasminowa.lua"
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            destination.chmod(0o600)
            # Report only feature keywords, not raw source or private metadata.
            text = data.decode("utf-8", errors="replace")
            terms = ("eco", "ieco", "power_saving", "screen_display", "display",
                     "led", "energy", "electricity", "compressor", "humidity",
                     "defrost", "sleep", "turbo", "json_to_data", "data_to_json")
            report.update(status="downloaded", bytes=len(data),
                          file="~/midea-lua/jasminowa.lua",
                          keyword_hits={term: len(re.findall(re.escape(term), text, re.I))
                                        for term in terms})
            print(json.dumps(report, indent=2))
            return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", help="NetHome Plus account email; otherwise prompt")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except Exception as exc:
        result = {"status": "error", "error_type": type(exc).__name__}
        if isinstance(getattr(exc, "code", None), int):
            result["cloud_error_code"] = exc.code
        print(json.dumps(result, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
