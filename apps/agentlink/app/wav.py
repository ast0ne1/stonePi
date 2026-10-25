"""PCM WAV in and out for Pebble asks (PROTOCOL.md §7): 16 kHz up, 22.05 kHz back."""

from __future__ import annotations

import array
import io
import math
import sys
import wave

ASK_RATE = 16000
REPLY_RATE = 22050


class BadAudio(Exception):
    pass


def read_pcm16(body: bytes, *, rate: int | None = ASK_RATE) -> tuple[array.array, int]:
    """Mono 16-bit samples and their rate. ``rate`` set: anything else is BadAudio."""
    try:
        with wave.open(io.BytesIO(body), "rb") as w:
            if w.getcomptype() != "NONE" or w.getnchannels() != 1 or w.getsampwidth() != 2:
                raise BadAudio("not mono 16-bit PCM")
            got = w.getframerate()
            if rate is not None and got != rate:
                raise BadAudio(f"{got} Hz, want {rate} Hz")
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as exc:
        raise BadAudio(str(exc) or "not a WAV file") from exc
    samples = array.array("h")
    samples.frombytes(frames[: len(frames) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    return samples, got


def to_wav(samples: array.array, rate: int) -> bytes:
    pcm = array.array("h", samples)
    if sys.byteorder == "big":
        pcm.byteswap()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def resample(samples: array.array, src: int, dst: int) -> array.array:
    """Linear resample (speech only: good enough, no dependencies)."""
    if src == dst or not samples:
        return array.array("h", samples)
    n_out = int(len(samples) * dst / src)
    step = src / dst
    last = len(samples) - 1
    out = array.array("h", bytes(2 * n_out))
    for i in range(n_out):
        pos = i * step
        j = int(pos)
        a = samples[j]
        b = samples[min(j + 1, last)]
        out[i] = int(a + (b - a) * (pos - j))
    return out


def to_reply_wav(body: bytes) -> bytes:
    """Any mono/stereo 16-bit WAV (e.g. from piper) → mono 22.05 kHz, as the contract says."""
    with wave.open(io.BytesIO(body), "rb") as w:
        channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
        frames = w.readframes(w.getnframes())
    if width != 2:
        raise BadAudio("TTS output must be 16-bit")
    samples = array.array("h")
    samples.frombytes(frames[: len(frames) // 2 * 2])
    if sys.byteorder == "big":
        samples.byteswap()
    if channels == 2:
        samples = array.array("h", ((samples[i] + samples[i + 1]) // 2 for i in range(0, len(samples) - 1, 2)))
    return to_wav(resample(samples, rate, REPLY_RATE), REPLY_RATE)


def level_dbfs(samples: array.array) -> float:
    if not samples:
        return -120.0
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    return 20 * math.log10(max(rms, 1.0) / 32768)


def chime(seconds: float = 0.0) -> bytes:
    """Two-note chime (+ optional silence): dev-mode speech stand-in."""
    out = array.array("h")
    for freq in (880.0, 1320.0):
        n = int(REPLY_RATE * 0.12)
        for i in range(n):
            fade = min(1.0, i / 200, (n - i) / 200)
            out.append(int(9000 * fade * math.sin(2 * math.pi * freq * i / REPLY_RATE)))
    out.extend([0] * int(REPLY_RATE * seconds))
    return to_wav(out, REPLY_RATE)
