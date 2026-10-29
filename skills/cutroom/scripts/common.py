"""Shared helpers for the cutroom scripts.

Every script works on one *project folder*:

    <project>/
      brief.md            goal, format, audience, length, style (written by the agent)
      plan.md             script, story outline or shot list (written by the agent)
      edit.json           the edit decision list that render.py turns into a video
      assets/             images, logos, music and sound effects for this project
      work/               everything generated (safe to delete and rebuild)
        inventory.csv     one row per source file, with its ID (probe.py)
        proxy/<ID>.mp4    edit-friendly copies: SDR BT.709, constant fps (proxy.py)
        proxy/<ID>.jpg    photos, EXIF-rotated, max 2560 px (proxy.py)
        audio/<ID>.wav    16 kHz mono for transcription (proxy.py)
        transcripts/<ID>.json   {"words": [{"text", "start", "end"}]} (transcribe.py)
        takes.md          all speech as timed phrases, the agent reads this (transcribe.py)
        sheets/           contact sheets as JPG, the agent looks at these (sheet.py)
        beats/<name>.json beat grid of a music track (beats.py)
        render/           cached segments, stems, base.mp4 and layout.json of the last render (render.py)
        faces.json        face boxes and free bands per segment (faces.py)
        review/           stills of the last render (render.py, qa.py)
      exports/            final video, captions.srt, stems/, qa.md

Originals are never modified, moved or deleted.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mts", ".m2ts", ".3gp"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff", ".dng"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}

PRESETS = {
    "reel": (1080, 1920),        # Reels, TikTok, Shorts
    "vertical": (1080, 1920),
    "portrait": (1080, 1350),    # 4:5 feed
    "square": (1080, 1080),
    "landscape": (1920, 1080),   # YouTube, vlogs
    "youtube": (1920, 1080),
    "4k": (3840, 2160),
}

if hasattr(sys.stdout, "reconfigure"):
    # Windows consoles default to cp1252; filenames and transcripts are not ASCII.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


class CutroomError(Exception):
    """An error with a message that says what happened, where, and what to do."""


def die(msg: str, code: int = 1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def need(tool: str):
    if not shutil.which(tool):
        die(f"'{tool}' was not found on PATH. Install FFmpeg (https://ffmpeg.org/download.html) "
            f"and make sure '{tool}' runs in a terminal.")


def run(cmd, quiet=True, check=True, cwd=None):
    """Run a command list. On failure, show the tail of stderr so the agent can fix the call."""
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=cwd)
    if check and p.returncode != 0:
        tail = "\n".join(p.stderr.strip().splitlines()[-25:])
        raise CutroomError(f"command failed ({p.returncode}): {' '.join(map(str, cmd[:6]))} ...\n{tail}")
    if not quiet and p.stdout:
        print(p.stdout)
    return p


def ffprobe(path) -> dict:
    p = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    return json.loads(p.stdout or "{}")


def project_dir(arg: str) -> Path:
    p = Path(arg).expanduser().resolve()
    if p.is_file() and p.name == "edit.json":
        p = p.parent
    p.mkdir(parents=True, exist_ok=True)
    for sub in ("work", "assets", "exports"):
        (p / sub).mkdir(exist_ok=True)
    return p


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise CutroomError(f"{path} is not valid JSON (line {e.lineno}, column {e.colno}): {e.msg}")


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def inventory(project: Path) -> dict:
    """ID -> row dict from work/inventory.csv (empty if probe.py has not run)."""
    import csv
    f = project / "work" / "inventory.csv"
    if not f.exists():
        return {}
    with f.open(encoding="utf-8", newline="") as fh:
        return {r["id"]: r for r in csv.DictReader(fh)}


def resolve_source(project: Path, ref: str) -> Path:
    """A source reference in edit.json: an inventory ID (C003), a project-relative path or an absolute path.
    IDs prefer the proxy (work/proxy/<ID>.mp4 or .jpg) and fall back to the original."""
    for ext in (".mp4", ".jpg"):
        p = project / "work" / "proxy" / f"{ref}{ext}"
        if p.exists():
            return p
    inv = inventory(project)
    if ref in inv:
        return Path(inv[ref]["path"])
    p = Path(ref)
    if not p.is_absolute():
        p = project / p
    if p.exists():
        return p
    raise CutroomError(f"source '{ref}' not found. Use an ID from work/inventory.csv, "
                       f"a path relative to {project}, or an absolute path.")


def fmt_tc(t: float) -> str:
    t = max(0.0, t)
    m, s = divmod(t, 60)
    return f"{int(m):02d}:{s:05.2f}"
