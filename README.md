# cutroom

**A video editing skill for AI agents.** Give Claude Code (or Codex, Cursor and other agents that read skills) a folder of raw footage and a goal. It plans the video, reads the transcript, cuts on words, adds captions that stay off your face, animated text, image cards, music that ducks under speech and sound effects. Then it watches its own render and fixes what's wrong. No editing app, no browser renderer, no cloud upload: just Python and FFmpeg on your machine.

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
| **Cuts on words** | Transcribes locally with Whisper, then cuts on the first and last word of a take, not on guessed seconds. Removes pauses, keeps the last of several retakes |
| **Face-aware captions** | Detects the face in every shot and places captions under the chin or above the head, never over eyes and mouth. Bold pop-in style with the spoken word highlighted, or a clean style for landscape |
| **Motion** | Jump-cut zooms, punch-ins on key words, slow push-ins, Ken Burns on photos, hook titles, chips, lower thirds, title cards, typewriter text, image cards with rounded corners and shadow |
| **Sound** | Music with automatic ducking under speech, sound effects anchored to words, beat detection for cutting on the music, loudness normalized to -14 LUFS with a true-peak limiter |
| **Footage that fights back** | iPhone HDR to SDR without pink skin, variable frame rate, rotated clips, low-res messenger copies flagged, photos and HEIC |
| **Reviews itself** | Stills of every segment and overlay, contact sheets, black and frozen frame detection, silence check, loudness report, QR code scan for privacy |
| **Hands off cleanly** | Optional package for CapCut, Premiere or DaVinci: picture with original sound, separate dialog, music and SFX stems, captions and cut markers |

Eight use-case profiles tell the agent how to approach each kind of video: talking-head shorts, screen recordings and tutorials, vlogs and event recaps, montages cut to music, podcast and interview clips, product and promo videos, long-form YouTube, and rough cuts for a human editor.

## How it works

```text
brief.md ─▶ probe ─▶ proxy ─▶ contact sheets ─▶ transcribe ─▶ plan.md ─▶ edit.json ─▶ render ─▶ review ─▶ qa
             IDs     SDR,      the agent looks    takes.md      hook,      the cut as    one        stills,   loudness,
             HDR?    const fps                    retakes       beats      data          command    fix loop  black, QR
```

The agent never edits pixels by hand. It writes `edit.json`, a small edit decision list, and `render.py` turns it into a video. Changing the cut means changing a line and re-rendering; unchanged segments come from the cache.

```json
{
  "format": "reel",
  "tighten": 0.35,
  "captions": {"style": "bold", "emphasis": ["ffmpeg"]},
  "clips": [
    {"src": "C002", "from": "I edited", "to": "without an app"},
    {"src": "C001", "from": "Here is", "to": "the trick", "zoom": 1.08, "punch": [{"word": "trick"}]}
  ],
  "overlays": [
    {"type": "hook", "text": "No editing app\nClaude cut this", "at": 0, "dur": 2.5},
    {"type": "image", "file": "assets/repo.png", "at": {"clip": 1, "word": "trick"}, "dur": 1.6}
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

`faster-whisper` transcribes locally (GPU if available, CPU otherwise). `opencv-python-headless` enables face-aware captions and the QR scan. Everything runs offline after the Whisper model download.

## Use it

Open your agent in any folder and describe the video. The skill triggers on requests like "edit this video", "make a reel from these clips", "add captions", "cut a vlog". The agent creates a project folder:

```text
my-video/
  brief.md  plan.md  edit.json
  assets/        music, sound effects, images, fonts you want to use
  work/          proxies, transcripts, takes.md, contact sheets, review stills (rebuildable)
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
| `render.py` | `edit.json` to finished video, captions, cut list and review stills |
| `qa.py` | Format, loudness, black/frozen frames, silence and QR code report |

Documentation for agents and humans: [SKILL.md](skills/cutroom/SKILL.md) (workflow), [edit-json.md](skills/cutroom/references/edit-json.md) (every key), [use-cases.md](skills/cutroom/references/use-cases.md), [planning.md](skills/cutroom/references/planning.md) (brief, hooks, structure), [troubleshooting.md](skills/cutroom/references/troubleshooting.md).

## Good to know

- **Speed:** rendering runs at roughly 1x to 3x real time on a laptop; NVENC is used when available. A 40-second reel takes a minute or two, a 9-minute vlog the better part of an hour.
- **Not a generator:** cutroom edits footage you filmed. It does not generate video with AI models.
- **Your rights, your privacy:** the skill tells the agent to blur private details, skip ripped music and credit third-party material, but you are responsible for what you publish.

## Auf Deutsch

cutroom ist ein Skill, mit dem Claude Code und andere KI-Agenten Videos selbst schneiden: Rohmaterial rein, fertiges Video raus. Der Agent schreibt erst einen Plan, liest das Transkript, schneidet auf Wörter genau, setzt Untertitel neben das Gesicht, kleine Animationen, Bildkarten, Musik und Sounds und prüft am Ende sein eigenes Ergebnis. Alles läuft lokal mit Python und FFmpeg, ohne Schnittprogramm und ohne Cloud. Installation: `npx skills add sydney839/cutroom -g`, dann einfach sagen, was für ein Video du willst.

## Credits

Hook and structure guidance in `references/planning.md` is paraphrased from [hooksmith](https://github.com/arhamhi/hooksmith), [vyralcontent/content-skills](https://github.com/vyralcontent/content-skills) and [coreyhaines31/marketingskills](https://github.com/coreyhaines31/marketingskills) (all MIT). The transcript-to-edit-list idea is shared with [browser-use/video-use](https://github.com/browser-use/video-use).

## License

MIT, see [LICENSE](LICENSE).
