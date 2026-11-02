#!/usr/bin/env python3
"""Transcribe an OBS PulseAudio monitor source in short rolling chunks."""

from __future__ import annotations

import argparse
from collections import deque
import io
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime

from dotenv import load_dotenv
from groq import Groq
from elevenlabs_tts import ElevenLabsTTS
from twitch_chat import TwitchChatBot

load_dotenv()

SAMPLE_RATE = 16_000
SAMPLE_WIDTH = 2  # signed 16-bit PCM
CHANNELS = 1
DEFAULT_CHUNK_SECONDS = 10
DEFAULT_OVERLAP_SECONDS = 1
DEFAULT_WINDOW_MINUTES = 5


class TranscriptBuffer:
    """Thread-safe, in-memory transcript retained for a rolling time window."""

    def __init__(self, retention_seconds: int) -> None:
        self._segments: deque[tuple[float, str]] = deque()
        self._lock = threading.Lock()
        self._retention_seconds = retention_seconds

    def add(self, timestamp: float, text: str) -> None:
        with self._lock:
            self._segments.append((timestamp, text))
            self._prune(time.time() - self._retention_seconds)

    def recent_text(self, window_seconds: int) -> str:
        cutoff = time.time() - window_seconds
        with self._lock:
            self._prune(cutoff)
            return "\n".join(
                f"[{datetime.fromtimestamp(ts).astimezone().strftime('%H:%M:%S')}] {text}"
                for ts, text in self._segments
            )

    def _prune(self, cutoff: float) -> None:
        while self._segments and self._segments[0][0] < cutoff:
            self._segments.popleft()


def list_sources() -> int:
    if not shutil.which("pactl"):
        print("pactl was not found. Install PulseAudio tools (pulseaudio-utils).", file=sys.stderr)
        return 1
    result = subprocess.run(["pactl", "list", "short", "sources"], text=True, check=False)
    return result.returncode


def wav_bytes(pcm: bytes) -> io.BytesIO:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(CHANNELS)
        wav.setsampwidth(SAMPLE_WIDTH)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    output.seek(0)
    return output


def remove_boundary_overlap(previous: str, current: str) -> str:
    """Trim repeated leading words caused by the one-second audio overlap."""
    previous_words = re.findall(r"[\w']+", previous.casefold())
    current_matches = list(re.finditer(r"[\w']+", current))
    current_words = [match.group(0).casefold() for match in current_matches]
    max_overlap = min(12, len(previous_words), len(current_words))
    overlap = next(
        (size for size in range(max_overlap, 0, -1)
         if previous_words[-size:] == current_words[:size]),
        0,
    )
    if not overlap:
        return current.strip()
    return current[current_matches[overlap - 1].end():].lstrip(" \t,.;:-")


def transcribe_worker(
    client: Groq,
    chunks: queue.Queue[tuple[float, bytes] | None],
    stop: threading.Event,
    transcript_buffer: TranscriptBuffer,
) -> None:
    previous_text = ""
    while not stop.is_set():
        try:
            item = chunks.get(timeout=0.25)
        except queue.Empty:
            continue
        if item is None:
            break

        started_at, pcm = item
        try:
            transcript = client.audio.transcriptions.create(
                file=("recap.wav", wav_bytes(pcm), "audio/wav"),
                model="whisper-large-v3-turbo",
                response_format="text",
                language="en",
            )
            text = remove_boundary_overlap(previous_text, str(transcript).strip())
            if text:
                stamp = datetime.fromtimestamp(started_at).astimezone().strftime("%H:%M:%S")
                print(f"[{stamp}] {text}", flush=True)
                transcript_buffer.add(started_at, text)
                previous_text = text
        except Exception as exc:  # keep capture alive through transient API errors
            print(f"Transcription request failed: {exc}", file=sys.stderr, flush=True)


def command_worker(
    client: Groq,
    transcript_buffer: TranscriptBuffer,
    stop: threading.Event,
    window_seconds: int,
    tts: ElevenLabsTTS | None = None,
) -> None:
    hint = f"Type !recap to summarize the last {window_seconds // 60} minutes."
    if tts is not None and tts.is_configured:
        hint += " Type !recap voice to hear it spoken."
    print(hint, flush=True)
    while not stop.is_set():
        command = sys.stdin.readline()
        if not command:
            return
        parts = command.strip().lower().split()
        if not parts or parts[0] != "!recap":
            continue
        want_voice = len(parts) > 1 and parts[1] == "voice"

        context = transcript_buffer.recent_text(window_seconds)
        if not context:
            print("No transcript yet. Let the stream run for a few seconds, then try !recap.", flush=True)
            continue
        print("Recap: thinking…", flush=True)
        try:
            response = client.chat.completions.create(
                model=os.getenv("GROQ_CHAT_MODEL", "openai/gpt-oss-120b"),
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are the streamer speaking directly to a viewer who just arrived. Write a "
                            "warm, casual, natural catch-up that could be sent in Twitch chat and spoken "
                            "aloud. Use only the transcript provided; do not invent events or facts. Address "
                            "the viewer as 'you' when natural. Use 'I' or 'we' only for things supported by "
                            "the transcript. Give 1-3 short sentences under 400 characters. Never use em dashes. "
                            "No labels, bullets, "
                            "markdown, or meta commentary. If context is thin, say briefly what is clear."
                        ),
                    },
                    {"role": "user", "content": f"Recent stream transcript:\n{context}"},
                ],
                temperature=0.2,
                # High enough that reasoning models finish their visible answer.
                max_completion_tokens=512,
            )
            recap = response.choices[0].message.content or "I couldn't form a recap from that transcript."
            print(f"\nRECAP: {recap.strip()}\n", flush=True)
            if want_voice:
                if tts is None or not tts.is_configured:
                    print("Voice recap needs ELEVENLABS_API_KEY.", flush=True)
                else:
                    tts.speak_async(recap.strip())
        except Exception as exc:
            print(f"Recap request failed: {exc}", file=sys.stderr, flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="recap.monitor", help="PulseAudio source name (default: recap.monitor)")
    parser.add_argument("--chunk-seconds", type=int, default=DEFAULT_CHUNK_SECONDS)
    parser.add_argument("--overlap-seconds", type=int, default=DEFAULT_OVERLAP_SECONDS)
    parser.add_argument("--window-minutes", type=int, default=DEFAULT_WINDOW_MINUTES,
                        help="How much recent transcript !recap can use (default: 5)")
    parser.add_argument("--twitch", action="store_true", help="Connect to Twitch EventSub chat")
    parser.add_argument("--list-sources", action="store_true", help="List PulseAudio sources and exit")
    parser.add_argument("--speak", action="store_true",
                        help="Enable ElevenLabs TTS for !recap voice (needs ELEVENLABS_API_KEY; "
                             "ELEVENLABS_VOICE_ID optional, else a stock voice is auto-selected)")
    parser.add_argument("--voice-id", default="",
                        help="Voice to speak with (default: ELEVENLABS_VOICE_ID, else auto-selected stock voice)")
    parser.add_argument("--elevenlabs-model", default="", help="Override ELEVENLABS_MODEL_ID")
    parser.add_argument("--tts-out-dir", default="tts_out", help="Directory for recap MP3s (default: tts_out)")
    parser.add_argument("--no-play", action="store_true", help="Save recap MP3s without playing them")
    parser.add_argument("--list-voices", action="store_true", help="List ElevenLabs voices and exit")
    parser.add_argument("--clone-voice", nargs="+", metavar=("NAME", "SAMPLE"),
                        help="Clone a voice: --clone-voice MyVoice sample1.mp3 [sample2.mp3 ...], prints the new voice ID and exits")
    args = parser.parse_args()

    if args.list_sources:
        return list_sources()

    def build_tts() -> ElevenLabsTTS:
        return ElevenLabsTTS(
            voice_id=args.voice_id,
            model_id=args.elevenlabs_model,
            out_dir=args.tts_out_dir,
            play=not args.no_play,
        )

    if args.list_voices:
        try:
            for voice in build_tts().list_voices():
                print(f"{voice['voice_id']}  {voice['name']}  [{voice['category']}]")
        except Exception as exc:
            print(f"Could not list voices: {exc}", file=sys.stderr)
            return 2
        return 0
    if args.clone_voice:
        name, *samples = args.clone_voice
        if not samples:
            parser.error("--clone-voice needs a NAME plus at least one audio sample file")
        try:
            voice_id = build_tts().clone_voice(name, samples)
        except Exception as exc:
            print(f"Could not clone voice: {exc}", file=sys.stderr)
            return 2
        print(f"Cloned voice '{name}': {voice_id}")
        print("Set ELEVENLABS_VOICE_ID to this value (or pass --voice-id).")
        return 0
    if args.chunk_seconds < 2 or not 0 <= args.overlap_seconds < args.chunk_seconds:
        parser.error("chunk-seconds must be >= 2 and overlap-seconds must be smaller than the chunk")
    if args.window_minutes < 1:
        parser.error("window-minutes must be at least 1")
    if not os.getenv("GROQ_API_KEY"):
        print("Set GROQ_API_KEY before starting capture.", file=sys.stderr)
        return 2
    if not shutil.which("ffmpeg"):
        print("ffmpeg was not found. Install it with: sudo apt install ffmpeg", file=sys.stderr)
        return 2

    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    chunks: queue.Queue[tuple[float, bytes] | None] = queue.Queue(maxsize=4)
    stop = threading.Event()
    transcript_buffer = TranscriptBuffer(args.window_minutes * 60)
    tts = build_tts() if args.speak else None
    if tts is not None:
        print(tts.status(), flush=True)
    chat_bot: TwitchChatBot | None = None
    if args.twitch:
        try:
            chat_bot = TwitchChatBot(client, transcript_buffer, args.window_minutes * 60, tts=tts)
            chat_bot.start()
        except Exception as exc:
            print(f"Could not start Twitch chat: {exc}", file=sys.stderr)
            return 2
    worker = threading.Thread(
        target=transcribe_worker,
        args=(client, chunks, stop, transcript_buffer),
        daemon=True,
    )
    worker.start()
    command_thread = threading.Thread(
        target=command_worker,
        args=(client, transcript_buffer, stop, args.window_minutes * 60, tts),
        daemon=True,
    )
    command_thread.start()

    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-f", "pulse", "-i", args.source,
        "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE),
        "-f", "s16le", "-acodec", "pcm_s16le", "pipe:1",
    ]
    print(f"Listening to {args.source} at {SAMPLE_RATE} Hz. Press Ctrl+C to stop.", flush=True)
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    bytes_per_second = SAMPLE_RATE * SAMPLE_WIDTH
    chunk_bytes = args.chunk_seconds * bytes_per_second
    overlap_bytes = args.overlap_seconds * bytes_per_second
    previous = b""
    buffered = bytearray()
    chunk_started = time.time()

    def stop_capture(_signum: int, _frame: object) -> None:
        stop.set()
        if process.poll() is None:
            process.send_signal(signal.SIGINT)

    signal.signal(signal.SIGINT, stop_capture)
    try:
        assert process.stdout is not None
        while not stop.is_set():
            block = process.stdout.read(32_000)
            if not block:
                break
            buffered.extend(block)
            while len(buffered) >= chunk_bytes:
                current = bytes(buffered[:chunk_bytes])
                del buffered[:chunk_bytes]
                pcm = previous + current
                try:
                    chunks.put_nowait((chunk_started - args.overlap_seconds, pcm))
                except queue.Full:
                    try:
                        chunks.get_nowait()
                    except queue.Empty:
                        pass
                    chunks.put_nowait((chunk_started - args.overlap_seconds, pcm))
                    print("Transcription is falling behind; skipped the oldest queued chunk.", file=sys.stderr, flush=True)
                previous = current[-overlap_bytes:] if overlap_bytes else b""
                chunk_started += args.chunk_seconds
    except KeyboardInterrupt:
        stop.set()
    finally:
        stop.set()
        if chat_bot:
            chat_bot.close()
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=5)
        try:
            chunks.put_nowait(None)
        except queue.Full:
            pass
        worker.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
