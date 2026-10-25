"""Pebble's voice: a short list of English piper voices, chosen in Notify → Sidekick.

One voice for the household (AgentLink has one speech engine; each loaded voice is ~60 MB
of RAM on a Pi). A chosen voice that isn't on the Pi yet is downloaded here, in the
background, from the pinned rhasspy/piper-voices v1.0.0 release, as the service user into
the data folder. The choice lives in ``settings.json``; without one, AGENTLINK_PIPER_VOICE
(written by stonepi-speech-setup) applies.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import httpx

from app import settings
from app.config import DATA_DIR, env

logger = logging.getLogger("agentlink.voices")

VOICES_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"
# (id, label). Ids are piper names: language_REGION-name-quality.
VOICES: tuple[tuple[str, str], ...] = (
    ("en_GB-alba-medium", "British English · female · Scottish accent"),
    ("en_GB-cori-medium", "British English · female"),
    ("en_GB-jenny_dioco-medium", "British English · female"),
    ("en_GB-southern_english_female-low", "British English · female · southern (lighter quality)"),
    ("en_GB-alan-medium", "British English · male"),
    ("en_GB-northern_english_male-medium", "British English · male · northern"),
    ("en_US-amy-medium", "American English · female"),
    ("en_US-lessac-medium", "American English · female"),
    ("en_US-hfc_female-medium", "American English · female"),
    ("en_US-ryan-medium", "American English · male"),
    ("en_US-hfc_male-medium", "American English · male"),
)
_IDS = {v for v, _ in VOICES}
_lock = threading.Lock()
_downloads: dict[str, str] = {}  # voice id → "downloading" | "failed: …"


def voices_dir() -> Path:
    return DATA_DIR / "voices"


def _load_settings() -> dict:
    return settings.load()


def _save_settings(data: dict) -> None:
    settings.save(data)


def installed(voice_id: str) -> bool:
    d = voices_dir()
    return (d / f"{voice_id}.onnx").is_file() and (d / f"{voice_id}.onnx.json").is_file()


def current_id() -> str:
    chosen = str(_load_settings().get("voice") or "")
    if chosen in _IDS and installed(chosen):
        return chosen
    return Path(env.piper_voice).stem if env.piper_voice else ""


def current_path() -> str:
    """The .onnx the speech engine should load ("" when no voice is set up)."""
    chosen = str(_load_settings().get("voice") or "")
    if chosen in _IDS and installed(chosen):
        return str(voices_dir() / f"{chosen}.onnx")
    return env.piper_voice or ""


def _url(voice_id: str) -> str:
    lang, rest = voice_id.split("-", 1)
    name, quality = rest.rsplit("-", 1)
    return f"{VOICES_BASE}/{lang.split('_')[0]}/{lang}/{name}/{quality}/{voice_id}"


def _download(voice_id: str) -> None:
    folder = voices_dir()
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=True) as client:
            for suffix in (".onnx.json", ".onnx"):  # the big model last, so .onnx means complete
                part = folder / f"{voice_id}{suffix}.part"
                with client.stream("GET", _url(voice_id) + suffix) as response:
                    response.raise_for_status()
                    with part.open("wb") as fh:
                        for chunk in response.iter_bytes(1 << 16):
                            fh.write(chunk)
                part.replace(folder / f"{voice_id}{suffix}")
    except Exception as exc:  # noqa: BLE001 - reported on the Sidekick page
        logger.warning("Voice %s download failed: %s", voice_id, exc)
        with _lock:
            _downloads[voice_id] = f"failed: {str(exc)[:80]}"
        return
    with _lock:
        _downloads.pop(voice_id, None)
        settings = _load_settings()
        if settings.get("pending_voice") == voice_id:
            settings["voice"] = voice_id
            settings.pop("pending_voice", None)
            _save_settings(settings)
    _reset_engine()
    logger.info("Voice %s downloaded and selected", voice_id)


def _reset_engine() -> None:
    from app import speech

    speech.reset_voice()


def select(voice_id: str) -> tuple[bool, str]:
    """Use ``voice_id`` from now on; downloads it first when it isn't on the Pi yet."""
    if voice_id not in _IDS:
        return False, "That voice isn't on the list."
    if installed(voice_id):
        with _lock:
            settings = _load_settings()
            settings["voice"] = voice_id
            settings.pop("pending_voice", None)
            _save_settings(settings)
        _reset_engine()
        return True, "Voice changed."
    with _lock:
        if _downloads.get(voice_id) == "downloading":
            return True, "Already downloading that voice."
        _downloads[voice_id] = "downloading"
        settings = _load_settings()
        settings["pending_voice"] = voice_id
        _save_settings(settings)
    threading.Thread(target=_download, args=(voice_id,), name=f"voice-{voice_id}", daemon=True).start()
    return True, "Downloading the voice (about 60 MB); Pebble switches to it when it's done."


def catalog() -> dict:
    current = current_id()
    with _lock:
        states = dict(_downloads)
    pending = str(_load_settings().get("pending_voice") or "")
    return {
        "current": current,
        "pending": pending if states.get(pending) == "downloading" else "",
        "voices": [
            {"id": v, "label": label, "installed": installed(v), "current": v == current, "state": states.get(v, "")}
            for v, label in VOICES
        ],
    }
