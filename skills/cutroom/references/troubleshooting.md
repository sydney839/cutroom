# Troubleshooting

Lessons from real edits. Check here before inventing a workaround.

## Picture

- **Pink, magenta or washed-out skin, or very flat colors.** The clip is HDR (iPhones film HLG/BT.2020 10-bit by default) and was used without conversion. Always edit from `proxy.py` output; it tonemaps HDR to SDR BT.709. `probe.py` lists HDR clips.
- **Clip plays sideways.** Phone metadata rotation was lost (messenger copies, some cameras). Look at the contact sheet; rotate in the proxy (`ffmpeg -vf transpose=1`) and re-run `proxy.py` for that ID with `--force` if needed.
- **Soft or blocky picture.** Messenger copies are 576 to 720 px wide. Ask the user for originals (cable, AirDrop, cloud download "keep originals"). Don't zoom into them.
- **Audio drifts out of sync over a long clip.** Variable frame rate (phones, screen recorders). The proxy converts to constant fps; always cut from proxies.
- **iPhone files missing over USB on Windows.** Photos that live only in iCloud ("Optimize Storage") are invisible over the cable. Download the originals on the phone first.
- **Black flash at a cut.** A segment ended past the end of its source. Check `in`/`out` against the duration in `inventory.csv`; `qa.py` reports black stretches.

## Captions and text

- **Captions or overlays on the face.** Run `faces.py <project>` after the render; it lists every block that covers a face (`COVERS FACE`) and the free bands per segment. Fixes: `caption_pos` on that clip, `pos`/`y` on the overlay, lower `max_words` (smaller caption block), shift the overlay in time, or a `focus`/`zoom` that leaves room above or below the head. A face turned far away may not be detected: the stills decide. Placement rules: [recipes.md](recipes.md).
- **`faces.py` cannot load a face detector.** It downloads a small model (YuNet, 230 KB) once into `~/.cache/cutroom/`. Without network, download `face_detection_yunet_2023mar.onnx` from the OpenCV Zoo on another machine and set `CUTROOM_FACE_MODEL` to its path. OpenCV 4 builds fall back to the old Haar detector on their own.
- **Wrong words in captions.** Add `fixes` (`{"cloud": "Claude"}`) or re-transcribe with `--model medium`. Product names: `--terms`.
- **Whisper writes text that was never said** (often the prompt text, or "thanks for watching") over music, crowd noise or silence. Transcribe without an initial prompt and with VAD (the defaults), and don't caption clips that are only music or ambience (`vol` 0 or `captions: false`).
- **Font falls back to Arial.** Put the `.ttf`/`.otf` into `<project>/assets/fonts/` and use its family name in `style.font`.
- **Render warning "covers the captions" or "overlap".** Two blocks share the same place at the same time. Move one (`pos`, `caption_pos`) or shift it in time; never stack a hook, a chip and an image card at the same moment.

## Audio

- **Clicks or pops at cuts.** Every segment gets a 12 ms fade; if you still hear clicks, a cut lands inside a word: use word anchors instead of seconds or nudge with `trim_in`/`trim_out`.
- **Music too loud under speech.** Lower `vol` (0.15 to 0.2) or raise `duck.ratio`. Clips meant as ambience should have `duck: false`, otherwise they pump the music.
- **Final too quiet or too loud.** The renderer normalizes to `loudness` (default -14 LUFS) with a peak limiter that keeps the true peak under -1 dBTP; `qa.py` reports the measured values. If the result is still more than 1 LU too quiet, the mix has sharp peaks (hard sound effects, claps, test tones) that the limiter has to catch: lower those `sfx` volumes.
- **Words cut off at the end of a take.** The `to` word was matched in an earlier occurrence; use a longer phrase or `to_n`.

## Transcription and GPU

- **faster-whisper fails with "unexpected keyword argument" while opening audio.** A faster-whisper / PyAV version mismatch. `transcribe.py` avoids it by passing the samples from `work/audio/<ID>.wav` directly; if you call faster-whisper yourself, do the same.
- **faster-whisper crashes silently on Windows with CUDA.** The cuBLAS/cuDNN DLLs are missing. `transcribe.py` falls back to CPU; for GPU speed install `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` via pip.
- **h264_nvenc not available.** The scripts fall back to libx264 automatically; slower but identical workflow.

## Process

- **Word anchor not found.** The error shows the start of the transcript. Copy the wording from `work/takes.md`, including how Whisper spelled names.
- **A render takes long.** Normal: about 1x to 3x real time on a laptop. Run final renders in the background, one at a time. Segments are cached; after small edits only changed segments re-render.
- **Many files, no order.** Never assign a range of IDs to a scene by assumption. Look at each file; use clothing per day, daylight and places to date undated files.
- **Stuck in a fix loop.** After two or three rounds, stop and ask the user with concrete options.
