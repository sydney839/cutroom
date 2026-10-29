#!/usr/bin/env python3
"""Quality gate for a finished render: technical specs, loudness, black/frozen/silent parts, QR codes.

Usage:
    python qa.py <video> [--project <dir>] [--every 1] [--target-lufs -14] [--expect 1080x1920] [--no-sheet]

Checks: ffprobe specs (resolution, fps, codec, pixel format, color tags, audio present), integrated
loudness and true peak (ebur128), black frames (>= 0.3 s), frozen picture (>= 1 s), silence (>= 1.5 s
below -45 dB), QR codes in the picture (privacy; needs opencv-python-headless). Also writes a contact
sheet (one tile every --every seconds) for you to look at.
Report: <project>/exports/qa.md, or <video>.qa.md without --project. One PASS/WARN/FAIL line per check.
Exit code: 0 = no FAIL, 2 = at least one FAIL. A WARN needs a look, it is not necessarily wrong
(a held photo is "frozen" on purpose).
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CutroomError, die, ffprobe, fmt_tc, need, project_dir  # noqa: E402
from sheet import sheet_for_video  # noqa: E402

results = []  # (level, name, text)


def add(level, name, text):
    results.append((level, name, text))


def frac(s):
    try:
        a, b = str(s).split("/")
        return float(a) / float(b) if float(b) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def num(s):
    return float("-inf") if "inf" in s else float(s)


def check_specs(path, args):
    info = ffprobe(path)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v is None:
        raise CutroomError(f"{path} has no video stream. Pass the rendered video file.")
    w, h = int(v["width"]), int(v["height"])
    dur = float(info.get("format", {}).get("duration") or v.get("duration") or 0)
    r, avg = frac(v.get("r_frame_rate")), frac(v.get("avg_frame_rate"))
    add("INFO", "specs", f"{w}x{h}, {avg or r:.2f} fps, {v.get('codec_name')} {v.get('profile', '')}".strip()
        + f", {v.get('pix_fmt')}, {fmt_tc(dur)} ({dur:.1f} s), {float(info['format'].get('size', 0)) / 1e6:.1f} MB")
    if args.expect:
        m = re.fullmatch(r"(\d+)x(\d+)", args.expect)
        if not m:
            raise CutroomError(f"--expect '{args.expect}' must look like 1080x1920.")
        ok = (w, h) == (int(m[1]), int(m[2]))
        add("PASS" if ok else "FAIL", "resolution", f"{w}x{h}" + ("" if ok else f", expected {args.expect}"))
    if r and avg and abs(r - avg) / max(r, avg) > 0.02:
        add("WARN", "frame rate", f"variable frame rate (r={r:.2f}, avg={avg:.2f}); some players drift out of sync")
    elif r:
        add("PASS", "frame rate", f"constant {r:.2f} fps")
    if v.get("pix_fmt") != "yuv420p":
        add("WARN", "pixel format", f"{v.get('pix_fmt')}; yuv420p plays everywhere (phones, social upload)")
    else:
        add("PASS", "pixel format", "yuv420p")
    tags = [v.get("color_space"), v.get("color_primaries"), v.get("color_transfer")]
    if all(t == "bt709" for t in tags):
        add("PASS", "color tags", "bt709")
    else:
        add("WARN", "color tags", f"space/primaries/transfer = {'/'.join(t or 'unset' for t in tags)}; expected bt709 "
            f"(untagged or HDR tags can make colors look washed out)")
    if v.get("codec_name") not in ("h264", "hevc"):
        add("WARN", "codec", f"{v.get('codec_name')}; h264 is the safe choice for social platforms")
    if a is None:
        add("WARN", "audio", "no audio stream")
    else:
        add("PASS", "audio", f"{a.get('codec_name')} {a.get('sample_rate')} Hz, {a.get('channels')} ch")
        vd, ad = float(v.get("duration") or 0), float(a.get("duration") or 0)
        if vd and ad and abs(vd - ad) > 0.3:
            add("WARN", "A/V length", f"video {vd:.2f} s, audio {ad:.2f} s; "
                f"the {'audio' if ad > vd else 'video'} runs {abs(vd - ad):.1f} s longer (silent or frozen tail in players)")
    return dur, a is not None, (w, h)


def check_signal(path, dur, has_audio, args):
    """One ffmpeg pass: loudness, silence, black frames, frozen picture."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "info", "-nostdin", "-i", str(path),
           "-vf", "blackdetect=d=0.3:pix_th=0.10,freezedetect=n=-60dB:d=1.0"]
    if has_audio:
        cmd += ["-af", "ebur128=peak=true:framelog=quiet,silencedetect=n=-45dB:d=1.5"]
    p = subprocess.run(cmd + ["-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0:
        raise CutroomError("ffmpeg analysis failed:\n" + "\n".join(p.stderr.strip().splitlines()[-8:]))
    log = p.stderr

    if has_audio:
        i = re.search(r"Integrated loudness:\s+I:\s+(-?[\d.]+|-?inf)\s+LUFS", log)
        pk = re.search(r"True peak:\s+Peak:\s+(-?[\d.]+|-?inf)\s+dBFS", log)
        lra = re.search(r"LRA:\s+([\d.]+)\s+LU", log)
        if not i:
            add("WARN", "loudness", "ebur128 gave no result; audio may be empty")
        else:
            lufs = num(i[1])
            diff = lufs - args.target_lufs
            lv = "PASS" if abs(diff) <= 1 else ("FAIL" if abs(diff) > 3 or lufs < -60 else "WARN")
            add(lv, "loudness", f"{lufs:.1f} LUFS integrated (target {args.target_lufs:g} +-1)"
                + (f", LRA {lra[1]} LU" if lra else "")
                + ("" if lv == "PASS" else f"; {'too quiet' if diff < 0 else 'too loud'} by {abs(diff):.1f} LU"))
        if pk:
            tp = num(pk[1])
            lv = "PASS" if tp <= -1 else ("FAIL" if tp > 0 else "WARN")
            add(lv, "true peak", f"{tp:.1f} dBTP (limit -1)" + ("" if lv == "PASS" else "; may clip after platform re-encoding"))
        sil = [(float(s), None) for s in re.findall(r"silence_start:\s*(-?[\d.]+)", log)]
        ends = [float(e) for e in re.findall(r"silence_end:\s*(-?[\d.]+)", log)]
        spans = [(max(s, 0), ends[k] if k < len(ends) else dur) for k, (s, _) in enumerate(sil)]
        if spans:
            add("WARN", "silence", "; ".join(f"{fmt_tc(s)}-{fmt_tc(e)} ({e - s:.1f} s)" for s, e in spans[:8])
                + " silent (< -45 dB for >= 1.5 s); dead air or missing music?")
        else:
            add("PASS", "silence", "no gaps >= 1.5 s")

    blacks = [(float(s), float(e)) for s, e in re.findall(r"black_start:([\d.]+)\s+black_end:([\d.]+)", log)]
    if blacks:
        total = sum(e - s for s, e in blacks)
        add("FAIL" if dur and total > 0.5 * dur else "WARN", "black frames",
            "; ".join(f"{fmt_tc(s)}-{fmt_tc(e)}" for s, e in blacks[:8]) + f" ({total:.1f} s black in total)")
    else:
        add("PASS", "black frames", "none >= 0.3 s")
    fs = [float(x) for x in re.findall(r"freeze_start:\s*([\d.]+)", log)]
    fe = [float(x) for x in re.findall(r"freeze_end:\s*([\d.]+)", log)]
    if fs:
        spans = [(s, fe[k] if k < len(fe) else dur) for k, s in enumerate(fs)]
        add("WARN", "frozen picture", "; ".join(f"{fmt_tc(s)}-{fmt_tc(e)}" for s, e in spans[:8])
            + " no motion for >= 1 s (fine for a held photo or title, otherwise a stuck frame)")
    else:
        add("PASS", "frozen picture", "none >= 1 s")


def check_qr(path, dur):
    try:
        import cv2
    except ImportError:
        add("INFO", "QR codes", "not scanned: opencv missing. Run: pip install opencv-python-headless")
        return
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    step, det = max(1, int(round(fps * 0.5))), cv2.QRCodeDetector()
    hits, n = [], 0  # (time, box, text)
    while cap.grab():
        if n % step == 0:
            ok, frame = cap.retrieve()
            if ok:
                found, pts = det.detectMulti(frame)
                if found and pts is not None:
                    try:
                        _, texts, _, _ = det.detectAndDecodeMulti(frame)
                    except Exception:
                        texts = [""] * len(pts)
                    for k, quad in enumerate(pts):
                        x, y = quad[:, 0], quad[:, 1]
                        hits.append((n / fps, (int(x.min()), int(y.min()), int(x.max() - x.min()), int(y.max() - y.min())),
                                     texts[k] if k < len(texts) else ""))
        n += 1
    cap.release()
    if not hits:
        add("PASS", "QR codes", "none visible (sampled every 0.5 s)")
        return
    t0, box, txt = hits[0]
    t1 = hits[-1][0]
    add("WARN", "QR codes", f"{len(hits)} detection(s) between {fmt_tc(t0)} and {fmt_tc(t1)}, first box x,y,w,h = {box}"
        + (f", content '{txt[:60]}'" if txt else "") + "; check it is meant to be public (links, accounts, tickets)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video")
    ap.add_argument("--project", help="project folder (report goes to exports/qa.md, sheet to work/review/)")
    ap.add_argument("--every", type=float, default=1.0, help="seconds between contact-sheet tiles (default 1)")
    ap.add_argument("--target-lufs", type=float, default=-14.0, help="loudness target (default -14)")
    ap.add_argument("--expect", help="expected resolution, e.g. 1080x1920")
    ap.add_argument("--no-sheet", action="store_true", help="skip the contact sheet")
    args = ap.parse_args()
    need("ffmpeg"), need("ffprobe")
    video = Path(args.video).expanduser().resolve()
    if not video.exists():
        raise CutroomError(f"{video} does not exist. Pass the rendered video (e.g. <project>/exports/final.mp4).")
    proj = project_dir(args.project) if args.project else None

    dur, has_audio, _ = check_specs(video, args)
    check_signal(video, dur, has_audio, args)
    check_qr(video, dur)
    if not args.no_sheet:
        out_dir = (proj / "work" / "review") if proj else video.parent
        paths, n = sheet_for_video(video, args.every, out_dir, f"{video.stem}_qa")
        add("INFO", "contact sheet", f"{n} tiles: " + ", ".join(str(p) for p in paths))

    nf = sum(1 for lv, *_ in results if lv == "FAIL")
    nw = sum(1 for lv, *_ in results if lv == "WARN")
    head = f"QA {'FAIL' if nf else ('WARN' if nw else 'PASS')}: {video.name} ({nf} fail, {nw} warn)"
    lines = [f"{lv:<4} {name}: {text}" for lv, name, text in results]
    report = (proj / "exports" / "qa.md") if proj else video.with_suffix(video.suffix + ".qa.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(f"# {head}\n\n" + "\n".join(f"- **{lv}** {name}: {text}" for lv, name, text in results) + "\n",
                      encoding="utf-8")
    print(head)
    print("\n".join(lines))
    print(f"report: {report}")
    sys.exit(2 if nf else 0)


if __name__ == "__main__":
    try:
        main()
    except CutroomError as e:
        die(str(e))
