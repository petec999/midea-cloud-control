# Midea Cloud Control

Home Assistant integration for Midea-protocol air conditioners that **cannot be
controlled locally** because Midea retired the token service.

Works with **Ariston Clima**, **NetHome Plus**, and **Midea Air** accounts.

## Why this exists

Every existing Home Assistant integration for these units (`midea_ac_lan`,
`midea-ac-py`, and friends) controls the appliance **locally** over TCP 6444.
That requires a per-device `token` and `key`, which are only obtainable from
Midea's cloud endpoint:

```
POST /v1/iot/secure/getToken  →  {"msg":"system error","errorCode":9999}
```

**The endpoint returned errors during development.** Appliances onboarded before the
shutdown keep working, because their token was already stored. Anything
onboarded afterwards cannot be added to Home Assistant at all — the vendor app
still works, but local control may be unavailable without another way to obtain the credentials.

This integration takes a different route. The legacy endpoint

```
POST /v1/appliance/transparent/send
```

worked during development. It relays a raw Midea protocol frame to the appliance *through
the cloud*, and needs **no token and no key** — only a normal app login. That is
the same path the vendor app uses, which is why the app keeps working when
Home Assistant cannot connect.

## Trade-offs

This is cloud control. Be clear-eyed about what that means:

- Requires internet access and Midea's servers being up.
- State is **polled** (30 s default), not pushed.
- ~1 s round trip per command, versus near-instant local control.
- Midea is retiring these APIs progressively; `transparent/send` could follow
  `getToken`.

If you can run [ESPHome's Midea component](https://esphome.io/components/climate/midea.html)
on a replacement Wi-Fi dongle, **do that instead** — it is fully local, faster,
and depends on nothing external. This integration is for people who can't or
won't swap hardware.

## Installation

**HACS** — add this repository as a custom repository (type: Integration),
download, restart Home Assistant.

**Manual** — copy `custom_components/midea_cloud_control` into your
`/config/custom_components/` directory and restart.

Then: **Settings → Devices & Services → Add Integration → Midea Cloud Control**.
Enter the email and password of the app your appliance is registered in, pick
the matching app from the dropdown, then select your appliance.

> The app selector matters. Picking the wrong one fails with a vague error,
> because the clouds have separate user databases.

## Features

| | |
|---|---|
| HVAC modes | off, auto, cool, dry, heat, fan only |
| Target temperature | 17–30 °C, 0.5 °C steps |
| Current temperature | indoor sensor |
| Fan | silent, low, medium, high, max, auto |
| Swing | off, horizontal, vertical, both |
| Attributes | outdoor temperature, eco, turbo, sleep, filter alert, error code |

## Command-line probe

Useful for confirming your account and appliance work before touching Home
Assistant:

```bash
python tools/probe.py list  --user EMAIL --password PW
python tools/probe.py state --user EMAIL --password PW --id APPLIANCE_ID
python tools/probe.py set   --user EMAIL --password PW --id APPLIANCE_ID --temp 22
```

Do not commit credentials, access tokens, appliance identifiers or captured authentication responses. Command-line passwords can be visible in shell history and process listings; use a dedicated test environment and avoid sharing command output containing secrets.

## Extended read-only diagnostic probe (experimental)

Use the probe to discover what the appliance and its cloud relay actually support.
No setting changes, write commands, raw frames or credentials are included in the
JSON report. Run from a local Linux terminal with Python 3.10+:

```bash
git clone https://github.com/petec999/midea-cloud-control.git
cd midea-cloud-control
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -U midea-local msmart-ng pycryptodome aiohttp
python tools/probe-all.py --cloud "Ariston Clima" > midea-report.json
```

The script prompts for credentials interactively; choose the correct cloud
(Ariston Clima, NetHome Plus or Midea Air). A single appliance is selected
automatically; for multiple appliances choose its number. Prompts may appear
on standard output, so for a clean JSON file supply `--user` and `--id`
when there is more than one appliance, or run without redirection.

It queries state, capabilities, then groups 1, 2, 4, 5, 7 and 11 sequentially
with a two-second delay and bounded timeouts. Unsupported library classes,
unexpected responses and failed optional requests are recorded independently.
Group 4 reports both energy encodings; neither should be assumed correct until
compared with a known meter. A successful response is not proof every field is
valid; zero and null have different meanings. A failed query does not prove
the hardware lacks that capability. Do not run this continuously.

The probe is experimental; cloud/device compatibility must be confirmed on
real hardware. It does not install or change the Home Assistant integration.

## Related project

[Home IoT Telemetry](https://github.com/petec999/home-iot-telemetry) provides complementary Home Assistant dashboard diagnostics.

## Protocol notes

For anyone extending this. The frame sent in the `order` parameter is:

1. A standard Midea `0xAA` protocol frame (built here with `msmart-ng`).
2. Wrapped in a **40-byte `0x5A5A` packet header**, with byte 4 set to the
   total packet length, followed by 16 trailing zero bytes.
3. Serialised as comma-separated **signed** integers (values ≥ 128 become
   negative).
4. AES-ECB encrypted with a key derived from the login access token:
   `aes_ecb_decrypt(access_token, md5(app_key)[:16])`.
5. Hex-encoded.

Replies come back the same way. The decrypted reply is zero-padded past the end
of the frame, so **truncate to the length declared in byte 1** before parsing —
otherwise the padding is read as payload and fails CRC.

## Credits

Builds on [`midea-local`](https://github.com/wuwentao/midea-local) for cloud
authentication and [`msmart-ng`](https://github.com/mill1000/midea-msmart) for
protocol frame encoding and decoding.

## License

MIT
