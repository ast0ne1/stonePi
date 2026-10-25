#!/bin/bash
# Speech for Pebble asks (AgentLink): faster-whisper (speech → text) and piper (text → speech)
# in AgentLink's venv, one piper voice and the whisper model, all on the Pi, so raw audio never
# leaves it. Records the choices in /etc/stonepi/agentlink.env, restarts AgentLink and runs the
# speed check (apps/agentlink/tools/speech_check.py).
#
# Usage: sudo stonepi-speech-setup [--voice NAME] [--whisper MODEL] [--packages-only | --check-only]
#   --voice NAME      piper voice (default en_GB-alba-medium; see huggingface.co/rhasspy/piper-voices)
#   --whisper MODEL   faster-whisper model (default tiny.en; base.en is better and ~2-3x slower)
#   --packages-only   just the pip packages (install.sh uses this to keep them through rebuilds)
#   --check-only      just the speed check
#   --start           Notify → Sidekick's Set up speech (sudoers allows exactly this and --status):
#                     the full setup in the background as the transient unit stonepi-speech-setup
#   --status          that run's state, the step it's on and the CPU, as one JSON line
set -euo pipefail

DEST=/opt/stonepi
DATA=/var/lib/stonepi/agentlink
CONF=/etc/stonepi/agentlink.env
APP="$DEST/apps/agentlink"
VENV="$APP/.venv"
SVC_USER=stonepi-agentlink
# Ranges, not pins: pip picks the newest wheels built for this Pi's Python and CPU.
PACKAGES=("faster-whisper>=1.1,<2" "piper-tts>=1.3,<2")
VOICES_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"

voice="$(sed -n 's/^AGENTLINK_PIPER_VOICE=.*\/\([^/]*\)\.onnx$/\1/p' "$CONF" 2>/dev/null | tail -n1)"
voice="${voice:-en_GB-alba-medium}"
whisper="$(sed -n 's/^AGENTLINK_WHISPER_MODEL=//p' "$CONF" 2>/dev/null | tail -n1)"
whisper="${whisper:-tiny.en}"
mode=all
while [[ $# -gt 0 ]]; do
  case "$1" in
    --voice) voice="${2:?}"; shift 2 ;;
    --whisper) whisper="${2:?}"; shift 2 ;;
    --packages-only) mode=packages; shift ;;
    --check-only) mode=check; shift ;;
    --start) mode=start; shift ;;
    --status) mode=status; shift ;;
    --logged) mode=logged; shift ;;  # what --start runs inside the unit
    -h | --help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "Unknown option $1" >&2; exit 2 ;;
  esac
done

log() { echo "[speech] $*"; }

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo stonepi-speech-setup" >&2
  exit 1
fi

# The background run (Sidekick): progress in a log, the outcome in a result file, both in root's
# /var/lib/stonepi and readable by anyone (they hold step names and errors, nothing secret).
UNIT=stonepi-speech-setup
RUN_LOG=/var/lib/stonepi/speech-setup.log
RUN_RESULT=/var/lib/stonepi/speech-setup.result

status_json() {
  local running=0
  systemctl is-active --quiet "$UNIT" 2>/dev/null && running=1
  python3 -I - "$RUN_LOG" "$RUN_RESULT" "$(uname -m)" "$running" <<'PY'
import json
import sys

log, result, arch, running = sys.argv[1:5]


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


res = read(result).strip()
lines = [line.strip() for line in read(log).splitlines() if line.strip()]
steps = [line[len("[speech] "):] for line in lines if line.startswith("[speech] ")]
if running == "1":
    state = "running"
elif res == "ok":
    state = "ok"
elif res:  # "failed N", or "running" left behind by a run that was killed
    state = "failed"
else:
    state = "never"
print(json.dumps({
    "ok": True, "state": state, "step": steps[-1] if steps else "",
    "error": (lines[-1] if state == "failed" and lines else "")[:240],
    "arch": arch, "supported": arch in ("aarch64", "x86_64"),
}))
PY
}

case "$mode" in
  status)
    status_json
    exit 0
    ;;
  start)
    case "$(uname -m)" in
      aarch64 | x86_64) ;;
      *)
        printf '{"ok": false, "message": "Speech needs 64-bit Raspberry Pi OS (this Pi runs %s)."}\n' "$(uname -m)"
        exit 0
        ;;
    esac
    if systemctl is-active --quiet "$UNIT" 2>/dev/null; then
      printf '{"ok": true, "message": "Speech is already being set up."}\n'
      exit 0
    fi
    if [[ ! -x "$VENV/bin/pip" ]]; then
      printf '{"ok": false, "message": "AgentLink isn'"'"'t installed: re-run the StonePi installer first."}\n'
      exit 0
    fi
    : > "$RUN_LOG"
    chmod 644 "$RUN_LOG"
    if ! systemd-run --unit="$UNIT" --collect --quiet "$(readlink -f "$0")" --logged >/dev/null 2>&1; then
      echo "failed (couldn't start)" > "$RUN_RESULT"
      printf '{"ok": false, "message": "Couldn'"'"'t start the speech setup."}\n'
      exit 0
    fi
    printf '{"ok": true, "message": "Setting up speech. This takes a few minutes."}\n'
    exit 0
    ;;
  logged)
    exec >>"$RUN_LOG" 2>&1
    echo running > "$RUN_RESULT"
    chmod 644 "$RUN_RESULT"
    trap 'rc=$?; if [[ $rc -eq 0 ]]; then echo ok > "$RUN_RESULT"; else echo "failed $rc" > "$RUN_RESULT"; fi' EXIT
    mode=all
    ;;
esac
case "$(uname -m)" in
  aarch64 | x86_64) ;;
  *)
    # faster-whisper (CTranslate2) and piper (onnxruntime) only ship 64-bit wheels.
    echo "Speech needs a 64-bit OS (this is $(uname -m)): reinstall with 64-bit Raspberry Pi OS." >&2
    exit 1
    ;;
esac
if [[ ! -x "$VENV/bin/pip" ]]; then
  echo "$VENV missing: install AgentLink first (deploy/install.sh)." >&2
  exit 1
fi
if [[ ! "$voice" =~ ^[a-z]{2,3}_[A-Z]{2}-[A-Za-z0-9_]+-(x_low|low|medium|high)$ ]]; then
  echo "Voice names look like en_GB-alba-medium (language_REGION-name-quality)." >&2
  exit 2
fi
if [[ ! "$whisper" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "Whisper model names look like tiny.en or base.en." >&2
  exit 2
fi

set_key() {
  local key="$1" value="$2"
  touch "$CONF"
  sed -i "/^${key}=/d" "$CONF"
  printf '%s=%s\n' "$key" "$value" >> "$CONF"
}

install_packages() {
  log "Installing ${PACKAGES[*]} into AgentLink's venv (a few minutes on a Pi)"
  env PYTHONWARNINGS=ignore:DEPRECATION "$VENV/bin/pip" install --quiet --disable-pip-version-check "${PACKAGES[@]}"
  # Venvs stay root-owned and read-only to the service (as install.sh leaves them).
  chown -R root:root "$VENV"
  chmod -R u+rwX,go+rX,go-w "$VENV"
}

# Everything inside $DATA belongs to the service user, who could plant symlinks there, so
# root never creates, writes or chowns in it: the service user does its own folders and
# downloads. ($DATA itself sits in root's /var/lib/stonepi, so it can't be swapped.)
as_svc() { runuser -u "$SVC_USER" -- "$@"; }

check_data_dir() {
  if [[ -L "$DATA" || ! -d "$DATA" ]]; then
    echo "$DATA is missing or a symlink: re-run the AgentLink install first." >&2
    exit 1
  fi
}

install_voice() {
  local lang="${voice%%-*}" rest="${voice#*-}"
  local family="${lang%%_*}" name="${rest%-*}" quality="${rest##*-}"
  local dir="$DATA/voices" url="$VOICES_BASE/$family/$lang/$name/$quality/$voice"
  check_data_dir
  as_svc mkdir -p "$dir"
  if [[ ! -s "$dir/$voice.onnx" || ! -s "$dir/$voice.onnx.json" ]]; then
    log "Downloading piper voice $voice"
    as_svc curl -fsSL --retry 3 -o "$dir/$voice.onnx.part" "$url.onnx"
    as_svc curl -fsSL --retry 3 -o "$dir/$voice.onnx.json.part" "$url.onnx.json"
    as_svc mv -f "$dir/$voice.onnx.part" "$dir/$voice.onnx"
    as_svc mv -f "$dir/$voice.onnx.json.part" "$dir/$voice.onnx.json"
  fi
  set_key AGENTLINK_PIPER_VOICE "$dir/$voice.onnx"
  set_key AGENTLINK_WHISPER_MODEL "$whisper"
  set_key AGENTLINK_SPEECH auto
  chmod 640 "$CONF"
}

run_check() {
  log "Speed check (the first run also downloads the whisper model $whisper)"
  check_data_dir
  as_svc mkdir -p "$DATA/models"
  local voice_path
  voice_path="$(sed -n 's/^AGENTLINK_PIPER_VOICE=//p' "$CONF" | tail -n1)"
  (cd "$APP" && runuser -u "$SVC_USER" -- env HOME="$DATA" STONEPI_DATA_DIR="$DATA" AGENTLINK_SPEECH=auto \
    AGENTLINK_PIPER_VOICE="$voice_path" AGENTLINK_WHISPER_MODEL="$whisper" \
    "$VENV/bin/python" tools/speech_check.py)
}

case "$mode" in
  packages) install_packages ;;
  check) run_check ;;
  all)
    install_packages
    install_voice
    run_check
    log "Restarting AgentLink"
    systemctl restart stonepi-agentlink
    log "Done. Notify → Sidekick should show Listening and Speaking ready."
    ;;
esac
