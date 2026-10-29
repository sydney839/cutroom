# Recipes: placing text, and editing without the renderer

`render.py` is one way to render. It covers the common cases, but you are the editor: you decide where things go, and when the renderer cannot do something, you do it with FFmpeg directly. This file has both: how to place captions and overlays, and tested FFmpeg/ASS snippets.

## Placing captions and overlays

The renderer puts text exactly where `edit.json` says, or at a fixed default per format. It never moves anything to avoid a face. That is your job, and it is quick:

1. **Render a draft** with the defaults: `render.py <project> --draft`.
2. **Get the face boxes:** `faces.py <project>`. For every segment it prints the face box (x and y as fractions of the frame) and the free bands above the head and below the chin inside the safe zone, then lists every caption chunk or overlay that covers a face (`COVERS FACE`).
3. **Read the render warnings:** text that covers other text, text in the platform UI zone.
4. **Decide**, write the positions into `edit.json`, render again, run `faces.py` again, and **look at the stills** in `work/review/`. The numbers are a help; the stills are the truth.

You can also run `faces.py <project> --video C003` on a source clip before the first render to plan. Keep in mind that `zoom`, `focus` and `fit` move the face in the output; the boxes after a render are the ones that count.

### Sizes to plan with (fractions of the frame height)

| Element | Vertical 1080x1920 | Landscape 1920x1080 |
|---|---|---|
| `bold` caption, 1 line / 2 lines | 0.06 / 0.12 | 0.06 / 0.12 |
| `clean` caption, 1 line / 2 lines | 0.07 / 0.12 | 0.07 / 0.12 |
| `hook`, 1 line / 2 lines | 0.07 / 0.13 | 0.11 / 0.20 |
| `chip`, `cta` (1 line) | 0.05 | 0.07 |
| `image` card, `width` 0.8 | 0.8 x frame width x image aspect, plus about 8 % shadow margin | same |

Positions are block **centers**. A block at y 0.70 with height 0.12 spans 0.64 to 0.76.

### Rules that hold for every format

- **Faces are off limits:** no text or card on eyes, nose or mouth. Hair and the top of the head are acceptable for a short hook only if there is no other room.
- **One element per band at a time.** Split the frame into bands (above the head, below the chin, bottom). At any moment each band holds one thing: captions *or* a chip *or* a card. When two things must show at once, give them different bands, or shift one in time (`at`, `until`).
- **Safe zone:** vertical video keeps everything between y 0.14 and 0.78 (platform buttons, captions and the progress bar cover the rest). Landscape between 0.06 and 0.93.
- **Captions stay in one place** inside a scene. Moving them every cut is tiring to read; move them only when the face moves (new framing, zoom) or another element needs the band.
- **Check the end:** the CTA usually arrives while captions are still running. In a real edit the CTA landed on the speaker's mouth (default y with the face sitting low in the frame) and a chip covered the captions. So: put the CTA in a free band below the chin and above the captions (or above the head), or turn captions off for the last clip (`"captions": false`) and give the CTA the caption band.

### Talking head, vertical (Reels, TikTok, Shorts)

Typical phone framing puts the face between y 0.15 and 0.45. Then:

- **Captions** below the chin: default 0.70 usually works. If the free band below the chin starts lower than about 0.62 (face low in frame, close-up), set `caption_pos` to the middle of that band, or above the head when that band is bigger, or reframe with `focus`/`zoom` so the face moves up.
- **Hook** (first 2 to 3 s) in the band below the chin, above the captions (for example 0.47 to 0.52), or above the head when the face is low. Never over the face, even for 2 seconds: the first frame decides the scroll.
- **Chips** next to the word they label, in the band the captions do not use at that moment.
- **Image cards** between chin and captions (`"pos": "center"`, `"y": 0.5`) for wide screenshots; end them before the next shot if that shot frames the face differently (`"until": {"clip": n}`).
- **CTA** see above.

### Landscape (YouTube, vlogs, interviews)

- **Captions** at the bottom (default 0.86). A person on one side leaves room on the other: `"caption_pos": [0.68, 0.5]` puts captions beside a person standing on the left.
- **Lower thirds** bottom left (default x 0.06, y 0.80). Don't show one while captions run in the same band: move it up (`"pos": [0.06, 0.7]`) or start it in a pause.
- **Titles** centered on cards or over calm b-roll.

### Screen recordings

Captions where the action is *not*: `caption_pos: "top"` (dark box) when the interesting part of the screen is low, `"bottom"` when it is high. Cover private details with `blur`/`pixelate` boxes; `qa.py` reports QR codes.

## Editing without the renderer

Everything below is plain FFmpeg (version 5 or newer). Use these when `render.py` cannot do what the edit needs, or to build a custom pipeline. Edit from the proxies (`work/proxy/<ID>.mp4`: SDR, constant frame rate), keep intermediate files in `work/`, and re-encode every piece with the same settings so the pieces can be joined without re-encoding again.

On Windows, run FFmpeg from the folder that contains `.ass` files and use relative paths inside filters: a drive-letter colon (`C:`) breaks filter option parsing. Make text images with Pillow rather than `drawtext`: some Windows builds crash in `drawtext` without fontconfig.

### Cut a piece (frame-accurate)

```bash
ffmpeg -ss 3.20 -t 2.80 -i work/proxy/C003.mp4 -c:v libx264 -crf 18 -preset veryfast -r 30 -pix_fmt yuv420p \
  -c:a aac -b:a 192k -ar 48000 -ac 2 -af "afade=t=in:d=0.012,afade=t=out:st=2.788:d=0.012" work/cut_01.mp4
```

`-ss` before `-i` seeks fast and, with re-encoding, exactly. The 12 ms audio fades prevent clicks at the cut.

### Join pieces

Pieces encoded with identical settings join without re-encoding:

```bash
printf "file 'cut_01.mp4'\nfile 'cut_02.mp4'\n" > work/list.txt
ffmpeg -f concat -safe 0 -i work/list.txt -c copy work/joined.mp4
```

Pieces that differ (size, frame rate, codec) need the concat filter:

```bash
ffmpeg -i a.mp4 -i b.mp4 -filter_complex \
  "[0:v]scale=1080:1920,setsar=1,fps=30[v0];[1:v]scale=1080:1920,setsar=1,fps=30[v1];[v0][0:a][v1][1:a]concat=n=2:v=1:a=1[v][a]" \
  -map "[v]" -map "[a]" -c:v libx264 -crf 18 -c:a aac joined.mp4
```

### Fill a vertical frame from landscape footage (crop around a point)

```bash
ffmpeg -i in.mp4 -vf "scale=-2:1920,crop=1080:1920:x='max(0,min(iw-ow,0.30*iw-ow/2))':y=0" out.mp4
```

`0.30` is the horizontal position (0..1) that stays in frame, for example the speaker's face from `faces.py --video`.

### Zoom: static, slow push-in, punch-in

Static zoom by 8 % around the center:

```bash
ffmpeg -i in.mp4 -vf "scale=trunc(iw*1.08/2)*2:-2,crop=1080:1920" out.mp4
```

Slow push-in from 1.0 to 1.06 over 5 seconds (`eval=frame` re-evaluates the size every frame):

```bash
ffmpeg -i in.mp4 -vf "scale=w='trunc(iw*(1+0.06*min(t,5)/5)/2)*2':h=-2:eval=frame:flags=bicubic,crop=1080:1920" out.mp4
```

Punch-in of 10 % at 1.2 s for 0.8 s, with a 70 ms ramp:

```bash
ffmpeg -i in.mp4 -vf "scale=w='trunc(iw*(1+0.1*between(t,1.2,2.0)*min(1,(t-1.2)/0.07))/2)*2':h=-2:eval=frame,crop=1080:1920" out.mp4
```

The input must already be 1080x1920 (or larger) for these crops. For photos, scale to twice the output size first and scale down after the crop: slow zooms on photos otherwise jitter.

### Blur-fit (portrait clip in a landscape frame, or the other way round)

```bash
ffmpeg -i portrait.mp4 -filter_complex \
  "split[fg][bg];[bg]scale=480:270:force_original_aspect_ratio=increase,crop=480:270,boxblur=12:2,eq=brightness=-0.08,scale=1920:1080[b];[fg]scale=1920:1080:force_original_aspect_ratio=decrease[f];[b][f]overlay=(W-w)/2:(H-h)/2" \
  out.mp4
```

### Blur or pixelate a region

```bash
ffmpeg -i in.mp4 -filter_complex \
  "[0:v]split[a][b];[b]crop=400:300:1500:620,boxblur=20:3[c];[a][c]overlay=1500:620:enable='between(t,2,5)'" out.mp4
```

`crop=w:h:x:y` in pixels. The blur radius must be below a quarter of the smaller box side. For pixelation replace `boxblur=20:3` with `scale=25:19,scale=400:300:flags=neighbor`.

### Image card with fade

```bash
ffmpeg -i in.mp4 -loop 1 -t 1.6 -i card.png -filter_complex \
  "[1:v]format=rgba,fade=t=in:d=0.15:alpha=1,fade=t=out:st=1.45:d=0.15:alpha=1,setpts=PTS+3.0/TB[im];[0:v][im]overlay=(W-w)/2:960-h/2:eof_action=pass" \
  out.mp4
```

Shows `card.png` from 3.0 s for 1.6 s, centered at y 960. Prepare the PNG with Pillow (resize, rounded corners, shadow).

### Text with ASS: pop, fade, move, highlight

Write an `.ass` file and burn it in. `PlayResX/Y` = the video size, so positions are pixels:

```text
[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,DejaVu Sans,96,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,8,3,5,20,20,20,1
Style: Box,DejaVu Sans,54,&H00FFFFFF,&H00FFFFFF,&H10111111,&H10111111,-1,0,0,0,100,100,0,0,3,17,0,5,30,30,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 1,0:00:00.50,0:00:01.40,Cap,,0,0,0,,{\an5\pos(540,1344)\fscx82\fscy82\t(0,90,\fscx106\fscy106)\t(90,170,\fscx100\fscy100)}HERE IS {\c&H000AD6FF&}THE{\c&H00FFFFFF&} TRICK
Dialogue: 5,0:00:02.00,0:00:04.00,Box,,0,0,0,,{\an5\pos(540,900)\fad(180,180)}Python + FFmpeg
Dialogue: 5,0:00:04.00,0:00:06.00,Box,,0,0,0,,{\an5\move(460,900,540,900,0,240)\fad(150,200)}slides in
```

```bash
cd work && ffmpeg -i ../in.mp4 -vf "ass=subs.ass:fontsdir=../assets/fonts" -c:a copy ../out.mp4
```

Tags: `\an5\pos(x,y)` centers the text on x,y. `\fad(in,out)` fades in milliseconds. `\t(t1,t2,\fscx106\fscy106)` animates scale (pop). `\move(x1,y1,x2,y2,t1,t2)` slides. `\c&HBBGGRR&` sets the color (blue-green-red order: `&H000AD6FF&` is #FFD60A). `BorderStyle 3` draws an opaque box. Captions with a highlighted active word are one `Dialogue` line per word, each with the same text and a different word colored.

SRT captions burn in with a style instead:

```bash
ffmpeg -i in.mp4 -vf "subtitles=captions.srt:force_style='Fontsize=18,Bold=1,Outline=2,MarginV=60'" out.mp4
```

### Music that ducks under speech (sidechain)

```bash
ffmpeg -i voice.wav -i music.mp3 -filter_complex \
  "[0:a]asplit[v][key];[1:a]volume=0.25[m];[m][key]sidechaincompress=threshold=0.025:ratio=8:attack=20:release=450[ducked];[v][ducked]amix=inputs=2:normalize=0:duration=first[a]" \
  -map "[a]" -c:a pcm_s16le mix.wav
```

The first input of `sidechaincompress` is compressed, the second controls it. Higher `ratio` = deeper dip. For ambience that should not push the music down, mix it after the compressor instead of into the key.

### Loudness to -14 LUFS

Measure, apply the difference as gain with a peak limiter, measure again:

```bash
ffmpeg -hide_banner -nostats -i mix.wav -af ebur128=peak=true -f null -     # summary at the end: I = -22.9 LUFS
ffmpeg -i mix.wav -af "volume=9.1dB,alimiter=limit=0.84:level=false" -ar 48000 mix_norm.wav   # -14 - (-22.9) = 9.1
ffmpeg -hide_banner -nostats -i mix_norm.wav -af ebur128=peak=true -f null -
```

The limiter (-1.5 dBFS) keeps the true peak under -1 dBTP after AAC encoding, but it also lowers the loudness when the mix has sharp peaks. If the second measurement misses the target by more than 0.5 LU, add the difference to the gain once and render again. -14 LUFS for social media and YouTube, -16 for podcasts, -23 for broadcast. `qa.py` checks both values.

### Stems for a hand-off

Render dialog, music and effects as separate WAVs of the same length that all start at 0:00 (pad with `apad` and cut with `atrim=0:<duration>`), plus the picture with dialog only:

```bash
ffmpeg -i joined.mp4 -i dialog.wav -map 0:v -map 1:a -c:v copy -c:a aac -shortest picture_with_dialog.mp4
```

### Final encode for social platforms

```bash
ffmpeg -i in.mp4 -c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p -colorspace bt709 -color_primaries bt709 \
  -color_trc bt709 -color_range tv -c:a aac -b:a 192k -ar 48000 -movflags +faststart final.mp4
```

Then `qa.py final.mp4 --project <project>`.
