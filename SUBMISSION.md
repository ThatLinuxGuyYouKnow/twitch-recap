# Submission pack: Codex × TwitchCon Hackathon (deadline Nov 2, 2pm PST)

## What you have
| Artifact | Path | Status |
|---|---|---|
| Product demo video (104s, <3min ✅) | `recap-demo-video/recap-demo.mp4` | Rendered, verified. **Upload to YouTube (public) and link it.** |
| Interactive stage demo (1:1 Twitch sim) | `demo/twitch-demo.html` | Playwright-verified, zero JS errors. Open in any browser, `4× speed`, Space to play. |
| Playwright proof | `demo/verify.cjs` + `screenshots/` | All checks pass. |
| Production proof (real Twitch `!recap` reply) | `demo/production-popout.html` + `screenshots/06-production-chat.png` | Rebuilt 1:1 from the live capture; swap native pixels in at the same path. |
| Working project | `recap_audio.py`, `twitch_chat.py`, `elevenlabs_tts.py` | As submitted repo. |

## Before you submit
1. **Repo URL**: push this folder to GitHub (keep `.env` out, it is gitignored). Replace `github.com/you/recap` in the video outro (`repo_url` variable in `recap-demo-video/index.html`, then re-render ~6 min). Or leave it and put the real link in the YouTube description + Devpost.
2. **YouTube**: upload `recap-demo.mp4` as public/unlisted? Rules say publicly visible. Title suggestion: "Recap: Never miss the stream again (Codex × TwitchCon)".
3. **Codex Session ID**: Devpost requires the Codex thread ID where most of the build happened (`/status` in Codex). This demo film + sim were built here; make sure your main Codex thread covers the product code.

## Project description (paste-ready draft)
**Recap** is a live catch-up bot for Twitch. It listens to the stream through OBS, transcribes speech in rolling 10-second chunks with Groq's whisper-large-v3-turbo, and keeps a 5-minute rolling transcript in memory. Nothing is saved to disk. When a newcomer types `!recap`, Recap writes a warm 1–3 sentence catch-up from the real transcript with Groq's gpt-oss-120b and posts it straight into chat via Helix, threaded as a reply. `!recap voice` speaks the recap out loud in the streamer's own ElevenLabs cloned voice, routed back into the broadcast through a dedicated OBS sink. The broadcaster can pin trusted context with `!note`, summarize chat separately with `!recap chat`, and stays protected by per-user cooldowns plus a global voice throttle that caps TTS cost. Built with OpenAI Codex: Codex wired the Twitch EventSub WebSocket flow, the Helix chat calls, the overlap-deduping transcription loop, and the ElevenLabs stdlib client.

## Stage demo script (2 min)
1. Open `demo/twitch-demo.html`, set `4× speed`, hit **Play** (or Space). Talk over the pipeline lighting up.
2. Click **✨ Auto-demo**: newcomer arrives, types `!recap`, bot answers from events that actually happened.
3. Click **🔊 !recap voice**: recap spoken aloud (browser voice on stage; ElevenLabs clone in production).
4. Click **📝 Broadcaster !note**, then ask `!recap` again. The note shapes the answer.
5. Close: "Recap. Never miss the stream again." Keys: `R` recap · `C` chat recap · `V` voice · `N` note · `Space` play/pause.
6. Receipt: show `demo/production-popout.html` (real-chat proof: the threaded `!recap` reply in a Twitch popout, rebuilt 1:1 from the live capture).
