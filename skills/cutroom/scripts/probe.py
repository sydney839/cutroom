#!/usr/bin/env python3
"""Scan media files, give each a stable ID and write work/inventory.csv.

Usage:
    python probe.py <project> <files-or-folders...> [--prefix C]

Folders are scanned recursively and sorted naturally (clip2 before clip10).
IDs are prefix + 3 digits (C001). If work/inventory.csv already lists a path, it keeps
its ID; new files are appended with the next free number. Originals are never touched.
"""
import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (AUDIO_EXT, PHOTO_EXT, VIDEO_EXT, CutroomError, die, ffprobe,  # noqa: E402
                    need, project_dir)

COLUMNS = ["id", "path", "type", "duration_s", "width", "height", "fps", "vfr", "rotation", "hdr",
           "has_audio", "codec", "created", "created_source", "size_mb"]


def natural_key(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(p))]


def collect(inputs):
    files = []
    for raw in inputs:
        p = Path(raw).expanduser().resolve()
        if p.is_dir():
            files += [f for f in p.rglob("*") if f.is_file()]
        elif p.is_file():
            files.append(p)
        else:
            raise CutroomError(f"'{raw}' does not exist. Pass existing files or folders.")
    exts = VIDEO_EXT | PHOTO_EXT | AUDIO_EXT
    files = [f for f in files if f.suffix.lower() in exts and not f.name.startswith(".")]
    return sorted(set(files), key=natural_key)


def frac(s):
    try:
        a, b = str(s).split("/")
        return float(a) / float(b) if float(b) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def rotation_of(stream):
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            return int(round(sd["rotation"])) % 360
    r = (stream.get("tags") or {}).get("rotate")
    return int(r) % 360 if r else 0


def exif_date(path):
    try:
        from PIL import Image
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        with Image.open(path) as im:
            ex = im.getexif()
            val = ex.get_ifd(0x8769).get(0x9003) or ex.get(0x0132)
            if val:
                return datetime.strptime(str(val)[:19], "%Y:%m:%d %H:%M:%S").isoformat()
    except Exception:
        pass
    return None


def photo_size(path):
    """(width, height, rotation) with EXIF orientation applied, or None if Pillow cannot read it."""
    try:
        from PIL import Image
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        with Image.open(path) as im:
            w, h = im.size
            if im.getexif().get(0x0112, 1) in (5, 6, 7, 8):  # stored sideways
                return h, w, 90
            return w, h, 0
    except Exception:
        return None


def probe_file(path: Path) -> dict:
    ext = path.suffix.lower()
    row = {k: "" for k in COLUMNS}
    row.update(path=str(path), size_mb=f"{path.stat().st_size / 1e6:.1f}")
    try:
        info = ffprobe(path)
    except CutroomError:
        if ext not in PHOTO_EXT:
            raise
        info = {}  # ffprobe cannot read some HEIC/DNG; Pillow below can
    streams = info.get("streams", [])
    fmt = info.get("format", {})
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if ext in AUDIO_EXT:
        row["type"] = "audio"
    elif ext in PHOTO_EXT:
        row["type"] = "photo"
    else:
        row["type"] = "video"
    if row["type"] == "audio" or (ext in PHOTO_EXT and not v):
        # HEIC/DNG often cannot be probed by ffprobe; Pillow gives the size
        if row["type"] == "photo" and (ps := photo_size(path)):
            row["width"], row["height"], row["rotation"] = ps
        row["duration_s"] = "" if row["type"] == "photo" else round(float(fmt.get("duration", 0) or 0), 2)
        row["hdr"] = "no"
        row["codec"] = (a or {}).get("codec_name", "")
        row["has_audio"] = "" if row["type"] == "photo" else ("yes" if a else "no")
    elif v is None:
        raise CutroomError(f"{path}: no video stream found. Is the file complete and playable?")
    else:
        rot = rotation_of(v)
        w, h = int(v["width"]), int(v["height"])
        if rot in (90, 270):
            w, h = h, w
        r, avg = frac(v.get("r_frame_rate")), frac(v.get("avg_frame_rate"))
        ct = v.get("color_transfer", "")
        row.update(width=w, height=h, rotation=rot, codec=v.get("codec_name", ""),
                   fps=round(avg or r, 3),
                   vfr="yes" if r and avg and abs(r - avg) / max(r, avg) > 0.02 else "no",
                   hdr={"arib-std-b67": "HLG", "smpte2084": "PQ"}.get(ct, "no"),
                   has_audio="yes" if a else "no")
        row["duration_s"] = round(float(fmt.get("duration") or v.get("duration") or 0), 2)
        if row["type"] == "photo":
            row.update(duration_s="", fps="", vfr="", has_audio="")
            if ps := photo_size(path):
                row["width"], row["height"], row["rotation"] = ps
    # best timestamp: container metadata, then EXIF, then mtime
    tags = {k.lower(): val for k, val in (fmt.get("tags") or {}).items()}
    qt = tags.get("com.apple.quicktime.creationdate") or tags.get("creation_time")
    if qt and not qt.startswith("1904") and not qt.startswith("0000"):
        row["created"], row["created_source"] = qt[:19], "metadata"
    elif row["type"] == "photo" and (d := exif_date(path)):
        row["created"], row["created_source"] = d, "exif"
    else:
        row["created"] = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
        row["created_source"] = "mtime"
    return row


def warnings_for(row):
    w = []
    if row["type"] == "audio":
        return w
    short = min(int(row["width"] or 0), int(row["height"] or 0))
    if short and short < 1080:
        w.append(f"low resolution ({row['width']}x{row['height']}), likely a WhatsApp/messenger copy: "
                 f"ask for the original")
    if row["vfr"] == "yes":
        w.append("variable frame rate (proxy.py converts to constant fps)")
    if row["hdr"] != "no":
        w.append(f"{row['hdr']} HDR (proxy.py tone-maps to SDR)")
    if row["type"] == "video" and row["has_audio"] == "no":
        w.append("no audio track")
    return w


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", help="project folder (created if missing)")
    ap.add_argument("inputs", nargs="+", help="media files or folders")
    ap.add_argument("--prefix", default="C", help="ID prefix for new files (default C)")
    args = ap.parse_args()
    need("ffprobe")
    proj = project_dir(args.project)
    inv_path = proj / "work" / "inventory.csv"

    old = []
    if inv_path.exists():
        with inv_path.open(encoding="utf-8", newline="") as fh:
            old = list(csv.DictReader(fh))
    known = {r["path"]: r for r in old}
    used = {r["id"] for r in old}

    def next_id(start):
        n = start
        while f"{args.prefix}{n:03d}" in used:
            n += 1
        used.add(f"{args.prefix}{n:03d}")
        return f"{args.prefix}{n:03d}", n

    counter, rows, warns, skipped = 1, [], [], []
    files = collect(args.inputs)
    if not files:
        raise CutroomError("no media files found in the given paths. Supported: "
                           + " ".join(sorted(VIDEO_EXT | PHOTO_EXT | AUDIO_EXT)))
    for f in files:
        try:
            row = probe_file(f)
        except CutroomError as e:
            skipped.append(str(e).splitlines()[0])
            continue
        if str(f) in known:
            row["id"] = known[str(f)]["id"]
        else:
            row["id"], counter = next_id(counter)
        rows.append(row)
    # keep older entries whose files were not part of this scan
    seen = {r["path"] for r in rows}
    rows += [{k: r.get(k, "") for k in COLUMNS} for r in old if r["path"] not in seen]
    rows.sort(key=lambda r: (re.sub(r"\d", "", r["id"]), int(re.sub(r"\D", "", r["id"]) or 0)))

    with inv_path.open("w", encoding="utf-8", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=COLUMNS)
        wr.writeheader()
        wr.writerows(rows)

    print(f"{'ID':<6}{'type':<6}{'dur':>7}  {'size':<10}{'fps':>6} {'hdr':<4}{'aud':<4} {'created':<20}file")
    for r in rows:
        if r["path"] not in seen:
            continue
        dur = f"{float(r['duration_s']):.1f}s" if r["duration_s"] != "" else "-"
        size = f"{r['width']}x{r['height']}" if r["width"] else "-"
        fps = r["fps"] if r["fps"] != "" else "-"
        print(f"{r['id']:<6}{r['type']:<6}{dur:>7}  {size:<10}{fps!s:>6} {r['hdr']:<4}{r['has_audio']:<4} "
              f"{r['created'][:19]:<20}{Path(r['path']).name}")
        for w in warnings_for(r):
            warns.append(f"{r['id']}: {w}")
    for s in skipped:
        warns.append(f"skipped: {s}")
    if warns:
        print("\nWarnings:")
        print("\n".join("  - " + w for w in warns))
    print(f"\n{len([r for r in rows if r['path'] in seen])} items -> {inv_path}")


if __name__ == "__main__":
    try:
        main()
    except CutroomError as e:
        die(str(e))
