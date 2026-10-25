"""Speech on the Pi: faster-whisper for speech-to-text, piper for text-to-speech.

Raw audio never leaves the Pi (pebble.md, Privacy). Both engines are optional installs:
when one is missing, asks answer ``503 speech_unavailable`` and Sidekick says why.
``AGENTLINK_SPEECH=dev`` fakes both so the whole pipeline runs in run-dev.
"""

from __future__ import annotations

import array
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from app.config import DATA_DIR, env
from app.wav import ASK_RATE, chime, to_reply_wav

logger = logging.getLogger("agentlink.speech")

DEV_TRANSCRIPT = "How is StonePi doing?"
# Whisper's initial prompt: the words it would otherwise mishear ("Stone Pie"). Spelling them
# out once biases it towards our names at no speed cost. AGENTLINK_WHISPER_HINT adds more
# (people's names, places), comma-separated.
VOCABULARY = (
    "StonePi, Pebble, Sidekick, NewsCast, EventTrakr, Pinboard, SportGuide, PriceWatch, "
    "PriceScout, FileServe, Studio, Library, Notify, Car Thing, TRMNL, Tailscale."
)


class SpeechUnavailable(Exception):
    pass


_whisper = None
_whisper_lock = threading.Lock()


def _whisper_model():
    global _whisper
    with _whisper_lock:
        if _whisper is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise SpeechUnavailable("faster-whisper isn't installed") from exc
            _whisper = WhisperModel(
                env.whisper_model,
                device="cpu",
                compute_type="int8",
                download_root=str(DATA_DIR / "models" / "whisper"),
                cpu_threads=max(1, (os.cpu_count() or 4) - 1),
            )
        return _whisper


def transcribe(samples: array.array) -> str:
    """16 kHz mono samples → English text ("" when nothing was said)."""
    if env.speech == "dev":
        return DEV_TRANSCRIPT
    if env.speech == "off":
        raise SpeechUnavailable("speech is switched off")
    try:
        import numpy as np
    except ImportError as exc:
        raise SpeechUnavailable("faster-whisper isn't installed") from exc
    model = _whisper_model()
    audio = np.frombuffer(samples.tobytes(), dtype=np.int16).astype(np.float32) / 32768.0
    segments, _info = model.transcribe(audio, **transcribe_options())
    return " ".join(seg.text.strip() for seg in segments).strip()


def transcribe_options() -> dict:
    hint = VOCABULARY
    extra = (env.whisper_hint or "").strip().strip(",")
    if extra:
        hint = f"{hint.rstrip('.')}, {extra}."
    return {"language": "en", "beam_size": 1, "vad_filter": True, "initial_prompt": hint}


def warm_up() -> None:
    """Load both models now (run in the background at start), so the first ask after a
    restart doesn't pay seconds of loading. Missing engines are just logged."""
    if env.speech != "auto":
        return
    started = time.monotonic()
    try:
        _whisper_model()
    except Exception as exc:  # noqa: BLE001 - not installed / no model yet: asks will say so
        logger.info("Whisper not warmed up: %s", exc)
    if _voice_path() and Path(_voice_path()).is_file() and _piper_module():
        try:
            _synthesize_python("Hi.")
        except Exception as exc:  # noqa: BLE001
            logger.info("Piper not warmed up: %s", exc)
    logger.info("Speech warm-up done in %.1f s", time.monotonic() - started)


def _piper_bin() -> str | None:
    found = shutil.which(env.piper_bin) if env.piper_bin else None
    return found or (env.piper_bin if env.piper_bin and Path(env.piper_bin).is_file() else None)


def _piper_module() -> bool:
    try:
        import piper  # noqa: F401
    except ImportError:
        return False
    return True


_voice = None
_voice_loaded = ""  # path of the loaded voice: a different choice reloads it
_voice_lock = threading.Lock()


def _voice_path() -> str:
    from app import voices

    return voices.current_path()


def reset_voice() -> None:
    """Drop the loaded voice: the next reply loads the newly chosen one."""
    global _voice, _voice_loaded
    with _voice_lock:
        _voice, _voice_loaded = None, ""


def _speak(voice, text: str) -> bytes:
    import io
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        if hasattr(voice, "synthesize_wav"):  # piper-tts 1.3+
            voice.synthesize_wav(text, wav_file)
        else:
            voice.synthesize(text, wav_file)
    return to_reply_wav(buf.getvalue())


def _synthesize_python(text: str, voice_path: str | None = None) -> bytes:
    """piper-tts's Python API. The household voice stays loaded; any other (a Sidekick
    preview) is loaded just for this call."""
    global _voice, _voice_loaded
    from piper import PiperVoice

    path = voice_path or _voice_path()
    with _voice_lock:
        if voice_path and voice_path != _voice_loaded:
            return _speak(PiperVoice.load(voice_path), text)
        if _voice is None or _voice_loaded != path:
            _voice, _voice_loaded = PiperVoice.load(path), path
        return _speak(_voice, text)


def preview(voice_path: str, text: str) -> bytes:
    """A sample in a given installed voice (Sidekick → Voice → Preview)."""
    if env.speech == "dev":
        return chime(0.3)
    if not _piper_module():
        raise SpeechUnavailable("piper isn't installed")
    return _synthesize_python(text, voice_path)


def synthesize(text: str) -> bytes:
    """Text → mono 22.05 kHz WAV."""
    if env.speech == "dev":
        return chime(0.3)
    if env.speech == "off":
        raise SpeechUnavailable("speech is switched off")
    voice = _voice_path()
    if not voice or not Path(voice).is_file():
        raise SpeechUnavailable("no piper voice is set up")
    if _piper_module():
        try:
            return _synthesize_python(text)
        except Exception as exc:  # noqa: BLE001 - fall through to the binary, else report
            logger.warning("piper (python) failed: %s", exc)
            if not _piper_bin():
                raise SpeechUnavailable("text-to-speech failed") from exc
    binary = _piper_bin()
    if not binary:
        raise SpeechUnavailable("piper isn't installed")
    with tempfile.TemporaryDirectory(prefix="agentlink-tts-") as tmp:
        out = Path(tmp) / "reply.wav"
        result = subprocess.run(
            [binary, "--model", voice, "--output_file", str(out)],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0 or not out.is_file():
            logger.warning("piper failed (%s): %s", result.returncode, result.stderr[-300:])
            raise SpeechUnavailable("text-to-speech failed")
        return to_reply_wav(out.read_bytes())


def status() -> dict:
    """What Sidekick shows: can we hear, can we speak, and if not, why."""
    if env.speech == "dev":
        return {"mode": "dev", "stt": {"ok": True, "detail": "Dev stand-in"}, "tts": {"ok": True, "detail": "Dev chime"}}
    if env.speech == "off":
        off = {"ok": False, "detail": "Switched off"}
        return {"mode": "off", "stt": off, "tts": off}
    try:
        import faster_whisper  # noqa: F401

        stt = {"ok": True, "detail": f"Whisper {env.whisper_model}"}
    except ImportError:
        stt = {"ok": False, "detail": "faster-whisper isn't installed"}
    if not _piper_module() and not _piper_bin():
        tts = {"ok": False, "detail": "piper isn't installed"}
    elif not _voice_path() or not Path(_voice_path()).is_file():
        tts = {"ok": False, "detail": "No piper voice set up"}
    else:
        tts = {"ok": True, "detail": f"Piper {Path(_voice_path()).stem}"}
    return {"mode": "auto", "stt": stt, "tts": tts}


__all__ = ["ASK_RATE", "DEV_TRANSCRIPT", "SpeechUnavailable", "status", "synthesize", "transcribe"]
