"""ElevenLabs text-to-speech for Recap (stdlib only, no new dependencies).

Uses a cloned (or stock) ElevenLabs voice to speak recap text out loud on the
streamer's machine and/or save it as an MP3. Configure with:

    ELEVENLABS_API_KEY=...   # required to enable TTS
    ELEVENLABS_VOICE_ID=...  # your cloned voice ID (from dashboard or --clone-voice)
    ELEVENLABS_MODEL_ID=...  # optional, default: eleven_turbo_v2_5

Voice cloning itself happens once, up front, either in the ElevenLabs dashboard
(Voices -> Add voice) or via ``python recap_audio.py --clone-voice NAME file1.mp3 ...``.
This module only needs the resulting voice ID at recap time.
"""

from __future__ import annotations

import io
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

API_BASE = "https://api.elevenlabs.io/v1"
DEFAULT_MODEL_ID = "eleven_turbo_v2_5"
MAX_CHARS = 5_000  # ElevenLabs per-request text limit


def _api_key() -> str:
    return os.getenv("ELEVENLABS_API_KEY", "").strip()


class ElevenLabsTTS:
    """Thin wrapper around the ElevenLabs REST API using only the stdlib."""

    def __init__(
        self,
        api_key: str = "",
        voice_id: str = "",
        model_id: str = "",
        out_dir: str = "tts_out",
        play: bool = True,
    ) -> None:
        self.api_key = (api_key or _api_key()).strip()
        self.voice_id = (voice_id or os.getenv("ELEVENLABS_VOICE_ID", "")).strip()
        self.model_id = (model_id or os.getenv("ELEVENLABS_MODEL_ID", "") or DEFAULT_MODEL_ID).strip()
        self.out_dir = out_dir
        self.play = play
        self.pulse_sink = os.getenv("ELEVENLABS_PULSE_SINK", "recap_tts").strip()
        self._lock = threading.RLock()

    @property
    def is_configured(self) -> bool:
        # A voice ID is optional: without one, the first synthesis auto-selects
        # a stock voice via list_voices(). Only the API key is required.
        return bool(self.api_key)

    def status(self) -> str:
        if not self.api_key:
            return "ElevenLabs TTS is disabled: set ELEVENLABS_API_KEY."
        if not self.voice_id:
            return (
                "ElevenLabs TTS ready (no ELEVENLABS_VOICE_ID set; a stock voice "
                f"will be auto-selected. Set one to use your cloned voice, model={self.model_id})."
            )
        return f"ElevenLabs TTS ready (voice={self.voice_id}, model={self.model_id})."

    # -- low-level HTTP -------------------------------------------------------

    def _headers(self, content_type: str = "application/json") -> dict[str, str]:
        return {"xi-api-key": self.api_key, "Content-Type": content_type}

    def _require_config(self) -> None:
        if not self.api_key:
            raise RuntimeError("Set ELEVENLABS_API_KEY to use ElevenLabs TTS.")
        if not self.voice_id:
            with self._lock:
                if not self.voice_id:
                    self.voice_id = self._resolve_default_voice()

    def _resolve_default_voice(self) -> str:
        """Pick a stock voice when no voice ID is configured. Cached in self.voice_id."""
        voices = self.list_voices()
        if not voices:
            raise RuntimeError(
                "ElevenLabs returned no voices; set ELEVENLABS_VOICE_ID explicitly "
                "(clone with --clone-voice or pick one with --list-voices)."
            )
        chosen = next(
            (v for v in voices if v.get("name", "").casefold() == "rachel"),
            None,
        )
        if chosen is None:
            premade = [v for v in voices if v.get("category", "").casefold() == "premade"]
            chosen = premade[0] if premade else voices[0]
        print(
            f"No ELEVENLABS_VOICE_ID set; using stock voice "
            f"'{chosen['name']}' ({chosen['voice_id']}).",
            flush=True,
        )
        return chosen["voice_id"]

    # -- voices ---------------------------------------------------------------

    def list_voices(self) -> list[dict]:
        """Return [{'voice_id': ..., 'name': ..., 'category': ...}, ...]."""
        if not self.api_key:
            raise RuntimeError("Set ELEVENLABS_API_KEY first.")
        request = urllib.request.Request(
            f"{API_BASE}/voices", headers={"xi-api-key": self.api_key}
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"ElevenLabs voices request failed (HTTP {exc.code}): {exc.read().decode()[:300]}") from exc
        return [
            {"voice_id": v.get("voice_id", ""), "name": v.get("name", ""), "category": v.get("category", "")}
            for v in payload.get("voices", [])
        ]

    def clone_voice(self, name: str, audio_paths: list[str], description: str = "") -> str:
        """Upload 1+ voice samples and return the new cloned voice ID (IVC)."""
        if not self.api_key:
            raise RuntimeError("Set ELEVENLABS_API_KEY first.")
        if not name.strip():
            raise ValueError("Give the cloned voice a name.")
        for path in audio_paths:
            if not os.path.isfile(path):
                raise FileNotFoundError(f"Sample not found: {path}")
        boundary = uuid.uuid4().hex
        body = io.BytesIO()
        fields: dict[str, str] = {"name": name.strip(), "remove_background_noise": "true"}
        if description.strip():
            fields["description"] = description.strip()
        for key, value in fields.items():
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
            body.write(f"{value}\r\n".encode())
        for path in audio_paths:
            filename = os.path.basename(path)
            mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            body.write(f"--{boundary}\r\n".encode())
            body.write(
                f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'.encode()
            )
            body.write(f"Content-Type: {mime}\r\n\r\n".encode())
            with open(path, "rb") as handle:
                shutil.copyfileobj(handle, body)
            body.write(b"\r\n")
        body.write(f"--{boundary}--\r\n".encode())
        request = urllib.request.Request(
            f"{API_BASE}/voices/add",
            data=body.getvalue(),
            method="POST",
            headers={"xi-api-key": self.api_key, "Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"ElevenLabs voice cloning failed (HTTP {exc.code}): {exc.read().decode()[:500]}") from exc
        voice_id = str(payload.get("voice_id", ""))
        if not voice_id:
            raise RuntimeError(f"ElevenLabs did not return a voice ID: {payload}")
        return voice_id

    # -- synthesis ------------------------------------------------------------

    def synthesize(self, text: str) -> bytes:
        """Convert text to MP3 bytes. Raises on misconfiguration or API error."""
        self._require_config()
        text = " ".join(text.split())
        if not text:
            raise ValueError("Nothing to speak: recap text was empty.")
        if len(text) > MAX_CHARS:
            text = text[:MAX_CHARS]
            print(f"Recap text truncated to {MAX_CHARS} chars for TTS.", file=sys.stderr)
        body = json.dumps({
            "text": text,
            "model_id": self.model_id,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }).encode()
        url = f"{API_BASE}/text-to-speech/{self.voice_id}?output_format=mp3_44100_128"
        request = urllib.request.Request(url, data=body, method="POST", headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError(f"ElevenLabs TTS failed (HTTP {exc.code}): {details}") from exc

    def speak(self, text: str, prefix: str = "recap") -> str | None:
        """Synthesize, save MP3 under out_dir, optionally play it. Returns file path."""
        audio = self.synthesize(text)
        os.makedirs(self.out_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self.out_dir, f"{prefix}_{stamp}.mp3")
        with open(path, "wb") as handle:
            handle.write(audio)
        print(f"Saved recap audio: {path}", flush=True)
        if self.play:
            self.play_file(path)
        return path

    def speak_async(self, text: str, prefix: str = "recap") -> threading.Thread:
        """Non-blocking speak: synthesis + playback happen on a daemon thread."""
        thread = threading.Thread(target=self._speak_guarded, args=(text, prefix), daemon=True)
        thread.start()
        return thread

    def _speak_guarded(self, text: str, prefix: str) -> None:
        with self._lock:  # one voice at a time; avoids overlapping recap audio
            try:
                self.speak(text, prefix)
            except Exception as exc:
                print(f"Recap TTS failed: {exc}", file=sys.stderr, flush=True)

    # -- playback -------------------------------------------------------------

    def play_file(self, path: str) -> None:
        """Play an MP3 in the background. Uses ffplay, falls back to aplay/paplay."""
        player = shutil.which("ffplay")
        if player:
            player_env = os.environ.copy()
            if self.pulse_sink:
                player_env["PULSE_SINK"] = self.pulse_sink
            subprocess.Popen(
                [player, "-nodisp", "-autoexit", "-loglevel", "error", path],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=player_env,
            )
            return
        for candidate in ("paplay", "aplay"):
            if shutil.which(candidate):
                print(f"ffplay not found; {candidate} cannot play MP3 directly, file saved at {path}.", flush=True)
                return
        print(f"No audio player found (install ffplay); file saved at {path}.", flush=True)
