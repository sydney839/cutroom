---
name: cutroom
description: Edit real footage into finished videos with Python and FFmpeg only, no editing app. The agent writes a brief and a plan, reads the transcript, writes an edit decision list (edit.json), renders it with word-accurate cuts, face-aware captions, animated text, image overlays, blur boxes, music with ducking and sound effects, then reviews its own output (stills, loudness, black/frozen frames, QR codes) before handing over. Use when the user wants to edit, cut or assemble videos - reels, shorts, TikToks, talking-head clips, vlogs, travel or event recaps, tutorials and screen recordings, podcast or interview clips, product and promo videos, montages cut to music - or wants captions or subtitles burned in, a rough cut, a hand-off package for CapCut, Premiere or DaVinci, or says "edit my video", "cut this footage", "make a reel from these clips".
---

# cutroom

You edit video the way a human editor does: understand the goal, watch and listen to the material, plan the story, cut, then watch the result and fix it. The difference is that every step is a file you write or a script you run, so the edit is reproducible and easy to change.

Scripts are in `scripts/` next to this file. Call them by absolute path with `python -X utf8 <this-skill>/scripts/<name>.py`. They need **Python 3.10+** and **FFmpeg** on PATH. Optional: `numpy`, `Pillow`, `opencv-python-headless` (face-aware captions, QR scan), `faster-whisper` (transcripts), `pillow-heif` (iPhone HEIC photos). The scripts tell you the exact `pip install` command when something is missing.

## Ground rules

- **Never modify, move or delete the user's originals.** Everything you generate goes into the project folder.
- **Look at your own output.** You can read images: open contact sheets and review stills, don't guess. A render is not done until you have looked at it and run `qa.py`.
- **Checkpoints with the human.** Confirm the brief before you plan, show the plan before a long render (anything over 2 minutes of output or more than 20 source files), and stop after two or three self-correction rounds to ask instead of looping.
- **Nothing private on screen.** Check the material for chats, emails, tokens, bank or ticket screens, QR codes, license plates and bystanders' faces where the user would object. Blur them (`blur` overlay) or leave the shot out. Ask when unsure.
- **Rights.** Use music and sound effects the user owns or that are licensed for this use (their own files, royalty-free libraries). Never rip music from streaming services. Third-party clips, posts or screenshots need the user's own commentary and a source credit.
- **Honest content.** Don't invent numbers, results or quotes in hooks, captions or titles. Everything on screen must be true for this video.
- **Never publish or upload.** Posting is the user's step.
- Long renders (more than about 2 minutes) run in the background. Render one thing at a time; parallel FFmpeg jobs fight over the CPU/GPU.

## Workflow

### 1. Brief

Find out, from the request or by asking briefly: what the video is for, platform and format, target length, audience, tone, must-have moments, things to avoid, music source, and whether captions are wanted. Pick the matching use case in [references/use-cases.md](references/use-cases.md); it sets defaults for format, length, structure, captions, pacing and music. Write `brief.md` in the project folder (template in [references/planning.md](references/planning.md)). If there is no footage yet, write a shooting script or shot list instead and stop: the user films first.

### 2. Inventory and first look

```
python -X utf8 scripts/probe.py <project> <files or folders>   # IDs, durations, resolution, HDR, timestamps -> work/inventory.csv
python -X utf8 scripts/proxy.py <project>                      # SDR edit proxies, 16 kHz audio, rotated photos
python -X utf8 scripts/sheet.py <project> --all                # contact sheets of everything -> work/sheets/
```

Pass the probe warnings on to the user (low resolution usually means a messenger copy; ask for originals). Open the contact sheets. For many files (vlogs, events) build a chronology first: timestamps where they exist, otherwise content clues like daylight, location and clothing. Never assign a whole range of IDs to one scene without looking at each file.

### 3. Listen

```
python -X utf8 scripts/transcribe.py <project> [IDs] --language de
```

Read `work/takes.md`, not the JSON. It lists every phrase with source times, marks probable retakes (`<- retake of`) and uncertain words (`?word?`). Of several takes of the same sentence, the last one is usually the good one.

### 4. Plan

Write `plan.md`: the hook, the structure (beats in order), and for each beat the source ID and the words or time range you will use, plus overlays, music and sound ideas. Rules for hooks, scripts and structure: [references/planning.md](references/planning.md). For music-driven edits run `beats.py` on the track and plan clip lengths in beats. Show the plan to the user when the checkpoint rule above applies.

### 5. Edit decision list

Write `edit.json`. Full reference: [references/edit-json.md](references/edit-json.md). The essentials:

```json
{
  "format": "reel",
  "tighten": 0.35,
  "style": {"font": "Montserrat", "accent": "#FFD60A"},
  "captions": {"style": "bold", "emphasis": ["claude"], "fixes": {"cloud": "Claude"}},
  "clips": [
    {"src": "C002", "from": "I built", "to": "in one night", "zoom": 1.0},
    {"src": "C001", "from": "Here is", "to": "the trick", "zoom": 1.08, "punch": [{"word": "trick"}]},
    {"src": "C004", "in": 3.0, "out": 6.5, "caption_pos": "top", "vol": 0.6}
  ],
  "overlays": [
    {"type": "hook", "text": "No editing app\nClaude cut this", "at": 0, "dur": 2.5},
    {"type": "chip", "text": "Python + FFmpeg", "at": {"clip": 1, "word": "trick"}},
    {"type": "image", "file": "assets/repo.png", "at": {"clip": 2, "offset": 0.3}, "dur": 1.6},
    {"type": "cta", "text": "Link in bio", "at": {"clip": -1, "offset": 0.5}, "to_end": true}
  ],
  "music": {"file": "assets/music/bed.mp3", "vol": 0.22},
  "sfx": [{"file": "assets/sfx/whoosh.wav", "at": {"clip": 1, "offset": -0.15}, "vol": 0.35}]
}
```

Cut on words (`from`/`to`), not on guessed seconds: the renderer pads and aligns the cut. `tighten` removes pauses inside a clip. Overlays and sounds anchor to words or clips, so they move with the cut when you change it.

### 6. Draft, look, fix

```
python -X utf8 scripts/render.py <project> --draft
```

Read the console: one line per segment with its caption zone, then warnings (`FALLBACK` captions over a face, overlays colliding with captions, missing fonts, too-short pieces). Open the stills in `work/review/` (one per segment, overlay and the ends; build a contact sheet with `sheet.py --video exports/<name>.mp4 --every 1` for a denser look). Check:

- captions never cover eyes, nose or mouth, and stay out of the platform UI zones
- text is readable and spelled right (compare against `exports/captions.srt`)
- overlays are visible and don't collide; cards don't sit on the face
- no black flashes, frozen frames, sideways clips or wrong colors (pink or washed-out skin means an HDR clip skipped the proxy step)
- the story works: hook first, no repeated sentences, no dead air, cuts don't clip words

Fix `edit.json` and render again. Segments are cached, so re-renders after small changes are fast.

### 7. Final and QA

```
python -X utf8 scripts/render.py <project> [--package]
python -X utf8 scripts/qa.py <project>/exports/<name>.mp4 --project <project>
```

`qa.py` checks format, loudness and true peak, black and frozen stretches, long silences and QR codes, and writes `exports/qa.md`. Fix every FAIL. `--package` adds a hand-off folder for CapCut, Premiere or DaVinci: picture with original sound, separate dialog, music and sound-effect stems, captions and cut markers.

### 8. Hand over

Report: the output path, its length, the structure in a few lines, music and sounds used with their source, QA results, known weaknesses and open questions. Offer the next step (shorter cut, other format, swap the music) instead of doing it unasked. If the video is for social media and the user wants it, draft a caption and title in their voice, with credits for third-party material.

## When things go wrong

See [references/troubleshooting.md](references/troubleshooting.md): HDR colors, variable frame rate, Whisper hallucinations, GPU crashes, fonts, clicks at cuts, sync drift, word anchors that are not found.
