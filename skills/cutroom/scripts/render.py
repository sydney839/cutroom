"""Render edit.json into a finished video.

    python render.py <project> [--draft] [--out name.mp4] [--package] [--no-cache]

Pipeline: plan the timeline from edit.json (word-anchored cuts, pause removal, beat-length clips)
-> render every segment to a cached, uniform intermediate (fit, zoom, punch-ins, grade, fades)
-> concat -> captions and text overlays as one ASS file (face-aware placement)
-> image overlays and blur boxes -> music with ducking under speech, sound effects
-> loudness to target -> final encode -> captions.srt, edl.csv, review stills.

See references/edit-json.md for every key.
"""
import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (PHOTO_EXT, PRESETS, CutroomError, die, ffprobe, fmt_tc, need,  # noqa: E402
                    project_dir, read_json, resolve_source, run, write_json)

PAD_IN, PAD_OUT = 0.08, 0.15          # breathing room around word-anchored cuts
EDGE_FADE = 0.012                     # tiny audio fade on every cut, prevents clicks
TEXT_TOKEN = re.compile(r"[\w']+", re.UNICODE)


# ---------------------------------------------------------------- small helpers

def norm(s: str) -> str:
    return " ".join(TEXT_TOKEN.findall(s.lower()))


def even(x: float) -> int:
    return max(2, int(round(x / 2.0)) * 2)


def ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    if len(h) != 6:
        raise CutroomError(f"color '{hex_rgb}' must be #RRGGBB")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(s: str) -> str:
    return s.replace("\\", "\u29f5").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def srt_time(t: float) -> str:
    ms = int(round(max(0.0, t) * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def concat_list(paths, dest: Path):
    lines = []
    for p in paths:
        q = str(Path(p).resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{q}'")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def find_font(name: str, project: Path):
    """True if a font file whose name contains `name` exists in a usual font folder."""
    key = name.lower().replace(" ", "")
    dirs = [project / "assets" / "fonts", Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
            Path.home() / "AppData/Local/Microsoft/Windows/Fonts", Path("/Library/Fonts"),
            Path.home() / "Library/Fonts", Path("/usr/share/fonts"), Path.home() / ".fonts",
            Path.home() / ".local/share/fonts"]
    for d in dirs:
        if d.exists():
            for f in d.rglob("*"):
                if f.suffix.lower() in (".ttf", ".otf") and key in f.name.lower().replace(" ", "").replace("-", ""):
                    return True
    return False


_ENCODER = None


def video_encoder(final=False, draft=False):
    """h264_nvenc when it works on this machine, else libx264."""
    global _ENCODER
    if _ENCODER is None:
        p = run(["ffmpeg", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "color=s=256x256:d=0.1",
                 "-c:v", "h264_nvenc", "-f", "null", "-"], check=False)
        _ENCODER = "h264_nvenc" if p.returncode == 0 else "libx264"
    if _ENCODER == "h264_nvenc":
        q = "30" if draft else ("19" if final else "17")
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", q, "-b:v", "0"]
    crf = "28" if draft else ("18" if final else "16")
    return ["-c:v", "libx264", "-preset", "ultrafast" if draft else ("medium" if final else "veryfast"), "-crf", crf]


COLOR_TAGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]


# ---------------------------------------------------------------- media info

_INFO = {}


def media_info(path: Path) -> dict:
    if path in _INFO:
        return _INFO[path]
    if path.suffix.lower() in PHOTO_EXT:
        try:
            from PIL import Image, ImageOps
            with Image.open(path) as im:
                w, h = ImageOps.exif_transpose(im).size
        except Exception:
            d = ffprobe(path)
            v = next(s for s in d["streams"] if s["codec_type"] == "video")
            w, h = int(v["width"]), int(v["height"])
        info = {"kind": "photo", "w": w, "h": h, "dur": None, "audio": False}
    else:
        d = ffprobe(path)
        v = next((s for s in d.get("streams", []) if s["codec_type"] == "video"), None)
        if v is None:
            raise CutroomError(f"{path} has no video stream")
        w, h = int(v["width"]), int(v["height"])
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                rot = int(float(sd["rotation"]))
        rot = int(v.get("tags", {}).get("rotate", rot))
        if abs(rot) % 180 == 90:
            w, h = h, w
        info = {"kind": "video", "w": w, "h": h, "dur": float(d["format"].get("duration", 0)),
                "audio": any(s["codec_type"] == "audio" for s in d["streams"])}
    _INFO[path] = info
    return info


def load_words(project: Path, src_ref: str, clip: dict):
    tr = clip.get("transcript")
    f = (project / tr) if tr else project / "work" / "transcripts" / f"{src_ref}.json"
    data = read_json(f)
    return (data or {}).get("words", []), f


def find_phrase(words, phrase: str, n: int = 1, after: float = -1.0, where=""):
    """(start, end, i0, i1) of the n-th occurrence of `phrase` (one or more words) starting after `after`."""
    target = norm(phrase).split()
    if not target:
        raise CutroomError(f"empty word anchor {where}")
    toks = [norm(w["text"]) for w in words]
    hits = 0
    for i in range(len(words)):
        if words[i]["start"] < after - 1e-6:
            continue
        ok = True
        for k, t in enumerate(target):
            if i + k >= len(toks) or t not in toks[i + k]:
                ok = False
                break
        if ok:
            hits += 1
            if hits == n:
                return words[i]["start"], words[i + len(target) - 1]["end"], i, i + len(target) - 1
    sample = " ".join(w["text"] for w in words[:40])
    raise CutroomError(f"word anchor '{phrase}' (occurrence {n}) not found {where}. "
                       f"Check work/takes.md for the exact wording. Transcript starts: \"{sample}...\"")


# ---------------------------------------------------------------- timeline planning

class Plan:
    def __init__(self, project: Path, edit: dict, draft: bool):
        self.project, self.edit, self.draft = project, edit, draft
        fmt = edit.get("format", "reel")
        if isinstance(fmt, str):
            if fmt not in PRESETS:
                raise CutroomError(f"format '{fmt}' unknown. Use one of {sorted(PRESETS)} or "
                                   f'{{"w": 1080, "h": 1920, "fps": 30}}.')
            self.W, self.H = PRESETS[fmt]
            self.fps = 30
        else:
            self.W, self.H, self.fps = int(fmt["w"]), int(fmt["h"]), int(fmt.get("fps", 30))
        if isinstance(edit.get("format"), str) and "fps" in edit:
            self.fps = int(edit["fps"])
        self.vertical = self.H > self.W
        self.segments = []      # rendered pieces in order
        self.clip_segments = {}  # clip index -> [segment]
        self.warnings = []
        self.beats = None
        self._build()

    def frames(self, dur):
        return max(1, int(round(dur * self.fps)))

    def timeline_beats(self):
        if self.beats is None:
            spec = self.edit.get("beats")
            self.beats = []
            if spec:
                f = self.project / (spec if isinstance(spec, str) else spec["file"])
                data = read_json(f)
                if data is None:
                    raise CutroomError(f"beats file {f} not found. Run beats.py on the music track first.")
                at = 0.0 if isinstance(spec, str) else float(spec.get("at", 0.0))
                start = 0.0 if isinstance(spec, str) else float(spec.get("start", 0.0))
                self.beats = [b - start + at for b in data.get("beats", []) if b - start + at >= -1e-6]
        return self.beats

    def beat_duration(self, t_now, n, where):
        beats = self.timeline_beats()
        if not beats:
            raise CutroomError(f'{where} uses "beats" but edit.json has no "beats" file. '
                               f'Add "beats": "work/beats/<track>.json" (from beats.py).')
        idx = next((i for i, b in enumerate(beats) if b > t_now + 0.5 / self.fps), None)
        if idx is None or idx + n - 1 >= len(beats):
            raise CutroomError(f"{where}: the music has no beats left at {fmt_tc(t_now)}")
        return beats[idx + n - 1] - t_now

    def _build(self):
        e, project = self.edit, self.project
        tighten = e.get("tighten")
        clips = e.get("clips") or []
        if not clips:
            raise CutroomError('edit.json has no "clips". Add at least one clip, see references/edit-json.md.')
        t = 0.0
        for ci, c in enumerate(clips):
            where = f"in clips[{ci}]"
            if "card" in c or c.get("type") == "card":
                pieces = [(None, None)]
                kind, path, info = "card", None, None
                dur = float(c.get("dur", 2.0))
                if "beats" in c:
                    dur = self.beat_duration(t, int(c["beats"]), where)
                spans = [dur]
            else:
                if "src" not in c:
                    raise CutroomError(f'{where}: every clip needs "src" (an ID like C003 or a path) or "card".')
                path = resolve_source(project, str(c["src"]))
                info = media_info(path)
                kind = info["kind"]
                speed = float(c.get("speed", 1.0))
                if kind == "photo":
                    dur = float(c.get("dur", 2.0))
                    if "beats" in c:
                        dur = self.beat_duration(t, int(c["beats"]), where)
                    pieces, spans = [(0.0, dur)], [dur]
                else:
                    a, b = self._clip_range(c, info, where)
                    if "beats" in c:
                        b = a + self.beat_duration(t, int(c["beats"]), where) * speed
                        b = min(b, info["dur"])
                    pieces = [(a, b)]
                    tg = c.get("tighten", tighten)
                    if tg and ("from" in c or "words" in c or c.get("tighten")):
                        pieces = self._tighten(c, a, b, float(tg), where)
                    spans = [(pb - pa) / speed for pa, pb in pieces]
            segs = []
            for pi, ((pa, pb), span) in enumerate(zip(pieces, spans)):
                nf = self.frames(span)
                seg = {"clip": ci, "piece": pi, "kind": kind, "path": path, "info": info, "a": pa, "b": pb,
                       "t0": t, "dur": nf / self.fps, "frames": nf, "spec": c,
                       "first": pi == 0, "last": pi == len(pieces) - 1}
                if nf / self.fps < 0.3 and kind == "video":
                    self.warnings.append(f"clips[{ci}] piece {pi} is only {nf / self.fps:.2f} s long")
                segs.append(seg)
                t += nf / self.fps
            self.segments += segs
            self.clip_segments[ci] = segs
        self.total = t
        self.total_frames = sum(s["frames"] for s in self.segments)
        td = e.get("target_duration")
        if td and not (td[0] <= self.total <= td[1]):
            self.warnings.append(f"duration {self.total:.1f} s is outside target_duration {td}")

    def _clip_range(self, c, info, where):
        dur = info["dur"]
        if "from" in c or "to" in c:
            words, f = load_words(self.project, str(c["src"]), c)
            if not words:
                raise CutroomError(f'{where}: "from"/"to" need a transcript at {f}. Run transcribe.py first.')
            a = 0.0
            if "from" in c:
                a, _, _, _ = find_phrase(words, c["from"], int(c.get("from_n", 1)), where=where)
                a = max(0.0, a - PAD_IN)
            if "to" in c:
                _, b, _, _ = find_phrase(words, c["to"], int(c.get("to_n", 1)), after=a, where=where)
                b = min(dur, b + PAD_OUT)
            else:
                b = dur
        else:
            a = float(c.get("in", 0.0))
            b = float(c.get("out", dur if dur else a + float(c.get("dur", 3.0))))
            if "dur" in c and "out" not in c:
                b = a + float(c["dur"]) * float(c.get("speed", 1.0))
        a += float(c.get("trim_in", 0.0))
        b -= float(c.get("trim_out", 0.0))
        b = min(b, dur) if dur else b
        if b - a < 1.0 / self.fps:
            raise CutroomError(f"{where}: empty range {a:.2f}-{b:.2f} s (source is {dur:.2f} s long)")
        return a, b

    def _tighten(self, c, a, b, gap, where):
        words, _ = load_words(self.project, str(c["src"]), c)
        ws = [w for w in words if w["start"] >= a - 0.02 and w["end"] <= b + 0.02]
        if len(ws) < 2:
            return [(a, b)]
        pieces, start = [], a
        for w0, w1 in zip(ws, ws[1:]):
            if w1["start"] - w0["end"] > gap:
                pieces.append((start, min(b, w0["end"] + 0.1)))
                start = max(a, w1["start"] - 0.07)
        pieces.append((start, b))
        return [(pa, pb) for pa, pb in pieces if pb - pa > 0.12]

    # -------- anchors: "at": 3.2 | {"clip": 2, "offset": 0.3} | {"clip": 2, "word": "Claude", "n": 1}
    def anchor(self, spec, where):
        if spec is None:
            return 0.0
        if isinstance(spec, (int, float)):
            return float(spec)
        if spec == "end":
            return self.total
        if not isinstance(spec, dict) or "clip" not in spec:
            raise CutroomError(f'{where}: anchor must be seconds, "end", or {{"clip": i, ...}}, got {spec!r}')
        ci = int(spec["clip"])
        if ci < 0:
            ci += len(self.clip_segments)
        if ci not in self.clip_segments:
            raise CutroomError(f"{where}: clip {spec['clip']} does not exist (there are {len(self.clip_segments)})")
        segs = self.clip_segments[ci]
        off = float(spec.get("offset", 0.0))
        if "word" not in spec:
            if spec.get("from_end"):
                return segs[-1]["t0"] + segs[-1]["dur"] + off
            return segs[0]["t0"] + off
        c = segs[0]["spec"]
        words, _ = load_words(self.project, str(c.get("src")), c)
        ws, _, _, _ = find_phrase(words, spec["word"], int(spec.get("n", 1)), after=segs[0]["a"] - 0.05,
                                  where=where)
        speed = float(c.get("speed", 1.0))
        for s in segs:
            if s["a"] - 0.05 <= ws <= s["b"]:
                return s["t0"] + max(0.0, ws - s["a"]) / speed + off
        later = [s for s in segs if s["a"] > ws]
        if later:
            return later[0]["t0"] + off
        raise CutroomError(f"{where}: word '{spec['word']}' lies outside clip {ci}'s cut range")

    def span(self, o, where, default_dur=2.0):
        t0 = self.anchor(o.get("at", 0.0), where)
        if o.get("to_end") or o.get("until") == "end":
            t1 = self.total
        elif "until" in o:
            t1 = self.anchor(o["until"], where)
        else:
            t1 = t0 + float(o.get("dur", default_dur))
        t0, t1 = max(0.0, t0), min(self.total, t1)
        if t1 - t0 < 0.05:
            raise CutroomError(f"{where}: ends before it starts ({t0:.2f}-{t1:.2f} s)")
        return t0, t1


# ---------------------------------------------------------------- segment rendering

def zoom_expr(seg, plan):
    c = seg["spec"]
    z0 = float(c.get("zoom", 1.0))
    z1 = c.get("zoom_to")
    kb = c.get("kenburns", True) if seg["kind"] == "photo" else False
    if kb and z1 is None:
        if kb == "out":
            z0, z1 = z0 + 0.08, z0
        else:
            z1 = z0 + 0.08
    expr = f"{z0}" if z1 is None else f"({z0}+({float(z1)}-{z0})*t/{seg['dur']:.4f})"
    punches = []
    clip_t0 = plan.clip_segments[seg["clip"]][0]["t0"]
    for p in c.get("punch", []) or []:
        if isinstance(p.get("at"), (int, float)):
            t_abs = clip_t0 + float(p["at"])          # seconds into this clip
        else:
            t_abs = plan.anchor({**p, "clip": seg["clip"]}, f"punch in clips[{seg['clip']}]")
        ts = t_abs - seg["t0"]                        # segment-local time
        hold, amt = float(p.get("hold", 0.8)), float(p.get("amt", 0.1))
        if -hold < ts < seg["dur"]:
            punches.append(f"{amt}*between(t,{ts:.3f},{ts + hold:.3f})*min(1,(t-{ts:.3f})/0.07)")
    if punches:
        expr = f"({expr}+{'+'.join(punches)})"
    animated = z1 is not None or bool(punches)
    return expr, animated


def video_filter(seg, plan, grade):
    W, H, fps = plan.W, plan.H, plan.fps
    c, info = seg["spec"], seg["info"]
    fit = c.get("fit", "cover")
    fx, fy = (c.get("focus") or [0.5, 0.5 if seg["kind"] == "photo" else 0.45])
    chain = []
    if seg["kind"] == "video" and float(c.get("speed", 1.0)) != 1.0:
        chain.append(f"setpts=PTS/{float(c['speed'])}")
    zexpr, animated = zoom_expr(seg, plan)
    if seg["kind"] == "card":
        chain = []
    elif fit == "cover":
        sup = 2 if (animated and seg["kind"] == "photo") else 1   # supersample slow photo zooms: no stair-step jitter
        cw, ch = W * sup, H * sup
        s = max(cw / info["w"], ch / info["h"])
        bw, bh = even(info["w"] * s), even(info["h"] * s)
        if animated:
            chain.append(f"scale=w='trunc({bw}*{zexpr}/2)*2':h='trunc({bh}*{zexpr}/2)*2':eval=frame:flags=bicubic")
        else:
            z = float(zexpr)
            chain.append(f"scale={even(bw * z)}:{even(bh * z)}:flags=lanczos")
        chain.append(f"crop={cw}:{ch}:x='max(0,min(iw-ow,{fx}*iw-ow/2))':y='max(0,min(ih-oh,{fy}*ih-oh/2))'")
        if sup > 1:
            chain.append(f"scale={W}:{H}:flags=area")
    elif fit == "contain":
        chain.append(f"scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
                     f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={c.get('bg', 'black')}")
    elif fit == "blur":
        chain = (["setpts=PTS/%s" % float(c["speed"])] if seg["kind"] == "video" and float(c.get("speed", 1.0)) != 1.0 else [])
        chain.append(f"split[fg][bg];[bg]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,"
                     f"crop={W // 4}:{H // 4},boxblur=12:2,eq=brightness=-0.08,scale={W}:{H}[bgb];"
                     f"[fg]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos[fgs];"
                     f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2")
    else:
        raise CutroomError(f'clips[{seg["clip"]}]: fit must be "cover", "contain" or "blur", got "{fit}"')
    g = {**(grade or {}), **(c.get("grade") or {})}
    eq = []
    for k in ("contrast", "saturation", "brightness", "gamma"):
        if k in g:
            eq.append(f"{k}={g[k]}")
    if eq:
        chain.append("eq=" + ":".join(eq))
    if g.get("lut"):
        lut = (plan.project / g["lut"]).resolve().as_posix().replace(":", "\\:")
        chain.append(f"lut3d=file='{lut}'")
    if g.get("vignette"):
        chain.append("vignette=PI/5")
    fin, fout = float(c.get("fade_in", 0)) if seg["first"] else 0, float(c.get("fade_out", 0)) if seg["last"] else 0
    if fin:
        chain.append(f"fade=t=in:st=0:d={fin}")
    if fout:
        chain.append(f"fade=t=out:st={max(0, seg['dur'] - fout):.3f}:d={fout}")
    chain += [f"fps={fps}", "format=yuv420p", "setsar=1"]
    return ",".join(chain)


def audio_filter(seg):
    c = seg["spec"]
    chain = []
    sp = float(c.get("speed", 1.0))
    while sp > 2.0:
        chain.append("atempo=2.0")
        sp /= 2.0
    while sp < 0.5:
        chain.append("atempo=0.5")
        sp /= 0.5
    if sp != 1.0:
        chain.append(f"atempo={sp}")
    chain.append(f"volume={float(c.get('vol', 1.0))}")
    d = seg["dur"]
    fin = max(EDGE_FADE, float(c.get("fade_in", 0)) if seg["first"] else 0)
    fout = max(EDGE_FADE, float(c.get("fade_out", 0)) if seg["last"] else 0)
    chain += ["aresample=48000", "aformat=sample_fmts=fltp:channel_layouts=stereo",
              f"apad,atrim=0:{d:.4f}", f"afade=t=in:st=0:d={fin}", f"afade=t=out:st={max(0, d - fout):.4f}:d={fout}"]
    return ",".join(chain)


def render_segment(seg, plan, grade, cache_dir: Path, use_cache=True):
    key_src = {k: v for k, v in seg["spec"].items() if not k.startswith("_")}
    stat = seg["path"].stat() if seg["path"] else None
    key = json.dumps([key_src, seg["a"], seg["b"], seg["frames"], plan.W, plan.H, plan.fps, grade,
                      str(seg["path"]), stat.st_mtime if stat else 0, stat.st_size if stat else 0,
                      seg["first"], seg["last"], plan.draft], sort_keys=True, default=str)
    h = hashlib.sha1(key.encode()).hexdigest()[:12]
    vout, aout = cache_dir / f"seg_{h}.mp4", cache_dir / f"seg_{h}.wav"
    seg["vfile"], seg["afile"] = vout, aout
    if use_cache and vout.exists() and aout.exists():
        return False
    c, d = seg["spec"], seg["dur"]
    if seg["kind"] == "card":
        inp = ["-f", "lavfi", "-i", f"color=c={c.get('bg', '#111111')}:s={plan.W}x{plan.H}:r={plan.fps}:d={d + 0.2:.3f}"]
    elif seg["kind"] == "photo":
        inp = ["-loop", "1", "-framerate", str(plan.fps), "-t", f"{d + 0.2:.3f}", "-i", str(seg["path"])]
    else:
        src_len = (seg["b"] - seg["a"]) + 0.2
        inp = ["-ss", f"{seg['a']:.4f}", "-t", f"{src_len:.4f}", "-i", str(seg["path"])]
    vf = video_filter(seg, plan, grade)
    fc = f"[0:v]{vf}[v]"
    run(["ffmpeg", "-hide_banner", "-y", "-v", "error", *inp, "-filter_complex", fc, "-map", "[v]", "-an",
         "-frames:v", str(seg["frames"]), *video_encoder(draft=plan.draft), "-pix_fmt", "yuv420p",
         "-r", str(plan.fps), *COLOR_TAGS, str(vout)])
    if seg["kind"] == "video" and seg["info"]["audio"] and float(c.get("vol", 1.0)) > 0:
        run(["ffmpeg", "-hide_banner", "-y", "-v", "error", "-ss", f"{seg['a']:.4f}",
             "-t", f"{(seg['b'] - seg['a']) + 0.2:.4f}", "-i", str(seg["path"]), "-vn",
             "-af", audio_filter(seg), "-c:a", "pcm_s16le", str(aout)])
    else:
        silence(d, aout)
    return True


def silence(d, out: Path):
    run(["ffmpeg", "-hide_banner", "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-t", f"{d:.4f}", "-c:a", "pcm_s16le", str(out)])


# ---------------------------------------------------------------- faces and caption placement

def detect_faces(seg, samples=5):
    """Union face box (x0, y0, x1, y1) in output pixels from the rendered segment, or None."""
    try:
        import cv2
    except ImportError:
        return "nocv"
    casc = [cv2.CascadeClassifier(cv2.data.haarcascades + n)
            for n in ("haarcascade_frontalface_alt2.xml", "haarcascade_frontalface_default.xml")]
    cap = cv2.VideoCapture(str(seg["vfile"]))
    n = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
    boxes, tried = [], 0
    for i in range(samples):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int((i + 0.5) / samples * n))
        ok, fr = cap.read()
        if not ok:
            continue
        tried += 1
        h, w = fr.shape[:2]
        f = 540 / max(w, h) * (2 if h > w else 1)
        g = cv2.equalizeHist(cv2.cvtColor(cv2.resize(fr, None, fx=f, fy=f), cv2.COLOR_BGR2GRAY))
        for cc in casc:
            r = cc.detectMultiScale(g, 1.1, 5, minSize=(40, 40))
            if len(r):
                x, y, bw, bh = max(r, key=lambda b: b[2] * b[3])
                boxes.append((x / f, y / f, (x + bw) / f, (y + bh) / f))
                break
    cap.release()
    if not boxes or len(boxes) < max(2, 0.4 * tried):
        return None
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    hh = y1 - y0
    return x0, y0 - 0.25 * hh, x1, y1 + 0.2 * hh     # hair above, chin below


def safe_zone(plan):
    """(top, bottom) y range free of platform UI."""
    if plan.vertical:
        return int(plan.H * 0.14), int(plan.H * 0.78)
    return int(plan.H * 0.06), int(plan.H * 0.93)


def text_sizes(plan):
    """Font sizes and box paddings in pixels, shared by the ASS styles and the layout."""
    H, v = plan.H, plan.vertical
    return {"hook": int(H * (0.036 if v else 0.065)), "hook_pad": int(H * (0.011 if v else 0.014)),
            "box": int(H * (0.028 if v else 0.045)), "box_pad": int(H * (0.009 if v else 0.012)),
            "title": int(H * (0.075 if v else 0.11)), "sub": int(H * (0.03 if v else 0.045)),
            "handle": int(H * 0.022)}


class Layout:
    """Free-space bookkeeping on the frame over time: faces per segment plus placed text and image blocks.
    Overlays are placed first (they are deliberate design choices), captions then avoid faces and overlays."""

    def __init__(self, plan):
        self.plan = plan
        self.top, self.bottom = safe_zone(plan)
        self.gap = int(plan.H * 0.012)
        self.blocks = []

    def faces(self, t0, t1):
        return [s["face"] for s in self.plan.segments if isinstance(s.get("face"), tuple)
                and s["t0"] < t1 and s["t0"] + s["dur"] > t0]

    def free(self, t0, t1, y0, y1):
        if y0 < self.top or y1 > self.bottom:
            return False
        g = self.gap
        if any(y0 < f[3] + g and y1 > f[1] - g for f in self.faces(t0, t1)):
            return False
        return not any(a < t1 and b > t0 and y0 < d + g and y1 > c - g for a, b, c, d in self.blocks)

    def scan(self, h, start, direction):
        ys, y = [], start
        while self.top + h / 2 <= y <= self.bottom - h / 2:
            ys.append(y)
            y += 8 * direction
        return ys

    def find(self, t0, t1, h, candidates):
        for y in candidates:
            if self.free(t0, t1, y - h / 2, y + h / 2):
                return int(y)
        return None

    def add(self, t0, t1, y, h):
        self.blocks.append((t0, t1, y - h / 2, y + h / 2))


def place_caption(layout, t0, t1, face, block_h, pos):
    """Center y of a caption block, a size factor and a zone label."""
    plan = layout.plan
    default = int(plan.H * (0.72 if plan.vertical else 0.86))
    if isinstance(pos, (int, float)) and not isinstance(pos, bool):
        return int(pos if pos > 1 else pos * plan.H), 1.0, "fixed"
    if pos == "top":
        return int(plan.H * (0.19 if plan.vertical else 0.1)), 1.0, "top"
    if pos == "bottom":
        return layout.bottom - block_h // 2, 1.0, "bottom"
    if pos == "center":
        return plan.H // 2, 1.0, "center"
    for scale in (1.0, 0.8, 0.65):
        h = block_h * scale
        if isinstance(face, tuple):
            g = layout.gap
            below = layout.scan(h, face[3] + g + h / 2, 1)
            above = layout.scan(h, face[1] - g - h / 2, -1)
            cands = [("below face", y) for y in below] + [("above face", y) for y in above]
        else:
            ys = layout.scan(h, default, 1)
            ys2 = layout.scan(h, default - 8, -1)
            cands = [("default", y) for pair in zip(ys2, ys) for y in pair[::-1]] + \
                    [("default", y) for y in (ys[len(ys2):] + ys2[len(ys):])]
        for zone, y in cands:
            if layout.free(t0, t1, y - h / 2, y + h / 2):
                return int(y), scale, zone + ("" if scale == 1.0 else f" {int(scale * 100)}%")
    y = int(layout.bottom - block_h * 0.65 / 2)
    return y, 0.65, "FALLBACK (overlaps face or overlay)"


def layout_overlays(plan, overlays, layout, render_dir, log):
    """Decide y (and image sizes) for every overlay without an explicit position; store as o['_y'], o['_h']."""
    W, H = plan.W, plan.H
    sz = text_sizes(plan)
    order = {"title": 0, "lower_third": 0, "hook": 1, "image": 2, "chip": 3, "text": 3, "cta": 4}
    for i, o in sorted(enumerate(overlays), key=lambda io: order.get(io[1].get("type"), 9)):
        typ = o.get("type")
        if typ in ("blur", "pixelate"):
            continue
        where = f"overlays[{i}] ({typ})"
        default_dur = {"hook": 2.6, "title": 2.0, "cta": 3.0, "image": 1.8}.get(typ, 1.6)
        t0, t1 = plan.span(o, where, default_dur)
        o["_t"] = (t0, t1)
        n_lines = len(str(o.get("text", "")).split("\n"))
        if typ == "hook":
            h = n_lines * sz["hook"] * 1.3 + 2 * sz["hook_pad"]
        elif typ == "cta":
            h = sz["box"] * 1.3 + (sz["handle"] * 1.3 if o.get("handle") else 0) + 2 * sz["box_pad"]
        elif typ == "image":
            f = plan.project / o.get("file", "")
            if not f.exists():
                raise CutroomError(f"{where}: image {f} not found. Put it in assets/ and use \"assets/<name>\".")
            if o.get("layout", "card") == "full":
                continue
            pre, size = prep_image(f, render_dir / f"img_{i}.png", int(W * float(o.get("width", 0.8))),
                                   o.get("rounded", True))
            o["_img"] = pre
            h = size[1] if size else H * 0.25
        elif typ == "title":
            h = sz["title"] * 1.3 + (sz["sub"] * 2.2 if o.get("sub") else 0)
        elif typ == "lower_third":
            h = sz["box"] * 1.3 + 2 * sz["box_pad"] + (sz["sub"] * 1.6 if o.get("sub") else 0)
        elif typ == "chip" or o.get("box"):
            h = n_lines * sz["box"] * 1.3 + 2 * sz["box_pad"]
        else:
            h = n_lines * sz["sub"] * 1.3
        o["_h"] = h
        pos = o.get("pos")
        y = None
        if isinstance(pos, list):
            y = pos[1] * H if pos[1] <= 1 else pos[1]
        elif o.get("y") is not None:
            y = o["y"] * H if o["y"] <= 1 else o["y"]
        elif typ == "title":
            y = H / 2
        elif typ == "lower_third":
            y = H * (0.70 if plan.vertical else 0.80)
        elif typ == "image" and pos in ("center", "bottom"):
            y = layout.find(t0, t1, h, layout.scan(h, H / 2 if pos == "center" else layout.bottom - h / 2, -1))
        else:
            start, direction = (layout.bottom - h / 2, -1) if typ == "cta" else (layout.top + h / 2, 1)
            y = layout.find(t0, t1, h, layout.scan(h, start, direction))
            if y is None:
                y = layout.top + h / 2 if typ != "cta" else layout.bottom - h / 2
                log.append(f"WARNING: {where} at {fmt_tc(t0)}: no free space beside the face; it covers the face "
                           f"or other text. Shorten it, move it in time or set \"pos\".")
        o["_y"] = int(y)
        layout.add(t0, t1, o["_y"], h)


# ---------------------------------------------------------------- captions and text overlays (ASS)

STYLE_PRESETS = {
    "bold": {"size": 0.050, "upper": True, "max_words": 3, "outline": 0.0045, "pop": True, "highlight": True},
    "clean": {"size": 0.036, "upper": False, "max_words": 7, "outline": 0.0, "pop": False, "highlight": False,
              "box": True},
    "karaoke": {"size": 0.044, "upper": False, "max_words": 5, "outline": 0.004, "pop": False, "highlight": True},
}


def caption_words(plan, cap):
    fixes = {norm(k): v for k, v in (cap.get("fixes") or {}).items()}
    drop = {norm(d) for d in cap.get("drop", ["ähm", "äh", "uh", "um", "hmm"])}
    out = []
    for s in plan.segments:
        c = s["spec"]
        if s["kind"] != "video" or c.get("captions") is False or float(c.get("vol", 1.0)) == 0:
            continue
        words, _ = load_words(plan.project, str(c.get("src")), c)
        sp = float(c.get("speed", 1.0))
        for w in words:
            if w["start"] >= s["a"] - 0.02 and w["end"] <= s["b"] + 0.05:
                txt = w["text"].strip()
                k = norm(txt)
                if not k or k in drop:
                    continue
                if fixes:
                    txt = TEXT_TOKEN.sub(lambda m: fixes.get(m.group(0).lower(), m.group(0)), txt)
                t0 = s["t0"] + max(0.0, w["start"] - s["a"]) / sp
                t1 = min(s["t0"] + s["dur"], s["t0"] + (w["end"] - s["a"]) / sp)
                out.append({"text": txt, "t0": t0, "t1": max(t1, t0 + 0.08), "seg": s})
    return out


def chunk_words(words, max_words):
    chunks, cur = [], []
    for i, w in enumerate(words):
        if cur and (len(cur) >= max_words or w["seg"] is not cur[-1]["seg"] or w["t0"] - cur[-1]["t1"] > 0.45
                    or re.search(r"[.!?:;,]$", cur[-1]["text"])):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    for i, ch in enumerate(chunks):
        nxt = chunks[i + 1][0]["t0"] if i + 1 < len(chunks) else ch[-1]["t1"] + 0.6
        end = min(nxt, ch[-1]["t1"] + 0.5, ch[-1]["seg"]["t0"] + ch[-1]["seg"]["dur"])
        ch_end = max(end, ch[0]["t0"] + 0.25)
        for w in ch:
            w["chunk_end"] = ch_end
    return chunks


def wrap_lines(words, max_chars):
    text = " ".join(words)
    if len(text) <= max_chars or len(words) < 2:
        return [words]
    best, best_d = None, 1e9
    for k in range(1, len(words)):
        a, b = " ".join(words[:k]), " ".join(words[k:])
        d = abs(len(a) - len(b)) + (1000 if max(len(a), len(b)) > max_chars * 1.15 else 0)
        if d < best_d:
            best, best_d = [words[:k], words[k:]], d
    return best


def build_ass(plan, overlays_text, fontname, accent, log):
    W, H = plan.W, plan.H
    e = plan.edit
    cap = {"style": "bold" if plan.vertical else "clean", **(e.get("captions") or {})}
    enabled = cap.get("enabled", True) and cap.get("style") != "none"
    st = {**STYLE_PRESETS.get(cap["style"], STYLE_PRESETS["bold"]), **cap}
    size = int(st["size"] * H if st["size"] < 1 else st["size"])
    outline = max(0, int(st.get("outline", 0.004) * H if st.get("outline", 0) < 1 else st["outline"]))
    acc = ass_color(accent)
    white, black = ass_color("#FFFFFF"), ass_color("#000000")
    boxc = ass_color("#000000", 0x60)
    head = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2",
        "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cap,{fontname},{size},{white},{white},{black},{ass_color('#000000', 0x80)},-1,0,0,0,100,100,0,0,1,"
        f"{outline},{max(1, outline // 3)},5,20,20,20,1",
        f"Style: CapBox,{fontname},{size},{white},{white},{boxc},{boxc},-1,0,0,0,100,100,0,0,3,"
        f"{int(size * 0.28)},0,5,20,20,20,1",
        f"Style: Hook,{fontname},{int(H * 0.042 if plan.vertical else H * 0.07)},{white},{white},"
        f"{ass_color('#000000', 0x20)},{ass_color('#000000', 0x20)},-1,0,0,0,100,100,0,0,3,{int(H * 0.012)},0,5,30,30,30,1",
        f"Style: Box,{fontname},{int(H * 0.028 if plan.vertical else H * 0.045)},{white},{white},"
        f"{ass_color('#111111', 0x10)},{ass_color('#111111', 0x10)},-1,0,0,0,100,100,0,0,3,{int(H * 0.009)},0,5,30,30,30,1",
        f"Style: Title,{fontname},{int(H * 0.075 if plan.vertical else H * 0.11)},{white},{white},{black},"
        f"{ass_color('#000000', 0x80)},-1,0,0,0,100,100,2,0,1,{max(2, int(H * 0.002))},{max(2, int(H * 0.003))},5,30,30,30,1",
        f"Style: Sub,{fontname},{int(H * 0.03 if plan.vertical else H * 0.045)},{white},{white},{black},"
        f"{ass_color('#000000', 0x80)},0,0,0,0,100,100,1,0,1,{max(1, int(H * 0.0015))},1,5,30,30,30,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    ev = []
    srt = []
    caption_spans = []

    def dlg(layer, t0, t1, style, text):
        ev.append(f"Dialogue: {layer},{ass_time(t0)},{ass_time(t1)},{style},,0,0,0,,{text}")

    if enabled:
        words = caption_words(plan, cap)
        if not words:
            log.append("captions: no transcribed words in the cut (run transcribe.py, or set captions.enabled false)")
        chunks = chunk_words(words, int(st["max_words"]))
        emph = [norm(x) for x in cap.get("emphasis", [])]
        max_chars = int((W * 0.86) / (size * 0.56))
        face_cache = {}
        for ch in chunks:
            seg = ch[0]["seg"]
            if id(seg) not in face_cache:
                pos = seg["spec"].get("caption_pos", cap.get("position", "auto"))
                face = detect_faces(seg) if pos == "auto" else None
                if face == "nocv" and not log.count("nocv"):
                    log.append("nocv")
                face_cache[id(seg)] = (face, pos)
            face, pos = face_cache[id(seg)]
            toks = [w["text"].upper() if st.get("upper") else w["text"] for w in ch]
            lines = wrap_lines(toks, max_chars)
            block_h = int(len(lines) * size * 1.15 + outline * 2)
            y, scale, zone = place_caption(plan, face if face != "nocv" else None, block_h, pos)
            seg.setdefault("zones", set()).add(zone)
            style = "CapBox" if (st.get("box") or pos == "top") else "Cap"
            t_start, t_end = ch[0]["t0"], ch[-1]["chunk_end"]
            caption_spans.append((t_start, t_end, y, block_h * scale))
            srt.append((t_start, t_end, " ".join(w["text"] for w in ch)))
            fs = f"\\fscx{int(scale * 100)}\\fscy{int(scale * 100)}" if scale != 1.0 else ""
            base = f"{{\\an5\\pos({W // 2},{y}){fs}}}"
            pop = (f"{{\\fscx{int(scale * 82)}\\fscy{int(scale * 82)}\\t(0,90,\\fscx{int(scale * 106)}\\fscy{int(scale * 106)})"
                   f"\\t(90,170,\\fscx{int(scale * 100)}\\fscy{int(scale * 100)})}}") if st.get("pop") else ""
            fade = "{\\fad(80,60)}" if not st.get("pop") else ""

            def render_line(active):
                parts, k = [], 0
                for li, line in enumerate(lines):
                    out = []
                    for tok in line:
                        hot = (k == active and st.get("highlight")) or any(x and x in norm(tok) for x in emph)
                        tx = ass_escape(tok)
                        out.append(f"{{\\c{acc}}}{tx}{{\\c{white}}}" if hot else tx)
                        k += 1
                    parts.append(" ".join(out))
                return "\\N".join(parts)

            if st.get("highlight") and len(ch) > 1:
                for i, w in enumerate(ch):
                    a = t_start if i == 0 else w["t0"]
                    b = ch[i + 1]["t0"] if i + 1 < len(ch) else t_end
                    if b - a < 0.01:
                        continue
                    dlg(1, a, b, style, base + (pop if i == 0 else "") + render_line(i))
            else:
                dlg(1, t_start, t_end, style, base + pop + fade + render_line(0 if st.get("highlight") else -1))

    # text overlays
    top, bottom = safe_zone(plan)
    for i, o in overlays_text:
        where = f"overlays[{i}] ({o['type']})"
        default_dur = {"hook": 2.6, "title": 2.0, "cta": 3.0}.get(o["type"], 1.6)
        t0, t1 = plan.span(o, where, default_dur)
        x, y = o.get("pos") if isinstance(o.get("pos"), list) else (None, None)
        x = int(x * W) if x is not None and x <= 1 else (x or W // 2)
        anim = o.get("anim", {"hook": "pop", "chip": "pop", "title": "rise", "lower_third": "slide",
                              "cta": "pop", "text": "fade"}.get(o["type"], "fade"))
        text = ass_escape(str(o.get("text", "")))
        if o["type"] == "hook":
            yy = y or int(H * (0.22 if plan.vertical else 0.18))
            lines = str(o.get("text", "")).split("\n")
            first = ass_escape(lines[0])
            body = first
            if len(lines) > 1:
                body += f"\\N{{\\3c{acc}\\4c{acc}\\c{ass_color('#111111')}}}" + ass_escape(" ".join(lines[1:]))
            dlg(5, t0, t1, "Hook", f"{{\\an5\\pos({x},{int(yy * H if yy <= 1 else yy)})}}{anim_tags(anim, x, yy if yy > 1 else int(yy * H), W, H)}{body}")
            check_overlap(log, where, t0, t1, int(yy if yy > 1 else yy * H), caption_spans)
        elif o["type"] in ("chip", "text"):
            yy = y if y is not None else (o.get("y") or (H * (0.31 if plan.vertical else 0.14)))
            yy = int(yy * H if yy <= 1 else yy)
            style = "Box" if o["type"] == "chip" or o.get("box", False) else "Sub"
            color = f"{{\\3c{ass_color(o['box_color'])}\\4c{ass_color(o['box_color'])}}}" if o.get("box_color") else ""
            if anim == "typewriter":
                raw = str(o.get("text", ""))
                step = min(0.05, (t1 - t0) * 0.5 / max(1, len(raw)))
                for k in range(1, len(raw) + 1):
                    a = t0 + (k - 1) * step
                    b = t0 + k * step if k < len(raw) else t1
                    dlg(5, a, b, style, f"{{\\an5\\pos({x},{yy})}}{color}{ass_escape(raw[:k])}")
            else:
                dlg(5, t0, t1, style, f"{{\\an5\\pos({x},{yy})}}{color}{anim_tags(anim, x, yy, W, H)}{text}")
            check_overlap(log, where, t0, t1, yy, caption_spans)
        elif o["type"] == "title":
            yy = int((y or 0.5) * H) if (y or 0.5) <= 1 else int(y)
            sub = o.get("sub")
            dy = int(H * (0.05 if plan.vertical else 0.08))
            dlg(6, t0, t1, "Title", f"{{\\an5\\pos({x},{yy - (dy // 2 if sub else 0)})}}{anim_tags(anim, x, yy, W, H)}{text}")
            if sub:
                dlg(6, t0 + 0.12, t1, "Sub", f"{{\\an5\\pos({x},{yy + dy})\\fad(250,200)}}{ass_escape(str(sub))}")
        elif o["type"] == "lower_third":
            xx = int(W * 0.06)
            yy = int(H * (0.70 if plan.vertical else 0.80))
            dlg(5, t0, t1, "Box", f"{{\\an4\\move({xx - 60},{yy},{xx},{yy},0,220)\\fad(150,200)}}{text}")
            if o.get("sub"):
                dlg(5, t0 + 0.1, t1, "Sub", f"{{\\an4\\move({xx - 60},{yy + int(H * 0.05)},{xx},{yy + int(H * 0.05)},0,260)\\fad(200,200)}}{ass_escape(str(o['sub']))}")
        elif o["type"] == "cta":
            yy = int((y or (0.66 if plan.vertical else 0.84)) * H) if (y or 0.66) <= 1 else int(y)
            label = text + (f"\\N{{\\fs{int(H * 0.022)}}}{ass_escape(o['handle'])}" if o.get("handle") else "")
            dlg(5, t0, t1, "Box", f"{{\\an5\\pos({x},{yy})\\3c{acc}\\4c{acc}\\c{ass_color('#111111')}}}"
                f"{anim_tags('pop', x, yy, W, H)}{label}")
        else:
            raise CutroomError(f"{where}: unknown type. Text types: hook, chip, text, title, lower_third, cta.")
    return "\n".join(head + ev) + "\n", srt


def anim_tags(anim, x, y, W, H):
    if anim == "pop":
        return "{\\fscx70\\fscy70\\t(0,110,\\fscx108\\fscy108)\\t(110,190,\\fscx100\\fscy100)\\fad(0,160)}"
    if anim == "rise":
        return f"{{\\move({x},{y + int(H * 0.025)},{x},{y},0,350)\\fad(300,250)}}"
    if anim == "slide":
        return f"{{\\move({x - int(W * 0.08)},{y},{x},{y},0,240)\\fad(150,200)}}"
    if anim == "none":
        return ""
    return "{\\fad(180,180)}"


def check_overlap(log, where, t0, t1, y, spans):
    for a, b, cy, bh in spans:
        if a < t1 and b > t0 and abs(cy - y) < bh / 2 + 70:
            log.append(f"WARNING: {where} at {fmt_tc(t0)} overlaps captions (y {cy}); move it ('pos') or in time")
            return


# ---------------------------------------------------------------- images and blur boxes

def prep_image(src: Path, out: Path, width_px: int, rounded=True):
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageOps
    except ImportError:
        return src, None
    im = ImageOps.exif_transpose(Image.open(src)).convert("RGBA")
    s = width_px / im.width
    im = im.resize((width_px, max(2, int(im.height * s))), Image.LANCZOS)
    if rounded:
        r = int(width_px * 0.035)
        mask = Image.new("L", im.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, im.width - 1, im.height - 1], r, fill=255)
        im.putalpha(mask)
        pad = int(width_px * 0.04)
        canvas = Image.new("RGBA", (im.width + 2 * pad, im.height + 2 * pad), (0, 0, 0, 0))
        sh = Image.new("RGBA", im.size, (0, 0, 0, 150))
        sh.putalpha(mask.point(lambda v: 150 if v else 0))
        canvas.alpha_composite(sh, (pad, pad + pad // 3))
        canvas = canvas.filter(ImageFilter.GaussianBlur(pad // 3))
        canvas.alpha_composite(im, (pad, pad))
        im = canvas
    im.save(out)
    return out, im.size


def build_video_graph(plan, img_overlays, blurs, render_dir: Path, log):
    """filter_complex fragments for blur boxes and image overlays on [0:v]; extra inputs list."""
    W, H = plan.W, plan.H
    inputs, parts, cur, n = [], [], "0:v", 1
    for i, o in blurs:
        where = f"overlays[{i}] (blur)"
        t0, t1 = plan.span(o, where, plan.total)
        x, y, w, h = o["box"]
        if max(x, y, w, h) <= 1:
            x, y, w, h = x * W, y * H, w * W, h * H
        x, y, w, h = even(x), even(y), even(w), even(h)
        strength = int(o.get("strength", 20))
        mode = "pixelate" if o.get("pixelate") else "blur"
        eff = (f"scale={max(2, w // 16)}:{max(2, h // 16)},scale={w}:{h}:flags=neighbor" if mode == "pixelate"
               else f"boxblur={strength}:3")
        parts.append(f"[{cur}]split[b{i}a][b{i}b];[b{i}b]crop={w}:{h}:{x}:{y},{eff}[b{i}c];"
                     f"[b{i}a][b{i}c]overlay={x}:{y}:enable='between(t,{t0:.3f},{t1:.3f})'[v{i}b]")
        cur = f"v{i}b"
    for i, o in img_overlays:
        where = f"overlays[{i}] (image)"
        f = plan.project / o["file"]
        if not f.exists():
            raise CutroomError(f"{where}: image {f} not found. Put it in assets/ and use \"assets/<name>\".")
        t0, t1 = plan.span(o, where, 1.8)
        layout = o.get("layout", "card")
        if layout == "full":
            inputs += ["-loop", "1", "-framerate", str(plan.fps), "-t", f"{t1 - t0 + 0.1:.3f}", "-i", str(f)]
            d = t1 - t0
            parts.append(f"[{n}:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                         f"format=rgba,fade=t=in:st=0:d=0.15:alpha=1,fade=t=out:st={max(0, d - 0.15):.3f}:d=0.15:alpha=1,"
                         f"setpts=PTS+{t0:.3f}/TB[im{i}];[{cur}][im{i}]overlay=0:0:eof_action=pass:"
                         f"enable='between(t,{t0:.3f},{t1:.3f})'[v{i}i]")
        else:
            wfrac = float(o.get("width", 0.8))
            pre, size = prep_image(f, render_dir / f"img_{i}.png", int(W * wfrac), o.get("rounded", True))
            inputs += ["-loop", "1", "-framerate", str(plan.fps), "-t", f"{t1 - t0 + 0.1:.3f}", "-i", str(pre)]
            pos = o.get("pos", "top")
            if isinstance(pos, list):
                cx, cy = pos[0] * W if pos[0] <= 1 else pos[0], pos[1] * H if pos[1] <= 1 else pos[1]
            else:
                cx = W / 2
                cy = {"top": H * (0.30 if plan.vertical else 0.3), "center": H / 2,
                      "bottom": H * (0.62 if plan.vertical else 0.7)}.get(pos, H * 0.3)
            d = t1 - t0
            slide = int(H * 0.02)
            parts.append(f"[{n}:v]format=rgba,fade=t=in:st=0:d=0.15:alpha=1,"
                         f"fade=t=out:st={max(0, d - 0.15):.3f}:d=0.15:alpha=1,setpts=PTS+{t0:.3f}/TB[im{i}];"
                         f"[{cur}][im{i}]overlay=x='{cx:.0f}-w/2':y='{cy:.0f}-h/2+{slide}*max(0,1-(t-{t0:.3f})/0.2)'"
                         f":eof_action=pass:enable='between(t,{t0:.3f},{t1:.3f})'[v{i}i]")
            if size:
                log.append(f"image overlays[{i}] {o['file']}: {size[0]}x{size[1]} px at y {cy:.0f}, {fmt_tc(t0)}-{fmt_tc(t1)}")
        cur = f"v{i}i"
        n += 1
    return inputs, parts, cur


# ---------------------------------------------------------------- audio

def resolve_audio(project: Path, f: str, where: str) -> Path:
    p = Path(f)
    if not p.is_absolute():
        p = project / f
    if not p.exists():
        raise CutroomError(f"{where}: audio file {p} not found (paths are relative to the project folder)")
    return p


def build_audio(plan, render_dir: Path, dialog: Path, key: Path, log):
    """Returns paths of music and sfx stems (ducked music), both exactly the timeline length."""
    T = plan.total
    project = plan.project
    music = plan.edit.get("music") or []
    if isinstance(music, dict):
        music = [music]
    sfx = plan.edit.get("sfx") or []
    mus_out, sfx_out = render_dir / "music.wav", render_dir / "sfx.wav"
    duck = {"threshold": 0.025, "ratio": 8, "attack": 20, "release": 450, **(plan.edit.get("duck") or {})}

    def mix(entries, out, kind):
        if not entries:
            silence(T, out)
            return
        inputs, chains = [], []
        for i, m in enumerate(entries):
            where = f"{kind}[{i}]"
            f = resolve_audio(project, m["file"], where)
            if kind == "music":
                t0 = plan.anchor(m.get("at", 0.0), where)
                t1 = plan.anchor(m["until"], where) if "until" in m else T
                start = float(m.get("start", 0.0))
                d = max(0.05, t1 - t0)
                fi, fo = float(m.get("fade_in", 0.3)), float(m.get("fade_out", 1.5))
                inputs += ["-ss", f"{start:.3f}", "-t", f"{d:.3f}", "-i", str(f)]
                chains.append(f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                              f"volume={float(m.get('vol', 0.25))},afade=t=in:st=0:d={fi},"
                              f"afade=t=out:st={max(0, d - fo):.3f}:d={fo},adelay={int(t0 * 1000)}:all=1[m{i}]")
            else:
                t0 = plan.anchor(m.get("at", 0.0), where)
                inputs += ["-i", str(f)]
                chains.append(f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                              f"volume={float(m.get('vol', 0.4))},adelay={int(max(0, t0) * 1000)}:all=1[m{i}]")
        labels = "".join(f"[m{i}]" for i in range(len(entries)))
        fc = ";".join(chains) + f";{labels}amix=inputs={len(entries)}:normalize=0:dropout_transition=0,apad,atrim=0:{T:.4f}[out]"
        run(["ffmpeg", "-hide_banner", "-y", "-v", "error", *inputs, "-filter_complex", fc, "-map", "[out]",
             "-c:a", "pcm_s16le", "-ar", "48000", str(out)])

    mix(music, render_dir / "music_raw.wav", "music")
    if music and any(m.get("duck", True) for m in music):
        run(["ffmpeg", "-hide_banner", "-y", "-v", "error", "-i", str(render_dir / "music_raw.wav"), "-i", str(key),
             "-filter_complex",
             f"[1:a]volume=2[k];[0:a][k]sidechaincompress=threshold={duck['threshold']}:ratio={duck['ratio']}:"
             f"attack={duck['attack']}:release={duck['release']}[out]",
             "-map", "[out]", "-c:a", "pcm_s16le", str(mus_out)])
    else:
        shutil.copy(render_dir / "music_raw.wav", mus_out)
    mix(sfx, sfx_out, "sfx")
    return mus_out, sfx_out


def measure_loudness(path: Path):
    p = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
            check=False)
    txt = p.stderr
    i = re.findall(r"I:\s+(-?[\d.]+|-inf) LUFS", txt)
    tp = re.findall(r"Peak:\s+(-?[\d.]+|-inf) dBFS", txt)
    li = float(i[-1]) if i and i[-1] != "-inf" else -70.0
    pk = float(tp[-1]) if tp and tp[-1] != "-inf" else -70.0
    return li, pk


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", help="project folder (contains edit.json)")
    ap.add_argument("--draft", action="store_true", help="fast low-quality encode for checking the cut")
    ap.add_argument("--out", help="output file name in exports/ (default: <project name>.mp4)")
    ap.add_argument("--package", action="store_true",
                    help="also export a hand-off package for CapCut/Premiere/DaVinci (picture+dialog, stems)")
    ap.add_argument("--no-cache", action="store_true", help="re-render every segment")
    ap.add_argument("--edit", default="edit.json", help="edit file inside the project (default edit.json)")
    args = ap.parse_args()
    need("ffmpeg")
    need("ffprobe")
    project = project_dir(args.project)
    edit = read_json(project / args.edit)
    if edit is None:
        die(f"{project / args.edit} not found. Write the edit decision list first (see references/edit-json.md).")
    plan = Plan(project, edit, args.draft)
    render_dir = project / "work" / "render"
    render_dir.mkdir(parents=True, exist_ok=True)
    review = project / "work" / "review"
    exports = project / "exports"
    log = list(plan.warnings)

    print(f"Timeline {plan.W}x{plan.H} @ {plan.fps} fps, {len(plan.segments)} segments, {fmt_tc(plan.total)}")
    grade = edit.get("grade") or {}
    built = 0
    for k, s in enumerate(plan.segments):
        built += render_segment(s, plan, grade, render_dir, not args.no_cache)
        print(f"\r  segments {k + 1}/{len(plan.segments)} ({built} new)", end="", flush=True)
    print()

    concat_list([s["vfile"] for s in plan.segments], render_dir / "video.txt")
    concat_list([s["afile"] for s in plan.segments], render_dir / "dialog.txt")
    base, dialog, key = render_dir / "base.mp4", render_dir / "dialog.wav", render_dir / "key.wav"
    run(["ffmpeg", "-hide_banner", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(render_dir / "video.txt"),
         "-c", "copy", str(base)])
    run(["ffmpeg", "-hide_banner", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(render_dir / "dialog.txt"),
         "-c:a", "pcm_s16le", str(dialog)])
    keys = []
    for s in plan.segments:
        c = s["spec"]
        is_key = c.get("duck", s["kind"] == "video" and float(c.get("vol", 1.0)) >= 0.5)
        if is_key:
            keys.append(s["afile"])
        else:
            z = render_dir / f"sil_{s['frames']}.wav"
            if not z.exists():
                silence(s["dur"], z)
            keys.append(z)
    concat_list(keys, render_dir / "key.txt")
    run(["ffmpeg", "-hide_banner", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(render_dir / "key.txt"),
         "-c:a", "pcm_s16le", str(key)])

    # overlays
    ovs = edit.get("overlays") or []
    text_types = {"hook", "chip", "text", "title", "lower_third", "cta"}
    txt = [(i, o) for i, o in enumerate(ovs) if o.get("type") in text_types]
    imgs = [(i, o) for i, o in enumerate(ovs) if o.get("type") == "image"]
    blurs = [(i, o) for i, o in enumerate(ovs) if o.get("type") in ("blur", "pixelate")]
    for i, o in blurs:
        if o.get("type") == "pixelate":
            o["pixelate"] = True
    unknown = [o.get("type") for o in ovs if o.get("type") not in text_types | {"image", "blur", "pixelate"}]
    if unknown:
        raise CutroomError(f"unknown overlay types {unknown}. Allowed: {sorted(text_types | {'image', 'blur', 'pixelate'})}")
    style = edit.get("style") or {}
    font = style.get("font", "Montserrat")
    if not find_font(font, project):
        fallback = "Arial" if os.name == "nt" or sys.platform == "darwin" else "DejaVu Sans"
        log.append(f"font '{font}' not found (put its .ttf into assets/fonts/), using {fallback}")
        font = fallback
    ass_text, srt = build_ass(plan, txt, font, style.get("accent", "#FFD60A"), log)
    (render_dir / "subs.ass").write_text(ass_text, encoding="utf-8")
    if srt:
        (exports / "captions.srt").write_text(
            "\n".join(f"{k + 1}\n{srt_time(a)} --> {srt_time(b)}\n{t}\n" for k, (a, b, t) in enumerate(srt)),
            encoding="utf-8")

    mus, sfx = build_audio(plan, render_dir, dialog, key, log)
    pre = render_dir / "premix.wav"
    run(["ffmpeg", "-hide_banner", "-y", "-v", "error", "-i", str(dialog), "-i", str(mus), "-i", str(sfx),
         "-filter_complex", f"[0:a][1:a][2:a]amix=inputs=3:normalize=0,atrim=0:{plan.total:.4f}[a]", "-map", "[a]",
         "-c:a", "pcm_s16le", str(pre)])
    target = float(edit.get("loudness", -14))
    li, pk = measure_loudness(pre)
    gain = max(-30.0, min(30.0, target - li)) if li > -69 else 0.0
    limiter = pk + gain > -1.0

    inputs, parts, vlabel = build_video_graph(plan, imgs, blurs, render_dir, log)
    fontsdir = ""
    if (project / "assets" / "fonts").exists():
        # relative to the render dir (ffmpeg runs there): drive-letter colons break filter parsing
        fontsdir = ":fontsdir=" + Path(os.path.relpath(project / "assets" / "fonts", render_dir)).as_posix()
    vchain = (";".join(parts) + ";" if parts else "") + f"[{vlabel}]ass=subs.ass{fontsdir},format=yuv420p[vout]"
    out_name = args.out or f"{project.name}.mp4"
    final = exports / out_name
    a_idx = 1 + sum(1 for x in inputs if x == "-i")
    afilter = f"[{a_idx}:a]volume={gain:.2f}dB" + (",alimiter=limit=0.891:level=false" if limiter else "") + "[aout]"
    run(["ffmpeg", "-hide_banner", "-y", "-v", "error", "-i", str(base), *inputs, "-i", str(pre),
         "-filter_complex", vchain + ";" + afilter, "-map", "[vout]", "-map", "[aout]",
         *video_encoder(final=True, draft=args.draft), "-pix_fmt", "yuv420p", *COLOR_TAGS,
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
         "-frames:v", str(plan.total_frames), str(final.resolve())], cwd=render_dir)

    # exports: edl, stems
    with (exports / "edl.csv").open("w", encoding="utf-8", newline="") as fh:
        fh.write("n,timeline_in,timeline_out,clip,source,source_in,source_out,note\n")
        for k, s in enumerate(plan.segments):
            src = s["path"].name if s["path"] else "card"
            note = str(s["spec"].get("note", s["spec"].get("card", ""))).replace(",", ";")
            a = "" if s["a"] is None else f"{s['a']:.2f}"
            b = "" if s["b"] is None else f"{s['b']:.2f}"
            fh.write(f"{k + 1},{fmt_tc(s['t0'])},{fmt_tc(s['t0'] + s['dur'])},{s['clip']},{src},{a},{b},{note}\n")
    if args.package:
        st = exports / "package"
        st.mkdir(exist_ok=True)
        for name, src in (("dialog.wav", dialog), ("music.wav", mus), ("sfx.wav", sfx)):
            run(["ffmpeg", "-hide_banner", "-y", "-v", "error", "-i", str(src), "-af", f"volume={gain:.2f}dB",
                 "-c:a", "pcm_s16le", str(st / name)])
        run(["ffmpeg", "-hide_banner", "-y", "-v", "error", "-i", str(base), "-i", str(st / "dialog.wav"),
             "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
             str(st / "picture_with_dialog.mp4")])
        shutil.copy(render_dir / "subs.ass", st / "captions.ass")
        if (exports / "captions.srt").exists():
            shutil.copy(exports / "captions.srt", st / "captions.srt")
        shutil.copy(exports / "edl.csv", st / "markers.csv")
        (st / "README.txt").write_text(
            "Hand-off package\n\n"
            "picture_with_dialog.mp4  the cut without captions/overlays, original sound only\n"
            "dialog.wav, music.wav, sfx.wav  stems at final mix level, all start at 0:00 and have the same length\n"
            "captions.srt  captions; import as subtitles\n"
            "markers.csv  every cut with timeline and source times\n\n"
            "In CapCut/Premiere/DaVinci: put picture_with_dialog.mp4 on track 1, mute its audio if you use dialog.wav,\n"
            "add music.wav and sfx.wav at 0:00 on their own tracks. Swap the music freely: the cut does not depend on it.\n",
            encoding="utf-8")

    # review stills
    if review.exists():
        for f in review.glob("*.jpg"):
            f.unlink()
    review.mkdir(parents=True, exist_ok=True)
    times = {0.4, max(0.0, plan.total - 0.4)}
    for s in plan.segments:
        times.add(s["t0"] + s["dur"] / 2)
    for i, o in enumerate(ovs):
        try:
            a, b = plan.span(o, f"overlays[{i}]", 1.5)
            times.add((a + b) / 2)
        except CutroomError:
            pass
    times = sorted(t for t in times if t < plan.total)
    if len(times) > 60:
        times = [times[int(k * len(times) / 60)] for k in range(60)]
    for t in times:
        run(["ffmpeg", "-hide_banner", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(final), "-frames:v", "1",
             "-vf", "scale=540:-2", "-q:v", "4", str(review / f"t{t:07.2f}.jpg")], check=False)

    # report
    print(f"\nSegments (timeline | source in-out | caption zone):")
    for k, s in enumerate(plan.segments):
        src = s["path"].name if s["path"] else f"card '{s['spec'].get('card', '')}'"
        rng = "" if s["a"] is None or s["kind"] != "video" else f"{s['a']:.2f}-{s['b']:.2f}"
        zones = ", ".join(sorted(s.get("zones", []))) or "-"
        print(f"  {k + 1:3d}  {fmt_tc(s['t0'])}-{fmt_tc(s['t0'] + s['dur'])}  clip {s['clip']:<3d} {src[:34]:<34} {rng:<13} {zones}")
    for line in log:
        if line == "nocv":
            print("WARNING: opencv not installed, captions use the default position (pip install opencv-python-headless)")
        else:
            print(("" if line.startswith(("WARNING", "image")) else "WARNING: ") + line)
    fb = sum(1 for s in plan.segments if any("FALLBACK" in z for z in s.get("zones", [])))
    if fb:
        print(f"WARNING: {fb} segment(s) had no room for captions beside the face (FALLBACK). Check their stills.")
    print(f"\nLoudness: premix {li:.1f} LUFS, gain {gain:+.1f} dB -> target {target} LUFS"
          f"{' (limiter on)' if limiter else ''}")
    print(f"Done: {final}  ({fmt_tc(plan.total)})")
    print(f"Review stills: {review} ({len(times)} JPGs) | captions: {exports / 'captions.srt'} | cuts: {exports / 'edl.csv'}")
    if args.package:
        print(f"Hand-off package: {exports / 'package'}")


if __name__ == "__main__":
    try:
        main()
    except CutroomError as e:
        die(str(e))
