# cutroom

**Your AI agent as video editor.** Give Claude Code (or Codex, Cursor and other agents that read skills) a folder of raw footage and a goal. It plans the video, reads the transcript, cuts on words, places captions and overlays where they don't cover your face, adds animated text, image cards, music that ducks under speech and sound effects. Then it watches its own render and fixes what's wrong. No editing app, no browser renderer, no cloud upload: just Python and FFmpeg on your machine.

cutroom is mostly instructions: a workflow that tells the agent how an editor works and which tools to use, plus small helper scripts for the mechanical parts and FFmpeg recipes for everything else. The agent makes the decisions by looking at the frames.

```text
"Make a 40-second reel from these three takes. Hook first, captions, a quiet music bed."
"Cut a 5-minute travel vlog from this folder of 600 phone files, chronological, on the beat."
"Turn this screen recording into a tutorial and blur anything private."
```

cutroom grew out of editing real videos with Claude: talking-head shorts for social media and a nine-minute travel vlog assembled from about 600 phone photos and clips. The workflow, defaults and the troubleshooting list come from those edits.

## What it does

| | |
|---|---|
| **Plans before it cuts** | Writes a brief and a plan (hook, beats, which take goes where) and checks in with you before long renders |
| **Cuts on words** | Transcribes locally with Whisper, then cuts on the first and last word of a take, not on guessed seconds. Removes pauses and marks retakes, so the weaker takes stay out |
| **Captions off the face** | The agent checks where the face is in every shot (`faces.py`) and puts captions and overlays beside it, under the chin or above the head; the render warns when text covers other text. Bold pop-in style with the spoken word highlighted, or a clean style for landscape |
| **Motion** | Jump-cut zooms, punch-ins on key words, slow push-ins, Ken Burns on photos, hook titles, chips, lower thirds, title cards, typewriter text, image cards with rounded corners and shadow |
| **Sound** | Music with automatic ducking under speech, sound effects anchored to words, beat detection for cutting on the music, loudness normalized to -14 LUFS with a limiter that keeps the true peak under -1 dBTP |
| **Footage that fights back** | iPhone HDR to SDR without pink skin, variable frame rate, rotated clips, low-res messenger copies flagged, photos and HEIC |
| **Reviews itself** | Stills of every segment and overlay, contact sheets, a list of text that covers a face, black and frozen frame detection, silence check, loudness and true-peak report, QR code scan for privacy |
| **Hands off cleanly** | Optional package for CapCut, Premiere or DaVinci: picture with original sound, separate dialog, music and SFX stems, captions and cut markers |

Eight use-case profiles suggest how to approach each kind of video: talking-head shorts, screen recordings and tutorials, vlogs and event recaps, montages cut to music, podcast and interview clips, product and promo videos, long-form YouTube, and rough cuts for a human editor.

## How it works

```text
brief.md ─▶ probe ─▶ proxy ─▶ contact sheets ─▶ transcribe ─▶ plan.md ─▶ edit.json ─▶ render ─▶ faces + stills ─▶ qa
             IDs     SDR,      the agent looks    takes.md      hook,      the cut as    draft      the agent places    loudness,
             HDR?    const fps                    retakes       beats      data                     text, fix loop      black, QR
```

The agent writes `edit.json`, a small edit decision list, and the reference renderer `render.py` turns it into a video. Changing the cut means changing a line and re-rendering; unchanged segments come from the cache. Where captions and overlays go is written in `edit.json` too: the agent decides it from the face boxes and the review stills, the renderer doesn't guess. When the renderer can't do something, the agent uses FFmpeg directly with the tested recipes in [recipes.md](skills/cutroom/references/recipes.md).

```json
{
  "format": "reel",
  "tighten": 0.35,
  "captions": {"style": "bold", "emphasis": ["ffmpeg"], "position": 0.70},
  "clips": [
    {"src": "C002", "from": "I edited", "to": "without an app"},
    {"src": "C001", "from": "Here is", "to": "the trick", "zoom": 1.08, "punch": [{"word": "trick"}]}
  ],
  "overlays": [
    {"type": "hook", "text": "No editing app\nClaude cut this", "at": 0, "dur": 2.5, "y": 0.48},
    {"type": "image", "file": "assets/repo.png", "at": {"clip": 1, "word": "trick"}, "dur": 1.6, "pos": "center"}
  ],
  "music": {"file": "assets/music/bed.mp3", "vol": 0.2}
}
```

## Install

**Any agent that supports skills** (Claude Code, Codex, Cursor and others, via the [skills CLI](https://github.com/vercel-labs/skills)):

```bash
npx skills add sydney839/cutroom -g
```

**Claude Code plugin:**

```text
/plugin marketplace add sydney839/cutroom
/plugin install cutroom@cutroom
```

**Manually:** copy `skills/cutroom` into `~/.claude/skills/` (or your agent's skills folder).

### Requirements

- Python 3.10 or newer
- FFmpeg with ffprobe on PATH ([download](https://ffmpeg.org/download.html); on Windows the gyan.dev "full" build, on macOS `brew install ffmpeg`)
- Optional Python packages, installed when you need the feature:

```bash
pip install numpy pillow opencv-python-headless faster-whisper pillow-heif
```

`faster-whisper` transcribes locally (GPU if available, CPU otherwise). `opencv-python-headless` gives the face boxes and the QR scan (OpenCV 4.8 or newer, including 5.x). Everything runs offline after two one-time model downloads: the Whisper model and a 230 KB face detection model.

## Use it

Open your agent in any folder and describe the video. The skill triggers on requests like "edit this video", "make a reel from these clips", "add captions", "cut a vlog". The agent creates a project folder:

```text
my-video/
  brief.md  plan.md  edit.json
  assets/        music, sound effects, images, fonts you want to use
  work/          proxies, transcripts, takes.md, contact sheets, face boxes, review stills (rebuildable)
  exports/       my-video.mp4, captions.srt, edl.csv, qa.md, package/
```

Bring your own music and sound effects (your files or a royalty-free library) and put fonts you like into `assets/fonts/`. Your original files are never changed.

The scripts also work without an agent: `python skills/cutroom/scripts/render.py my-video` renders whatever `edit.json` says. Every script has `--help`.

| Script | Does |
|---|---|
| `probe.py` | Inventory with IDs, durations, resolution, HDR, timestamps and warnings |
| `proxy.py` | Edit proxies: SDR BT.709, constant frame rate, rotated photos, audio for transcription |
| `sheet.py` | Contact sheets of a video or of all media |
| `transcribe.py` | Local Whisper transcript with word timings, `takes.md` with retake markers |
| `beats.py` | Tempo, beat grid, downbeats and energy sections of a music track |
| `faces.py` | Face box and free space per segment; lists captions and overlays that cover a face |
| `render.py` | Reference renderer: `edit.json` to finished video, captions, cut list, layout and review stills |
| `qa.py` | Format, loudness, true peak, black/frozen frames, silence and QR code report |

Documentation for agents and humans: [SKILL.md](skills/cutroom/SKILL.md) (workflow), [recipes.md](skills/cutroom/references/recipes.md) (placing text, FFmpeg snippets), [edit-json.md](skills/cutroom/references/edit-json.md) (every key), [use-cases.md](skills/cutroom/references/use-cases.md), [planning.md](skills/cutroom/references/planning.md) (brief, hooks, structure), [troubleshooting.md](skills/cutroom/references/troubleshooting.md).

## Good to know

- **Speed:** a final render takes roughly one to three times the length of the video on a CPU (a 16-second 1080x1920 reel with captions, overlays and music: about 50 seconds on 4 CPU cores). NVENC is used when available. Drafts are faster, and re-renders after small changes reuse the cached segments.
- **Tested so far:** every script on Linux (Python 3.13, FFmpeg 6.1, OpenCV 4.14 and 5.0, faster-whisper 1.2) with generated test footage: HDR, rotated, variable frame rate, low-res, HEIC, a screen recording with a QR code, synthetic speech and music. Not yet verified: Windows and macOS, real iPhone footage and NVENC. Reports welcome as issues.
- **Not a generator:** cutroom edits footage you filmed. It does not generate video with AI models.
- **Your rights, your privacy:** the skill tells the agent to blur private details, skip ripped music and credit third-party material, but you are responsible for what you publish.

## Auf Deutsch

cutroom ist ein Skill, mit dem Claude Code und andere KI-Agenten Videos selbst schneiden: Rohmaterial rein, fertiges Video raus. Der Skill ist vor allem eine Anleitung: Er sagt dem Agenten, wie ein Cutter arbeitet und welche Werkzeuge er nutzt. Der Agent schreibt erst einen Plan, liest das Transkript, schneidet auf Wörter genau, schaut sich die Bilder an und setzt Untertitel und Einblendungen so, dass sie das Gesicht nicht verdecken, dazu kleine Animationen, Bildkarten, Musik und Sounds. Am Ende prüft er sein eigenes Ergebnis. Alles läuft lokal mit Python und FFmpeg, ohne Schnittprogramm und ohne Cloud. Installation: `npx skills add sydney839/cutroom -g`, dann einfach sagen, was für ein Video du willst.

## Credits

Hook and structure guidance in `references/planning.md` is paraphrased from [hooksmith](https://github.com/arhamhi/hooksmith), [vyralcontent/content-skills](https://github.com/vyralcontent/content-skills) and [coreyhaines31/marketingskills](https://github.com/coreyhaines31/marketingskills) (all MIT). The transcript-to-edit-list idea is shared with [browser-use/video-use](https://github.com/browser-use/video-use). `faces.py` uses the [YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) face detector from the OpenCV Zoo (MIT), downloaded on first use.

## License

MIT, see [LICENSE](LICENSE).
