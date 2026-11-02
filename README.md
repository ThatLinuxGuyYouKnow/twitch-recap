# Recap audio slice

> **Hackathon demo:** watch `recap-demo-video/recap-demo.mp4` (104s product demo) or run the interactive 1:1 Twitch simulation with `demo/twitch-demo.html` (open in a browser, press Space). See `SUBMISSION.md` for the stage script and Devpost checklist.
>
> **Production proof:** the bot's threaded `!recap` reply in a real Twitch chat popout, rebuilt 1:1 as `demo/production-popout.html` (screenshot: `screenshots/06-production-chat.png`).

This prototype captures an OBS monitored mix from a PulseAudio source, sends short WAV chunks to Groq speech-to-text, and prints timestamped transcript lines. Audio is only captured while this script is running. The script does not save audio to disk.

## Requirements

- Ubuntu 22.04 with OBS using PulseAudio (or a PulseAudio-compatible PipeWire setup)
- `ffmpeg`, `pulseaudio-utils`, Python 3.10+
- Groq and Twitch credentials (Twitch is only needed for `--twitch`)

Install system tools and Python dependencies:

```bash
sudo apt update
sudo apt install ffmpeg pulseaudio-utils
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Fill in the local `.env` file with your credentials, using `.env.example` as a reference. `.env` is ignored by Git; do not commit or share it. Recap loads this file on startup. Values already exported in the shell take precedence, so your current setup keeps working while you move credentials into `.env`.

## Route OBS audio into Recap

Create a virtual PulseAudio sink (repeat after audio-server restart):

```bash
pactl load-module module-null-sink sink_name=recap sink_properties=device.description=RecapCapture
```

In OBS, open **Settings → Audio → Advanced → Monitoring Device** and choose `RecapCapture`. In **Advanced Audio Properties**, enable monitoring for only the sources the streamer wants transcribed. In OBS versions that label the option **Monitoring Enabled**, select that; it sends a monitored copy to the selected device.

Check the monitor source exists:

```bash
pactl list short sources
```

You should see `recap.monitor`. If the source has a different name, pass it with `--source`.

## Run

With `GROQ_API_KEY` set in `.env`, run:

```bash
python recap_audio.py --list-sources
python recap_audio.py --source recap.monitor
```

The default chunk is 10 seconds with a 1 second overlap. Groq receives each chunk for transcription, so text appears with a short delay. Ctrl+C stops capture. If transcription requests fall behind, the bounded queue drops its oldest waiting chunk to keep the prototype near the live edge.

## Try a local recap

While the script is running, type `!recap` in the same terminal. Recap summarizes the last five minutes of transcript. Set another window with `--window-minutes 3`. The transcript is kept in memory only and disappears when the process exits. You can override the Groq chat model with `GROQ_CHAT_MODEL`; by default it uses `openai/gpt-oss-120b`.

## Connect Twitch chat

Create a Twitch developer application and configure the Twitch CLI with its Client ID and secret. Then authorize the broadcaster account with chat read/write scopes:

```bash
twitch token -u -s 'user:read:chat user:write:chat'
```

The Twitch CLI prints a user access token. Keep it private. For this MVP, the token must belong to the broadcaster whose channel is being read and posted to. Twitch chat is read through EventSub WebSocket and replies are sent through Twitch's Helix chat API. [Twitch token command](https://dev.twitch.tv/docs/cli/token-command/), [Twitch chat authentication](https://dev.twitch.tv/docs/chat/authenticating)

Add the Twitch values to `.env` (use the channel's login name, without `#`) and start Recap with `--twitch`:

```bash
python recap_audio.py --source recap.monitor --twitch
```

In chat, viewers can use `!recap` or `!recap stream` for the stream summary, and `!recap chat` for recent chat. The broadcaster can use `!note some context` to add a note that stays in memory for the current run and informs stream recaps. Viewers have a 20-second per-user recap cooldown.

## Speak recaps with ElevenLabs (cloned voice)

Recap can speak a recap in the streamer's ElevenLabs voice. No new Python dependency is needed (`elevenlabs_tts.py` uses the stdlib plus `ffplay` for playback).

1. Get an API key from ElevenLabs and clone the voice once: either in the ElevenLabs dashboard (**Voices → Add voice**) or from the terminal:
   ```bash
   python recap_audio.py --clone-voice MyVoice sample1.mp3 sample2.mp3
   ```
   This prints a voice ID. Existing voices can be listed with `python recap_audio.py --list-voices`.
2. Add to `.env`:
   ```
   ELEVENLABS_API_KEY=
   ELEVENLABS_VOICE_ID=your-cloned-voice-id
   ELEVENLABS_MODEL_ID=eleven_turbo_v2_5
   ELEVENLABS_PULSE_SINK=recap_tts
   ```
   Set `ELEVENLABS_VOICE_ID` to the cloned voice ID to use the streamer's clone. If left blank, Recap chooses a stock voice.
3. Create the dedicated audio sink (again after an audio-server restart):
   ```bash
   pactl load-module module-null-sink sink_name=recap_tts sink_properties=device.description=RecapTTS
   ```
   In OBS, add an **Audio Input Capture** source and select the monitor source for `RecapTTS` (usually `Monitor of RecapTTS`). Keep this source's monitoring off; it should reach the stream output but not the `RecapCapture` monitor that Recap transcribes. `ffplay` sends generated speech to the `recap_tts` sink via `ELEVENLABS_PULSE_SINK`.
4. Start Recap with `--speak` (combine with `--twitch` as usual):
   ```bash
   python recap_audio.py --source recap.monitor --twitch --speak
   ```
   Useful extras: `--voice-id` (override the env voice), `--tts-out-dir` (where recap MP3s are saved, default `tts_out/`), `--no-play` (save MP3s without playing them).

Usage:

- Terminal: `!recap` prints the text recap, `!recap voice` also speaks it.
- Twitch chat: `!recap voice` (or `!recap stream voice` / `!recap chat voice`) replies with the text recap and plays the audio through OBS. Voice recaps share the 20-second per-user cooldown plus a 30-second global throttle to control API cost.
- Twitch broadcaster only: `!voice <text>` speaks arbitrary text (also handy for testing the voice).

Each spoken recap is saved as an MP3 in `tts_out/` (timestamped) and played in the background via `ffplay` into the dedicated OBS sink.

## Current boundary

This prototype does not persist notes or transcripts between runs, or provide a dashboard/OAuth web flow. Twitch credentials are supplied as environment variables and must be refreshed when expired. Voice cloning reuses ElevenLabs' hosted API (samples you upload are subject to their data handling). Verify the selected OBS sources and Groq data handling before using it on a live stream.
