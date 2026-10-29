# Use cases

Pick the closest profile, tell the user which one you chose, and adapt it to the brief. The values are starting points, not rules: the brief wins.

## 1. Talking-head short (Reels, TikTok, Shorts)

One person speaking to the camera, often several takes.

- **Format** `reel`, 20 to 60 s (30 to 45 s is the sweet spot for explanations).
- **Structure** hook (0 to 3 s) -> one sentence of context -> payoff -> loop back or one CTA. Pull the strongest sentence to the front even if it was said last.
- **Edit** cut on words, `tighten` 0.3 to 0.4, last take of a repeated sentence wins, no filler, no dead air. Alternate `zoom` 1.0 and 1.06 to 1.12 between cuts, 2 to 3 `punch` accents per 30 s on key words.
- **Captions** `bold`, auto placement beside the face, 2 to 4 `emphasis` words, `fixes` for brand names Whisper gets wrong.
- **Overlays** a `hook` in the first 2.5 s that says something different from the spoken line (the number or result, while the voice gives the why). 1 to 2 overlays per 10 s at most: `chip` for keywords, `image` cards for what you talk about. A `cta` at the end only if the brief has one.
- **Audio** quiet music bed (vol 0.15 to 0.2) or none, one soft sound per overlay at most.
- **Check** the first frame works without sound and paused; captions never on the face.

## 2. Screen recording, tutorial, app demo

Screen capture, often with a voice-over or a face cam.

- **Format** `reel` for social clips, `landscape` for full tutorials.
- **Structure** result first (show the finished thing for 2 s), then the steps in order, each step announced by a `chip` or numbered `text`.
- **Edit** cut waiting time hard (loading spinners, typing), `speed` 1.5 to 3 for unavoidable waits, `zoom_to` push-ins or `focus` + `zoom` on the part of the screen that matters (text must be readable on a phone).
- **Captions** `caption_pos: "top"` on screen segments (dark box, readable over UI text), `auto` on face segments.
- **Privacy** scan every frame for emails, tokens, API keys, customer data, QR codes: `blur` them. Run `qa.py` (it lists QR codes).
- **Audio** voice-over clean, music very low or none.

## 3. Vlog, travel or event recap

Lots of clips and photos from phones and cameras, several people, music carries the video.

- **Format** `landscape` for YouTube and private sharing, `reel` for a 30 to 60 s recap. Long vlogs 3 to 10 min.
- **Prep** `probe.py` on everything, then `sheet.py --all`. Build `chronology.csv` (ID, day, time, scene, reason, sure yes/no) from timestamps and content clues (daylight, location, clothing per day, people). Messenger copies have no metadata and low resolution: ask for originals. Remove duplicates (same shot saved twice) before choosing.
- **Structure** cold open (5 to 8 s of the best moments) -> title card -> chapters (per day or location) with small title cards -> group moment and outro. Name places only when they are clearly recognizable; no invented stories.
- **Edit** videos carry the story, photos are accents: bursts of 0.3 to 0.6 s per photo on the beat, or single strong photos for 1.5 to 2.5 s with Ken Burns. Best 1.5 to 5 s of each clip, longer only for real moments. Never two near-identical shots in a row. Portrait clips in a landscape video: `fit: "blur"`.
- **Original sound** 3 to 6 moments where speech or laughter carries: `vol` 1.0 with captions; everything else `vol` 0.1 to 0.2, `duck: false`.
- **Music** 2 to 3 tracks along the arc (energetic start, mood in the middle, strong finale), changes at chapter borders. Run `beats.py` and cut on beats (`"beats": 2` per clip, or durations from the beat grid). Whoosh on chapter changes, a soft impact on title cards.
- **Privacy** leave out screenshots, chats, tickets, maps, bank apps and documents. Ask before showing people who are not part of the group.
- **Process** for more than 5 min of output: render a short version first and let the user react, then the long one. Deliver with `--package` so the user can swap music in CapCut.

## 4. Montage cut to music (highlights, sports, product b-roll)

- **Format** any; 15 to 60 s.
- **Edit** `beats.py` first; clip lengths in beats (1 beat for fast sections, 2 to 4 beats for calm ones). Strongest shot on the drop (`sections` in the beat file). Match motion direction across cuts.
- **Audio** music 0.6 to 0.9, original sound off (`vol` 0) or as an accent.
- **Captions** usually none; a `title` at the start and a `cta` or logo at the end.

## 5. Podcast or interview clip

Two or more people, long recordings, the clip is a highlight.

- **Format** `reel` 30 to 90 s or `landscape` 1 to 3 min.
- **Find** read `takes.md` for self-contained moments: a strong statement, a story with a punchline, a disagreement. The clip must make sense without the rest.
- **Edit** `tighten` 0.3, cut crosstalk and "yeah, yeah", reframe on the active speaker (`focus` per clip, separate clips per speaker turn).
- **Captions** `bold` or `karaoke`; `lower_third` with name and role once per speaker.
- **Audio** loudness -14 for social, -16 for podcast platforms. No music under dialogue, or very low.

## 6. Product, promo or explainer from footage and images

- **Format** per platform; 15 to 45 s for ads, up to 2 min for explainers.
- **Structure** problem -> product in action -> benefit -> proof -> CTA. Show the product early.
- **Edit** b-roll and screen footage on top of the voice-over (voice clip with `vol` 1.0, picture clips with `vol` 0 and matching durations), `image` cards for screenshots and logos, `text` with `typewriter` for short claims.
- **Honesty** only claims the user can back up; mark ads as ads when the platform or law requires it.

## 7. Long-form YouTube (talking head, landscape)

- **Format** `landscape`, 5 to 20 min.
- **Edit** `tighten` 0.4, keep natural rhythm (less aggressive than shorts), b-roll or `image` cards every 20 to 40 s, `punch` sparingly. Chapters as `title` overlays or cards.
- **Captions** often off (the platform generates them) or `clean`; deliver `captions.srt` for upload.
- **Audio** -14 LUFS, light music under intro and transitions only.

## 8. Rough cut or assembly for a human editor

The user wants to finish in CapCut, Premiere or DaVinci.

- Do selects and order, `tighten`, no styling: captions off, no overlays, no music.
- Render with `--package`; hand over `markers.csv` and the stems. Say what you left out and why.
