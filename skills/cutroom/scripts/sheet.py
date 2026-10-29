#!/usr/bin/env python3
"""Contact sheets: a grid of small frames the agent can look at with its image viewer.

Usage:
    python sheet.py <project> --video <ID|path> [--every 2]    one tile every N seconds, labeled with timecode
    python sheet.py <project> --ids C001 C002 ...              one tile per item (video: middle frame)
    python sheet.py <project> --all                            same, for the whole inventory
    options: [--cols 6] [--width 320] [--per-sheet 48] [--out name]

JPGs go to work/sheets/<name>.jpg, then <name>_2.jpg ... when there are more tiles than --per-sheet.
Portrait material gets narrower tiles and fewer per sheet by default so each sheet stays legible.
Frames come from the SDR proxy when it exists (run proxy.py first for correct HDR colors).
"""
import argparse
import shutil
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (CutroomError, die, ffprobe, fmt_tc, inventory, need, project_dir,  # noqa: E402
                    resolve_source)

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ImportError:
    Image = None


def extract_frame(src, t, out, width, hdr=False):
    """One frame at time t as JPG, `width` px wide. Fast seek (-ss before -i)."""
    vf = f"scale={width}:-2"
    if hdr:  # original HDR file without a proxy: tone-map so the tile is not washed out
        from proxy import TONEMAPS
        chains = [f"scale={width}:-2,{TONEMAPS[0][1]}", vf]
    else:
        chains = [vf]
    for chain in chains:
        p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                            *(["-ss", f"{t:.3f}"] if t > 0 else []),  # -ss on a still image yields no frame
                            "-i", str(src), "-frames:v", "1", "-an", "-vf", chain, "-q:v", "3", str(out)],
                           capture_output=True, text=True)
        if p.returncode == 0 and Path(out).exists():
            return True
    return False


def photo_tile(src, out, width):
    if Image is not None:
        try:
            try:
                import pillow_heif
                pillow_heif.register_heif_opener()
            except ImportError:
                pass
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                im.thumbnail((width, width * 3))
                im.save(out, "JPEG", quality=88)
            return True
        except Exception:
            pass
    return extract_frame(src, 0, out, width)


def font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def compose(tiles, out_base: Path, cols, width, per_sheet):
    """tiles: list of (frame_path or None, label). Returns the written sheet paths."""
    ims = []
    for fp, label in tiles:
        try:
            ims.append((Image.open(fp).convert("RGB") if fp else None, label))
        except Exception:
            ims.append((None, label))
    aspects = [im.height / im.width for im, _ in ims if im]
    asp = min(max(statistics.median(aspects) if aspects else 0.5625, 0.5625), 1.7778)
    cell_h = int(round(width * asp))
    f = font(max(11, width // 20))
    paths = []
    for n, start in enumerate(range(0, len(ims), per_sheet), 1):
        chunk = ims[start:start + per_sheet]
        rows = (len(chunk) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * width, rows * cell_h), (20, 20, 20))
        d = ImageDraw.Draw(sheet)
        for k, (im, label) in enumerate(chunk):
            x, y = (k % cols) * width, (k // cols) * cell_h
            if im:
                im = ImageOps.contain(im, (width - 2, cell_h - 2))
                sheet.paste(im, (x + (width - im.width) // 2, y + (cell_h - im.height) // 2))
            else:
                d.text((x + 6, y + cell_h // 2), "no frame", fill=(200, 80, 80), font=f)
            tw = d.textlength(label, font=f)
            d.rectangle((x + 2, y + 2, x + tw + 10, y + getattr(f, "size", 14) + 8), fill=(0, 0, 0))
            d.text((x + 6, y + 4), label, fill=(255, 255, 255), font=f)
        p = out_base.with_name(out_base.name + ("" if n == 1 else f"_{n}") + ".jpg")
        sheet.save(p, "JPEG", quality=85)
        paths.append(p)
    return paths


def compose_ffmpeg(tiles, out_base, cols, width, per_sheet):
    """Fallback without Pillow: ffmpeg tile filter, no labels (tile order is printed instead)."""
    paths = []
    good = [(fp, lb) for fp, lb in tiles if fp]
    cell_h = int(round(width * 0.5625)) // 2 * 2
    for n, start in enumerate(range(0, len(good), per_sheet), 1):
        chunk = good[start:start + per_sheet]
        tmp = Path(chunk[0][0]).parent
        for k, (fp, _) in enumerate(chunk):
            shutil.copy(fp, tmp / f"seq_{k:04d}.jpg")
        p = out_base.with_name(out_base.name + ("" if n == 1 else f"_{n}") + ".jpg")
        rows = (len(chunk) + cols - 1) // cols
        vf = (f"scale={width}:{cell_h}:force_original_aspect_ratio=decrease,"
              f"pad={width}:{cell_h}:(ow-iw)/2:(oh-ih)/2,tile={cols}x{rows}")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-framerate", "1",
                        "-i", str(tmp / "seq_%04d.jpg"), "-vf", vf, "-frames:v", "1", "-q:v", "3", str(p)],
                       check=True)
        print("(Pillow not installed: sheet has no labels. Tile order, left to right, top to bottom:)")
        print("  " + ", ".join(lb for _, lb in chunk))
        paths.append(p)
    return paths


def build(tiles_spec, out_dir: Path, name, cols, width, per_sheet):
    """tiles_spec: list of (kind, src, t, label, hdr). Extract frames in parallel, then compose."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / f".tmp_{name}"
    tmp.mkdir(exist_ok=True)
    try:
        def job(a):
            k, (kind, src, t, label, hdr) = a
            fp = tmp / f"f_{k:05d}.jpg"
            ok = photo_tile(src, fp, width) if kind == "photo" else extract_frame(src, t, fp, width, hdr)
            return (fp if ok else None), label
        with ThreadPoolExecutor(max_workers=4) as ex:
            tiles = list(ex.map(job, enumerate(tiles_spec)))
        if Image is None:
            return compose_ffmpeg(tiles, out_dir / name, cols, width, per_sheet)
        return compose(tiles, out_dir / name, cols, width, per_sheet)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def video_info(src):
    """(duration, width, height) after rotation."""
    info = ffprobe(src)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if v is None:
        raise CutroomError(f"{src} has no video stream.")
    w, h = int(v["width"]), int(v["height"])
    for sd in v.get("side_data_list", []) or []:
        if abs(int(sd.get("rotation", 0))) % 180 == 90:
            w, h = h, w
    # prefer the video stream's own length: audio running longer would give tiles past the last frame
    return float(v.get("duration") or info.get("format", {}).get("duration") or 0), w, h


def sheet_for_video(src, every, out_dir, name, cols=None, width=None, per_sheet=None):
    """Importable helper (qa.py): a sheet with one tile every `every` seconds."""
    dur, w, h = video_info(src)
    portrait = h / max(w, 1) >= 1.2
    cols = cols or 6
    width = width or (240 if portrait else 320)
    per_sheet = per_sheet or (24 if portrait else 48)
    times = [k * every for k in range(int(dur / every) + 1) if k * every < max(dur, 0.1)]
    spec = [("video", src, min(t, max(dur - 0.1, 0)), fmt_tc(t), False) for t in times]
    return build(spec, out_dir, name, cols, width, per_sheet), len(spec)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--video", help="inventory ID or path of one video")
    g.add_argument("--ids", nargs="+", help="inventory IDs, one tile each")
    g.add_argument("--all", action="store_true", help="every item in the inventory")
    ap.add_argument("--every", type=float, default=2.0, help="seconds between tiles for --video (default 2)")
    ap.add_argument("--cols", type=int, help="columns (default 6)")
    ap.add_argument("--width", type=int, help="tile width in px (default 320, portrait 240)")
    ap.add_argument("--per-sheet", type=int, help="max tiles per sheet (default 48, portrait 24)")
    ap.add_argument("--out", help="sheet base name (default: <ID>_sheet, sheet or all)")
    args = ap.parse_args()
    need("ffmpeg"), need("ffprobe")
    proj = project_dir(args.project)
    out_dir = proj / "work" / "sheets"

    if args.video:
        src = resolve_source(proj, args.video)
        name = args.out or (f"{args.video}_sheet" if args.video in inventory(proj) else f"{Path(args.video).stem}_sheet")
        paths, n = sheet_for_video(src, args.every, out_dir, name, args.cols, args.width, args.per_sheet)
        what = f"{n} tiles, one every {args.every:g} s"
    else:
        inv = inventory(proj)
        if not inv:
            raise CutroomError(f"{proj / 'work' / 'inventory.csv'} is missing. Run probe.py first.")
        ids = list(inv) if args.all else args.ids
        unknown = [i for i in ids if i not in inv]
        if unknown:
            raise CutroomError(f"unknown ID(s) {', '.join(unknown)}. Known IDs: {', '.join(inv)}")
        spec, ratios = [], []
        for i in ids:
            r = inv[i]
            if r["type"] == "audio":
                continue
            src = resolve_source(proj, i)
            proxied = src.parent.name == "proxy"
            if r["type"] == "photo":
                spec.append(("photo", src, 0, f"{i} photo {r['created'][:16].replace('T', ' ')}", False))
            else:
                dur = float(r["duration_s"] or 0)
                spec.append(("video", src, dur / 2, f"{i} {dur:.1f}s", r["hdr"] != "no" and not proxied))
            if r["width"] and r["height"]:
                ratios.append(int(r["height"]) / int(r["width"]))
        if not spec:
            raise CutroomError("no videos or photos to show.")
        portrait = bool(ratios) and statistics.median(ratios) >= 1.2
        cols, width, per = args.cols or 6, args.width or (240 if portrait else 320), args.per_sheet or (24 if portrait else 48)
        paths = build(spec, out_dir, args.out or ("all" if args.all else "sheet"), cols, width, per)
        what = f"{len(spec)} tiles"
    print(f"{what} -> {len(paths)} sheet(s):")
    for p in paths:
        print(f"  {p}")
    if len(paths) > 6:
        print("Tip: that is a lot of sheets; raise --every or look at one range at a time.")


if __name__ == "__main__":
    try:
        main()
    except CutroomError as e:
        die(str(e))
