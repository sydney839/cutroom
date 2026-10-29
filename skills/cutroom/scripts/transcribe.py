#!/usr/bin/env python3
"""Transcribe speech with faster-whisper (word timestamps) and build work/takes.md.

Usage:
    python transcribe.py <project> [IDs...] [--model small] [--language de|en|auto]
                         [--device auto|cuda|cpu] [--terms "Claude, Codex"] [--force]

Reads work/audio/<ID>.wav (made by proxy.py; extracted here if missing), writes
work/transcripts/<ID>.json, then rebuilds work/takes.md from ALL transcripts: one heading per
source, then phrases as `[start-end] text`. Phrases that repeat an earlier phrase are marked
`<- retake of [t]` (usually the last take wins); words Whisper is unsure about look like `?word?`.

--terms: names or jargon to help recognition. Passed as `hotwords` (faster-whisper >= 1.0).
There is deliberately NO default initial prompt: it makes Whisper hallucinate the prompt text
over music and crowd noise. If your faster-whisper has no `hotwords`, --terms is sent as an
initial prompt instead, and only then.

--device auto uses the GPU when CUDA and its libraries (cuBLAS, cuDNN) are found, otherwise CPU.
"""
import argparse
import difflib
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CutroomError, die, inventory, need, project_dir, read_json, run, write_json  # noqa: E402


def import_whisper():
    try:
        from faster_whisper import WhisperModel
        return WhisperModel
    except ImportError:
        raise CutroomError("faster-whisper is not installed. Run: pip install faster-whisper")


def add_nvidia_dll_dirs():
    """pip packages nvidia-cublas-cu12 / nvidia-cudnn-cu12 put DLLs outside PATH; make them findable."""
    for base in map(Path, sys.path):
        for d in (base / "nvidia").glob("*/bin" if os.name == "nt" else "*/lib"):
            if os.name == "nt":
                os.add_dll_directory(str(d))
                os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
            else:
                os.environ["LD_LIBRARY_PATH"] = str(d) + os.pathsep + os.environ.get("LD_LIBRARY_PATH", "")


def cuda_ready():
    """(compute_type or None, reason). Checks the DLLs up front because a missing cuBLAS can kill the process."""
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() < 1:
            return None, "no CUDA device"
        types = ctranslate2.get_supported_compute_types("cuda")
    except Exception as e:
        return None, f"CUDA not usable ({e})"
    import ctypes
    add_nvidia_dll_dirs()
    lib = "cublas64_12.dll" if os.name == "nt" else "libcublas.so.12"
    try:
        ctypes.CDLL(lib)
    except OSError:
        return None, (f"{lib} not found. For GPU: pip install nvidia-cublas-cu12 "
                      f"nvidia-cudnn-cu12 (or install the CUDA 12 toolkit)")
    for t in ("int8_float16", "int8_float32", "int8", "float32"):
        if t in types:
            return t, ""
    return None, "no usable compute type"


def load_model(WhisperModel, name, device):
    if device in ("auto", "cuda"):
        ctype, why = cuda_ready()
        if ctype:
            try:
                return WhisperModel(name, device="cuda", compute_type=ctype), f"cuda/{ctype}"
            except Exception as e:
                why = str(e).splitlines()[0]
        print(f"GPU not used: {why}. Falling back to CPU (int8).")
    return WhisperModel(name, device="cpu", compute_type="int8"), "cpu/int8"


def read_wav(wav):
    """16 kHz mono PCM as float32. Passing samples instead of a path keeps PyAV out of the way
    (some faster-whisper / PyAV version pairs fail to open files)."""
    import wave
    import numpy as np
    with wave.open(str(wav)) as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
            raise CutroomError(f"{wav} is not 16 kHz mono 16-bit. Delete it and run again to re-extract it.")
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return data.astype(np.float32) / 32768.0


def transcribe_file(model, wav, args, hotwords_ok):
    kw = dict(word_timestamps=True, vad_filter=True, condition_on_previous_text=False,
              language=None if args.language == "auto" else args.language)
    if args.terms:
        kw["hotwords" if hotwords_ok else "initial_prompt"] = args.terms
    segments, info = model.transcribe(read_wav(wav), **kw)
    words = []
    for seg in segments:
        for w in seg.words or []:
            words.append({"text": w.word.strip(), "start": round(w.start, 2), "end": round(w.end, 2),
                          "prob": round(w.probability, 2)})
    return words, info


def phrases_of(words, gap=0.5):
    """Split words at pauses >= gap seconds or after sentence-ending punctuation."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if nxt is None or nxt["start"] - w["end"] >= gap or re.search(r"[.!?…]$", w["text"]):
            out.append(cur)
            cur = []
    return out


def norm(s):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", s.lower())).strip()


def build_takes(proj, inv):
    tdir = proj / "work" / "transcripts"
    lines = ["# Takes", "",
             "Phrases as `[start-end] text` (seconds in the source). `<- retake of [t]` = same line said again "
             "(usually the last take is the keeper). `?word?` = low-confidence word, check before using it as a caption.",
             ""]
    history = []  # (source id, normalized text, "start-end") of the previous source plus the current one
    prev_id, n_phr, n_retake = None, 0, 0
    for f in sorted(tdir.glob("*.json"), key=lambda p: (re.sub(r"\d", "", p.stem), int(re.sub(r"\D", "", p.stem) or 0))):
        sid = f.stem
        data = read_json(f, {})
        words = data.get("words", [])
        row = inv.get(sid, {})
        dur = float(row["duration_s"]) if row.get("duration_s") else (words[-1]["end"] if words else 0)
        lines.append(f"## {sid} ({dur:.1f} s, {Path(row['path']).name if row.get('path') else '?'})")
        if not words:
            lines += ["(no speech detected)", ""]
            continue
        history = [h for h in history if h[0] == prev_id] if prev_id else []
        for ph in phrases_of(words):
            text = " ".join(f"?{w['text']}?" if w.get("prob", 1) < 0.4 else w["text"] for w in ph)
            span = f"{ph[0]['start']:.2f}-{ph[-1]['end']:.2f}"
            key = norm(" ".join(w["text"] for w in ph))
            mark = ""
            if len(key.split()) >= 3:
                best = None
                for hid, hkey, hspan in reversed(history):
                    r = difflib.SequenceMatcher(None, key, hkey).ratio()
                    if r >= 0.75:
                        best = f"[{hspan}]" if hid == sid else f"[{hid} {hspan}]"
                        break
                if best:
                    mark, n_retake = f"  <- retake of {best}", n_retake + 1
            history.append((sid, key, span))
            lines.append(f"[{span}] {text}{mark}")
            n_phr += 1
        lines.append("")
        prev_id = sid
    (proj / "work" / "takes.md").write_text("\n".join(lines), encoding="utf-8")
    return n_phr, n_retake


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project")
    ap.add_argument("ids", nargs="*", help="inventory IDs (default: all videos and audio files)")
    ap.add_argument("--model", default="small", help="tiny|base|small|medium|large-v3 (default small)")
    ap.add_argument("--language", default="auto", help="de, en, ... or auto (default auto)")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    ap.add_argument("--terms", default="", help='names/jargon, e.g. "Claude, Codex" (see above)')
    ap.add_argument("--force", action="store_true", help="redo existing transcripts")
    args = ap.parse_args()
    need("ffmpeg")
    proj = project_dir(args.project)
    inv = inventory(proj)
    if not inv:
        raise CutroomError(f"{proj / 'work' / 'inventory.csv'} is missing or empty. Run probe.py first.")
    ids = args.ids or [i for i, r in inv.items() if r["type"] in ("video", "audio")]
    unknown = [i for i in ids if i not in inv]
    if unknown:
        raise CutroomError(f"unknown ID(s) {', '.join(unknown)}. Known IDs: {', '.join(inv)}")
    photos = [i for i in ids if inv[i]["type"] == "photo"]
    if photos:
        raise CutroomError(f"{', '.join(photos)} {'is a photo' if len(photos) == 1 else 'are photos'}: nothing to "
                           f"transcribe. Pass video or audio IDs.")
    tdir, adir = proj / "work" / "transcripts", proj / "work" / "audio"
    tdir.mkdir(parents=True, exist_ok=True), adir.mkdir(parents=True, exist_ok=True)
    todo = [i for i in ids if args.force or not (tdir / f"{i}.json").exists()]

    if todo:
        WhisperModel = import_whisper()
        import inspect
        hotwords_ok = "hotwords" in inspect.signature(WhisperModel.transcribe).parameters
        if args.terms and not hotwords_ok:
            print("note: this faster-whisper has no 'hotwords'; --terms is sent as initial_prompt.")
        t0 = time.time()
        print(f"loading model '{args.model}' (first use downloads it) ...")
        model, dev = load_model(WhisperModel, args.model, args.device)
        print(f"model ready on {dev} ({time.time() - t0:.1f}s)")
        for i in todo:
            if inv[i]["has_audio"] == "no":
                write_json(tdir / f"{i}.json", {"language": None, "model": args.model, "words": []})
                print(f"{i}: no audio track")
                continue
            wav = adir / f"{i}.wav"
            if not wav.exists():
                run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", inv[i]["path"],
                     "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)])
            t1 = time.time()
            try:
                words, info = transcribe_file(model, wav, args, hotwords_ok)
            except Exception as e:
                if dev.startswith("cpu"):
                    raise CutroomError(f"transcription of {wav} failed: {e}")
                print(f"{i}: GPU run failed ({str(e).splitlines()[0]}); switching to CPU")
                model, dev = load_model(WhisperModel, args.model, "cpu")
                words, info = transcribe_file(model, wav, args, hotwords_ok)
            write_json(tdir / f"{i}.json", {"language": info.language, "model": args.model, "device": dev,
                                            "words": words})
            print(f"{i}: {len(words)} words, {info.language} ({info.language_probability:.2f}), "
                  f"{dev}, {time.time() - t1:.1f}s")
    else:
        print("all requested transcripts exist (use --force to redo)")

    n_phr, n_retake = build_takes(proj, inv)
    print(f"\nwork/takes.md: {n_phr} phrases, {n_retake} marked as retakes -> {proj / 'work' / 'takes.md'}")


if __name__ == "__main__":
    try:
        main()
    except CutroomError as e:
        die(str(e))
