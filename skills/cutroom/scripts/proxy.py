#!/usr/bin/env python3
"""Build edit-friendly proxies: SDR BT.709 H.264 at constant fps, plus 16 kHz mono WAV for transcription.

Usage:
    python proxy.py <project> [IDs...] [--max 1920] [--fps 30] [--force] [--no-hdr-convert]

Writes work/proxy/<ID>.mp4 (videos), work/proxy/<ID>.jpg (photos, EXIF-rotated, max 2560 px)
and work/audio/<ID>.wav. Existing outputs are skipped unless --force. Run probe.py first.
HDR (HLG/PQ, e.g. iPhone) is tone-mapped to SDR so colors look natural in the final render.
Videos without audio get a silent AAC track so later concatenation never breaks.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CutroomError, die, ffprobe, inventory, need, project_dir, run  # noqa: E402

# Tone-map chains, tried in order. Each is applied after scaling (cheaper at low resolution).
# zscale: npl=140 maps HLG diffuse white to a natural SDR exposure. The BT.2020 -> BT.709 gamut
# conversion is skipped on purpose (pin=709:p=709): with it, iPhone skin tones turn pink/red and
# saturation doubles; without it the result matches how SDR players show HLG. Verified on iPhone clips.
TONEMAPS = [
    ("zscale", "zscale=t=linear:npl=140,format=gbrpf32le,zscale=pin=709:p=709,tonemap=hable:desat=0,"
               "zscale=t=bt709:m=bt709:r=tv,format=yuv420p"),
    ("libplacebo", "libplacebo=tonemapping=bt.2390:colorspace=bt709:color_primaries=bt709:"
                   "color_trc=bt709:range=tv:format=yuv420p"),
]
TAGS = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
_nvenc = None


def nvenc_works() -> bool:
    global _nvenc
    if _nvenc is None:
        p = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:d=0.2",
                 "-c:v", "h264_nvenc", "-f", "null", "-"], check=False)
        _nvenc = p.returncode == 0
    return _nvenc


def venc_args(use_nvenc: bool):
    if use_nvenc:
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "22", "-b:v", "0", "-g", "30"]
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-g", "30"]


def target_size(w, h, limit):
    s = min(1.0, limit / max(w, h))
    return max(2, int(round(w * s / 2)) * 2), max(2, int(round(h * s / 2)) * 2)


def make_video(row, out_mp4, out_wav, args):
    src = row["path"]
    w, h = target_size(int(row["width"]), int(row["height"]), args.max)
    has_audio = row["has_audio"] == "yes"
    hdr = row["hdr"] != "no" and not args.no_hdr_convert
    vs = next((s for s in ffprobe(src).get("streams", []) if s.get("codec_type") == "video"), {})
    matrix = vs.get("color_space", "unknown")
    # untagged HD footage is BT.709 in practice; ffmpeg would otherwise guess BT.601
    in_matrix = "bt709" if matrix in ("unknown", "", "bt709") else "auto"
    base = f"fps={args.fps},scale={w}:{h}:flags=lanczos"
    chains = []
    if hdr:
        chains += [(name, f"{base},{tm}") for name, tm in TONEMAPS]
        chains.append(("retag-only", f"{base},scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"))
    else:
        chains.append(("sdr", f"{base},scale=in_color_matrix={in_matrix}:out_color_matrix=bt709:out_range=tv,"
                              f"format=yuv420p"))
    tmp = out_mp4.with_name(out_mp4.stem + ".part.mp4")
    errors, encoder = [], ""
    for name, vf in chains:
        for use_nv in ([True, False] if nvenc_works() else [False]):
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", src]
            if not has_audio:
                cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            cmd += ["-map", "0:v:0", "-vf", vf, *venc_args(use_nv), *TAGS, "-pix_fmt", "yuv420p"]
            cmd += ["-map", "0:a:0" if has_audio else "1:a:0", "-c:a", "aac", "-b:a", "160k",
                    "-ar", "48000", "-ac", "2"]
            if not has_audio:
                cmd += ["-shortest"]
            cmd += ["-movflags", "+faststart", str(tmp)]
            if out_wav is not None and has_audio:
                cmd += ["-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(out_wav)]
            p = run(cmd, check=False)
            if p.returncode == 0 and tmp.exists():
                tmp.replace(out_mp4)
                encoder = "nvenc" if use_nv else "x264"
                note = f"HDR->SDR ({name})" if hdr else ""
                if hdr and name == "retag-only":
                    note += " WARNING: no tone-map filter worked, colors may look flat"
                return encoder, note, (w, h)
            errors.append(p.stderr.strip().splitlines()[-1] if p.stderr.strip() else "unknown error")
            tmp.unlink(missing_ok=True)
    raise CutroomError(f"ffmpeg could not encode {src}. Last errors:\n  " + "\n  ".join(errors[-3:]))


def make_wav(src, out_wav):
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", src, "-vn", "-ac", "1",
         "-ar", "16000", "-c:a", "pcm_s16le", str(out_wav)])


def make_photo(src, out_jpg):
    try:
        from PIL import Image, ImageOps
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            pass
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((2560, 2560), Image.LANCZOS)
            im.save(out_jpg, "JPEG", quality=92)
        return "pillow"
    except ImportError:
        pass  # Pillow missing: ffmpeg fallback below
    except Exception as e:
        if Path(src).suffix.lower() in (".heic", ".heif"):
            raise CutroomError(f"cannot read HEIC photo {src} ({e}). Run: pip install pillow-heif")
        raise CutroomError(f"cannot read photo {src}: {e}")
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", src, "-vf",
         "scale='min(2560,iw)':'min(2560,ih)':force_original_aspect_ratio=decrease", "-q:v", "2", str(out_jpg)])
    return "ffmpeg"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project")
    ap.add_argument("ids", nargs="*", help="inventory IDs (default: all videos and photos)")
    ap.add_argument("--max", "--height", dest="max", type=int, default=1920,
                    help="limit for the LONG side in px, aspect and orientation are kept (default 1920)")
    ap.add_argument("--fps", type=float, default=30, help="constant output frame rate (default 30)")
    ap.add_argument("--force", action="store_true", help="rebuild existing proxies")
    ap.add_argument("--no-hdr-convert", action="store_true", help="only retag HDR instead of tone-mapping")
    args = ap.parse_args()
    need("ffmpeg"), need("ffprobe")
    proj = project_dir(args.project)
    inv = inventory(proj)
    if not inv:
        raise CutroomError(f"{proj / 'work' / 'inventory.csv'} is missing or empty. "
                           f"Run: python probe.py {args.project} <media folder>")
    ids = args.ids or list(inv)
    unknown = [i for i in ids if i not in inv]
    if unknown:
        raise CutroomError(f"unknown ID(s) {', '.join(unknown)}. Known IDs: {', '.join(inv)}")
    pdir, adir = proj / "work" / "proxy", proj / "work" / "audio"
    pdir.mkdir(parents=True, exist_ok=True), adir.mkdir(parents=True, exist_ok=True)

    done = skipped = failed = 0
    for i in ids:
        row, t0 = inv[i], time.time()
        try:
            if row["type"] == "video":
                mp4, wav = pdir / f"{i}.mp4", adir / f"{i}.wav"
                need_wav = row["has_audio"] == "yes" and (args.force or not wav.exists())
                if mp4.exists() and not args.force:
                    if need_wav:
                        make_wav(row["path"], wav)
                        print(f"{i}: proxy exists, wav added")
                    else:
                        print(f"{i}: skipped (exists)")
                        skipped += 1
                    continue
                enc, note, (w, h) = make_video(row, mp4, wav if need_wav else None, args)
                print(f"{i}: {row['width']}x{row['height']} -> {w}x{h} {args.fps:g}fps {enc} {note} "
                      f"({time.time() - t0:.1f}s)".replace("  ", " "))
            elif row["type"] == "photo":
                jpg = pdir / f"{i}.jpg"
                if jpg.exists() and not args.force:
                    print(f"{i}: skipped (exists)")
                    skipped += 1
                    continue
                how = make_photo(row["path"], jpg)
                print(f"{i}: photo -> {jpg.name} ({how}, {time.time() - t0:.1f}s)")
            else:
                print(f"{i}: audio file, no proxy needed")
                continue
            done += 1
        except CutroomError as e:
            failed += 1
            print(f"{i}: FAILED {e}", file=sys.stderr)
    print(f"\n{done} built, {skipped} skipped, {failed} failed -> {pdir}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except CutroomError as e:
        die(str(e))
