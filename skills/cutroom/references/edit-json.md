# edit.json reference

`edit.json` lives in the project folder. `render.py`, the reference renderer, turns it into the video; for anything it cannot do, use FFmpeg directly ([recipes.md](recipes.md)). Paths are relative to the project folder unless absolute. Keys starting with `_` are notes and are ignored, so you can leave yourself comments like `"_why": "stronger take"`.

## Top level

| Key | Default | Meaning |
|---|---|---|
| `format` | `"reel"` | `reel`/`vertical` 1080x1920, `portrait` 1080x1350, `square` 1080x1080, `landscape`/`youtube` 1920x1080, `4k` 3840x2160, or `{"w": 1080, "h": 1920, "fps": 30}` |
| `fps` | 30 | Frame rate when `format` is a preset name |
| `target_duration` | none | `[min, max]` in seconds; the render warns when the cut lands outside |
| `tighten` | off | Remove pauses longer than this many seconds inside word-anchored clips (0.3 to 0.45 for talking heads; leave off for vlogs) |
| `style` | | `font` (default `Montserrat`, falls back to Arial with a warning; put `.ttf` files into `assets/fonts/`), `accent` color `#RRGGBB` (default `#FFD60A`) |
| `captions` | see below | Caption settings |
| `grade` | none | Color for every clip: `contrast` (1.0 neutral, 1.03 to 1.08 punchy), `saturation` (1.0 neutral), `brightness` (0 neutral, -0.05 to 0.05), `gamma`, `lut` (path to a `.cube` file), `vignette` (true/false) |
| `clips` | required | The cut, in playback order |
| `overlays` | `[]` | Text, images and blur boxes on top |
| `music` | none | One entry or a list |
| `sfx` | `[]` | Sound effects |
| `beats` | none | Beat grid for `"beats": n` clip lengths: `"work/beats/track.json"` or `{"file": ..., "at": 0, "start": 0}` where `at` is the timeline time the track starts and `start` the offset into the track (match your `music` entry) |
| `duck` | `{"threshold": 0.025, "ratio": 8, "attack": 20, "release": 450}` | How strongly music dips under speech (sidechain compressor). Higher `ratio` = deeper dip |
| `loudness` | -14 | Integrated loudness target in LUFS. -14 for social and YouTube, -16 for podcasts, -23 for broadcast |

## Clips

Every clip is one of three kinds.

**Video:** `{"src": "C003", ...}` where `src` is an inventory ID (uses the proxy), a project-relative path or an absolute path.

| Key | Meaning |
|---|---|
| `from`, `to` | Cut on words: the first word(s) and the last word(s) of the take, as they appear in `work/takes.md`. Matching ignores case and punctuation; one word or a short phrase. `from_n`/`to_n` pick the n-th occurrence when a phrase repeats. The cut gets 0.08 s before and 0.15 s after |
| `in`, `out` | Cut on seconds of the source instead (or `in` plus `dur`) |
| `trim_in`, `trim_out` | Nudge the start later or the end earlier, in seconds |
| `transcript` | Transcript path when `src` is not an ID |
| `tighten` | Override the global pause removal for this clip (`0` turns it off) |
| `speed` | 0.25 to 4; audio pitch is kept |
| `vol` | Original sound volume (1.0). 0 mutes it. Clips with `vol` 0 get no captions |
| `duck` | Whether this clip's sound pushes the music down. Default: true when `vol` >= 0.5. Set false for ambient sound under a montage |
| `fit` | `cover` (fill and crop, default), `contain` (letterbox), `blur` (whole picture over a blurred copy of itself: portrait clips in a landscape video and the other way round) |
| `focus` | `[x, y]` 0..1, the point that stays in frame when cropping or zooming. Default `[0.5, 0.45]`. For a person on the left of a landscape clip in a reel: `[0.3, 0.45]` |
| `zoom` | Static scale, 1.0 = fill. Alternate 1.0 and 1.05 to 1.12 between talking-head cuts so jump cuts feel intentional |
| `zoom_to` | Animate the zoom from `zoom` to this value over the clip (slow push-in 1.0 -> 1.06) |
| `punch` | Quick zoom accents: `[{"word": "trick", "n": 1, "amt": 0.1, "hold": 0.8}]` or `{"at": 1.2}` in seconds into the clip. Two or three per minute at most |
| `fade_in`, `fade_out` | Fade from/to black, seconds |
| `grade` | Per-clip override of the global grade |
| `captions` | `false` = no captions on this clip |
| `caption_pos` | Where this clip's captions go: `default` (y 0.70 vertical, 0.86 landscape), `top` (on a dark box near the top, for screen recordings), `center`, `bottom` (lowest spot inside the safe zone), a y value (0..1 of the height, or pixels) or `[x, y]` for the block center. Decide it from `faces.py` and the stills, see [recipes.md](recipes.md) |
| `beats` | Make the clip exactly n beats long (needs top-level `beats`) |
| `note` | Shows up in `exports/edl.csv` |

**Photo:** `{"src": "P012", "dur": 1.5}` with `fit`, `focus`, `zoom`, `grade`, `fade_in`/`fade_out`, `beats` like video clips, plus `kenburns`: `true`/`"in"` (default, slow push-in), `"out"` or `false`.

**Card:** `{"card": "DAY 2", "dur": 1.5, "bg": "#111111"}` is a plain colored frame. Put the text on it with a `title` overlay anchored to the clip (`"at": {"clip": 5}`, `"until": {"clip": 5, "from_end": true}`).

## Time anchors

Overlays, sounds and music start at an anchor:

- `"at": 3.2` seconds on the final timeline
- `"at": {"clip": 4}` start of clip 4 (index in `clips`, from 0; `-1` is the last clip), with `"offset": 0.3` to shift
- `"at": {"clip": 4, "from_end": true, "offset": -0.5}` relative to the end of clip 4
- `"at": {"clip": 4, "word": "Claude", "n": 1, "offset": 0}` the moment the word is spoken. This follows the cut automatically

Duration: `"dur": 1.5`, `"until": <anchor>`, or `"to_end": true`.

## Captions

```json
"captions": {"style": "bold", "max_words": 3, "emphasis": ["claude", "ffmpeg"],
             "fixes": {"cloud": "Claude", "codecs": "Codex"}, "drop": ["ähm", "äh", "uh", "um"]}
```

| Key | Meaning |
|---|---|
| `enabled` | false = no captions |
| `style` | `bold` (default for vertical: big uppercase chunks of up to 3 words, pop-in, active word in the accent color), `clean` (default for landscape: sentence case on a translucent box, up to 7 words), `karaoke` (sentence case, active word highlighted), `none` |
| `max_words`, `size`, `upper`, `highlight`, `pop`, `box`, `outline` | Override parts of the style. `size` as a fraction of the height (0.05) or pixels |
| `position` | Default `caption_pos` for all clips (same values) |
| `emphasis` | Words always shown in the accent color (substring match) |
| `fixes` | Replace misheard words (whole words, case-insensitive) |
| `drop` | Words never shown (fillers). The sound still plays: cut the take so fillers are not spoken |

Placement: captions go exactly where `caption_pos` says. The renderer does not look for faces; you do, with `faces.py` after a draft render (see [recipes.md](recipes.md)). Safe zone for vertical video: y between 0.14 and 0.78 of the height (platform buttons and captions sit outside); the render warns when captions or overlays reach outside it or cover each other.

## Overlays

Every overlay has an anchor (`at`) and a duration (`dur`/`until`/`to_end`). Text types take `anim`: `pop`, `fade`, `rise`, `slide`, `typewriter` (only `text` and `chip`), `none`.

**Position:** `"pos": [x, y]` is the center of the block (0..1 of the frame, or pixels; for `lower_third` x is the left edge). Or set only `"y"` (and `"x"`). Without them, each type has a fixed default spot, which is a starting point, not a decision:

| Type | Default y vertical | Default y landscape |
|---|---|---|
| `hook` | 0.22 | 0.18 |
| `chip`, `text` | 0.31 | 0.14 |
| `title` | 0.50 | 0.50 |
| `lower_third` | 0.70 (x 0.06) | 0.80 (x 0.06) |
| `cta` | 0.66 | 0.84 |
| `image` | `top` 0.30, `center` 0.50, `bottom` 0.62 (landscape 0.70) | |

In vertical talking-head footage the default hook and chip spots (0.22, 0.31) are usually on the face. Run `faces.py` after the first draft and move them into a free band.

| `type` | Keys | Use |
|---|---|---|
| `hook` | `text` (a second line after `\n` gets an accent box) | The opening promise, first 2 to 3 seconds. 3 to 7 words |
| `chip` | `text`, `box_color` | Short keyword label next to what is said |
| `text` | `text`, `pos` `[x, y]` (0..1 or px), `box` true/false | Free text |
| `title` | `text`, `sub` | Title or chapter card, centered. Usually on a `card` clip |
| `lower_third` | `text`, `sub` | Name and role of a speaker, slides in from the left |
| `cta` | `text`, `handle` | Call to action pill near the end |
| `image` | `file`, `layout` (`card`: rounded with shadow, default; `full`: fills the frame), `width` (0..1 of the frame width, default 0.8), `pos` (`top`, `center`, `bottom` or `[x, y]`), `y` (overrides the y of `top`/`center`/`bottom`), `rounded` | Screenshots, logos, photos of what you talk about. Crop screenshots wide (2:1 to 4:1) so they fit between chin and captions |
| `blur` / `pixelate` | `box` `[x, y, w, h]` (0..1 or px), `strength` | Hide private details: QR codes, plates, screens |

## Music and sound effects

```json
"music": [{"file": "assets/music/intro.mp3", "at": 0, "until": {"clip": 6}, "start": 12.5, "vol": 0.25,
           "fade_in": 0.3, "fade_out": 1.5, "duck": true},
          {"file": "assets/music/main.mp3", "at": {"clip": 6}, "vol": 0.22}],
"sfx": [{"file": "assets/sfx/whoosh.wav", "at": {"clip": 3, "offset": -0.12}, "vol": 0.35}]
```

`start` skips into the track (start on a strong part or a beat). Music ducks under every clip whose `duck` is true. Typical levels: music bed 0.15 to 0.25 under speech, 0.5 to 0.8 when music carries the video alone; sound effects 0.2 to 0.5.

## Outputs

- `exports/<project>.mp4` (or `--out`), H.264, AAC, BT.709, `+faststart`
- `exports/captions.srt` the caption chunks with final timing
- `exports/edl.csv` every segment with timeline and source times
- `exports/package/` with `--package`: `picture_with_dialog.mp4`, `dialog.wav`, `music.wav`, `sfx.wav`, `captions.srt`, `captions.ass`, `markers.csv`, `README.txt`
- `work/review/*.jpg` stills named by timeline time
- `work/render/layout.json` every segment and every caption chunk, text and image block with its time and pixel box; `faces.py` reads it
- `work/render/base.mp4` the cut without text and overlays (what `faces.py` scans)
