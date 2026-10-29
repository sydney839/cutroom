---
name: cutroom
description: Edit real footage into finished videos with Python and FFmpeg, no editing app. The agent works as the editor: brief and plan, read the transcript, cut on words, place captions and overlays by looking at the frames (off faces, off each other), add animated text, image cards, blur boxes, music with ducking and sound effects, then review its own output. Includes helper scripts (inventory, HDR-safe proxies, local Whisper, contact sheets, beats, face boxes, QA), a reference renderer for an edit decision list (edit.json) and FFmpeg recipes. Use when the user wants to edit, cut or assemble videos - reels, shorts, TikToks, talking-head clips, vlogs, travel or event recaps, tutorials and screen recordings, podcast or interview clips, promo videos, montages cut to music - or wants captions burned in, a rough cut or a hand-off package for CapCut, Premiere or DaVinci.
---

# cutroom

You edit video the way a human editor does: understand the goal, watch and listen to the material, plan the story, cut, then watch the result and fix it. The difference is that every step is a file you write or a command you run, so the edit is reproducible and easy to change.

You are the editor. The scripts do the mechanical work (measure, convert, transcribe, render, check); every decision about story, timing, framing and where text goes is yours, made by looking at the material. When a script cannot do what the video needs, use FFmpeg directly: [references/recipes.md](references/recipes.md).

## Tools

- **Python 3.10+ and FFmpeg** (with ffprobe) on PATH. Optional Python packages: `numpy`, `Pillow`, `opencv-python-headless` (face boxes, QR scan), `faster-whisper` (local transcripts), `pillow-heif` (iPhone HEIC photos). The scripts print the exact `pip install` command when something is missing.
- **Helper scripts** in `scripts/` next to this file. Call them by absolute path: `python -X utf8 <this-skill>/scripts/<name>.py`. Every script has `--help`.

| Script | Use it to |
|---|---|
| `probe.py` | give every file an ID and find HDR, variable frame rate, rotation, low resolution, timestamps |
| `proxy.py` | make edit proxies: SDR BT.709, constant frame rate, rotated photos, 16 kHz audio for Whisper |
| `sheet.py` | make contact sheets you can look at (all files, or one video every N seconds) |
| `transcribe.py` | get word timestamps (local Whisper) and `takes.md` with retakes marked |
| `beats.py` | get tempo, beats, downbeats and energy jumps of a music track |
| `faces.py` | get face boxes and free space per segment, and find text that covers a face |
| `render.py` | render `edit.json` into the video (reference renderer), with captions, overlays, mix, stills |
| `qa.py` | check the export: format, loudness, true peak, black/frozen frames, silence, QR codes |

## Ground rules

- **Never modify, move or delete the user's originals.** Everything you generate goes into the project folder.
- **Look at your own output.** You can read images: open contact sheets and review stills, don't guess. A render is not done until you have looked at it and run `qa.py`.
- **Faces stay free.** Captions, titles, chips, cards and CTAs never cover eyes, nose or mouth. You place them; `faces.py` and the stills confirm it.
- **Checkpoints with the human.** Confirm the brief before you plan, show the plan before a long render (anything over 2 minutes of output or more than 20 source files), and stop after two or three self-correction rounds to ask instead of looping.
- **Nothing private on screen.** Check the material for chats, emails, tokens, bank or ticket screens, QR codes, license plates and bystanders' faces where the user would object. Blur them or leave the shot out. Ask when unsure.
- **Rights.** Use music and sound effects the user owns or that are licensed for this use (their own files, royalty-free libraries). Never rip music from streaming services. Third-party clips, posts or screenshots need the user's own commentary and a source credit.
- **Honest content.** Don't invent numbers, results or quotes in hooks, captions or titles. Everything on screen must be true for this video.
- **Never publish or upload.** Posting is the user's step.
- Long renders (more than about 2 minutes) run in the background. Render one thing at a time; parallel FFmpeg jobs fight over the CPU/GPU.

## Workflow

### 1. Brief

Find out, from the request or by asking briefly: what the video is for, platform and format, target length, audience, tone, must-have moments, things to avoid, music source, and whether captions are wanted. Pick the matching use case in [references/use-cases.md](references/use-cases.md); it suggests format, length, structure, captions, pacing and music. Write `brief.md` in the project folder (template in [references/planning.md](references/planning.md)). If there is no footage yet, write a shooting script or shot list instead and stop: the user films first.

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
    {"type": "hook", "text": "No editing app\nClaude cut this", "at": 0, "dur": 2.5, "y": 0.48},
    {"type": "chip", "text": "Python + FFmpeg", "at": {"clip": 1, "word": "trick"}, "y": 0.47},
    {"type": "image", "file": "assets/repo.png", "at": {"clip": 2, "offset": 0.3}, "dur": 1.6, "pos": "center"},
    {"type": "cta", "text": "Link in bio", "at": {"clip": -1, "offset": 0.5}, "to_end": true, "y": 0.55}
  ],
  "music": {"file": "assets/music/bed.mp3", "vol": 0.22},
  "sfx": [{"file": "assets/sfx/whoosh.wav", "at": {"clip": 1, "offset": -0.15}, "vol": 0.35}]
}
```

Cut on words (`from`/`to`), not on guessed seconds: the renderer pads and aligns the cut. `tighten` removes pauses inside a clip. Overlays and sounds anchor to words or clips, so they move with the cut when you change it.

**Positions are yours.** Captions go where `caption_pos` says (per clip, or `captions.position` for all), overlays where `pos`/`y` says; otherwise each has a fixed default spot that knows nothing about the picture. For the first draft the defaults are fine; then place things properly in step 6.

### 6. Draft, look, place, fix

```
python -X utf8 scripts/render.py <project> --draft
python -X utf8 scripts/faces.py <project>
```

Read the render console: one line per segment with its caption y, then warnings (text covering other text, text in the platform UI zone, missing fonts, too-short pieces, duration outside the target). `faces.py` prints for every segment where the face is, which bands above the head and below the chin are free, and every caption chunk or overlay that covers a face (`COVERS FACE`).

Place captions and overlays from those numbers: captions in the bigger free band beside the face and in one steady place per scene, hook and chips in a band the captions don't use at that moment, image cards between chin and captions, the CTA off the mouth and off the captions. The rules and typical positions per format: [references/recipes.md](references/recipes.md#placing-captions-and-overlays).

Then open the stills in `work/review/` (one per segment, overlay and the ends; `sheet.py <project> --video exports/<name>.mp4 --every 1` gives a denser sheet). Check:

- captions and overlays never cover eyes, nose or mouth, never cover each other, and stay out of the platform UI zones
- text is readable and spelled right (compare against `exports/captions.srt`)
- no black flashes, frozen frames, sideways clips or wrong colors (pink or washed-out skin means an HDR clip skipped the proxy step)
- the story works: hook first, no repeated sentences, no dead air, cuts don't clip words

Fix `edit.json` and render again. Segments are cached, so re-renders after small changes are fast. If the edit needs something the renderer can't do (a split screen, a mask, a special transition, a different caption look), render that part with FFmpeg ([recipes.md](references/recipes.md#editing-without-the-renderer)) and use the result as a clip, or build the whole video with your own FFmpeg commands.

### 7. Final and QA

```
python -X utf8 scripts/render.py <project> [--package]
python -X utf8 scripts/qa.py <project>/exports/<name>.mp4 --project <project>
```

`qa.py` checks format, loudness and true peak, black and frozen stretches, long silences and QR codes, and writes `exports/qa.md`. Fix every FAIL; look at every WARN (a held title card is "frozen" on purpose). `--package` adds a hand-off folder for CapCut, Premiere or DaVinci: picture with original sound, separate dialog, music and sound-effect stems, captions and cut markers.

### 8. Hand over

Report: the output path, its length, the structure in a few lines, music and sounds used with their source, QA results, known weaknesses and open questions. Offer the next step (shorter cut, other format, swap the music) instead of doing it unasked. If the video is for social media and the user wants it, draft a caption and title in their voice, with credits for third-party material.

## When things go wrong

See [references/troubleshooting.md](references/troubleshooting.md): HDR colors, variable frame rate, Whisper hallucinations, GPU crashes, fonts, text on faces, clicks at cuts, sync drift, word anchors that are not found.
