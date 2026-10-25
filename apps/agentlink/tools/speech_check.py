"""Speech check for Pebble asks: how fast are piper and faster-whisper on this machine?

Speaks a few sentences with the configured piper voice, then transcribes that audio back
with faster-whisper, and prints the timings. Uses AgentLink's own settings (AGENTLINK_*), so
it measures exactly what an ask will do. Run from apps/agentlink as the service user, e.g.
through ``sudo stonepi-speech-setup --check-only``.

The first run also downloads the whisper model into the data folder, so time it twice.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import speech  # noqa: E402
from app.config import env  # noqa: E402
from app.wav import ASK_RATE, REPLY_RATE, read_pcm16, resample  # noqa: E402

SENTENCES = (
    "StonePi is healthy. All twelve services are running and the last backup was yesterday.",
    "Your next event is jazz in the park, tomorrow at seven in the evening.",
    "I don't know that for this house yet, but in general it takes about twenty minutes.",
)


def main() -> int:
    status = speech.status()
    print(f"Speech mode {status['mode']}: listening {status['stt']['detail']}; speaking {status['tts']['detail']}")
    if not (status["stt"]["ok"] and status["tts"]["ok"]):
        print("Not ready: run `sudo stonepi-speech-setup` first.")
        return 1
    rows = []
    for text in SENTENCES:
        t = time.monotonic()
        wav = speech.synthesize(text)
        tts_s = time.monotonic() - t
        samples, _rate = read_pcm16(wav, rate=REPLY_RATE)
        audio_s = len(samples) / REPLY_RATE
        t = time.monotonic()
        heard = speech.transcribe(resample(samples, REPLY_RATE, ASK_RATE))
        stt_s = time.monotonic() - t
        rows.append({"audio_s": round(audio_s, 1), "tts_s": round(tts_s, 2), "stt_s": round(stt_s, 2), "heard": heard})
        print(f"  {audio_s:4.1f} s of speech · piper {tts_s:5.2f} s · whisper {stt_s:5.2f} s · heard: {heard!r}")
    # The first call of each engine loads its model; the later ones are what an ask costs.
    warm = rows[1:] or rows
    avg_tts = sum(r["tts_s"] for r in warm) / len(warm)
    avg_stt = sum(r["stt_s"] for r in warm) / len(warm)
    avg_audio = sum(r["audio_s"] for r in warm) / len(warm)
    print(f"Warm average for ~{avg_audio:.1f} s of speech: piper {avg_tts:.2f} s, whisper {avg_stt:.2f} s "
          f"(model {env.whisper_model}). An ask adds the AI reply (usually 1-3 s) on top.")
    print(json.dumps({"whisper_model": env.whisper_model, "voice": Path(env.piper_voice).stem, "rows": rows}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
