# AgentLink

StonePi's single way in for agents and agent devices (`ideation/muse.md`, `ideation/pebble.md`).
It holds the **bindings** (one device or agent ↔ one StonePi user), the **tool registry**,
the per-binding **policy**, and the **audit log**. Adapters translate their protocol into
`registry.invoke(binding, tool, params)`; nothing else reaches the apps.

| | |
|---|---|
| Port | 8015 (loopback on the Pi; 8014 is Kiwix) |
| First adapter | **Pebble** — device API `/agent/pebble/v1` ([Pebble protocol v1](https://github.com/ast0ne1/pebble-esp32/blob/main/PROTOCOL.md)) |
| Admin UI | Notify → **Sidekick** (signed internal API `/agent/api/internal/pebble/*`) |
| Data | `agentlink.db` (bindings, hashed tokens), `audit.db` (`stonepi_audit`, 90 days) |
| Tools | `get_status` (Watch roll-up, no personal data). Phase 2 adds `get_today` and per-app reads. |

## Asking

`POST /agent/pebble/v1/ask`: WAV → faster-whisper (on the Pi) → the binding's enabled read
tools as facts → `stonepi_ai` function `pebble.reply` → piper → WAV. One ask at a time.
The audit records that an ask happened and its timings, never the words.

Speech engines are optional installs: Notify → Sidekick → **Set up speech**, or `sudo stonepi-speech-setup`. They need a **64-bit OS**
(`uname -m` = `aarch64`): faster-whisper and piper only ship 64-bit wheels.

| Env | Default | |
|---|---|---|
| `AGENTLINK_SPEECH` | `auto` | `dev` fakes both (run-dev), `off` refuses asks |
| `AGENTLINK_WHISPER_MODEL` | `tiny.en` | faster-whisper model name or path |
| `AGENTLINK_PIPER_BIN` / `AGENTLINK_PIPER_VOICE` | `piper` / — | piper binary and `.onnx` voice |

## Paths and hosts

nginx sets `X-StonePi-Path: lan | away` (overwriting the client's). Without it AgentLink
assumes `away` (`AGENTLINK_DEFAULT_PATH`), the stricter path. Devices are handed
`AGENTLINK_LAN_URL` (or `http://<LAN IP>[:AGENTLINK_LAN_PORT]`) and, when away use is on,
`AGENTLINK_AWAY_URL` (Tailscale Funnel, `https://<name>.ts.net:8443`).

## Dev

run-dev starts it on `0.0.0.0:8015` with dev speech, so a real Pebble on the LAN can pair
with your PC through Notify → Sidekick. Tests: `python -m pytest tests` from this folder.
