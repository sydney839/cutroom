#!/usr/bin/env python3
"""Face boxes per segment, so you can decide where captions and overlays go and check that they stay off faces.

Usage:
    python faces.py <project>                     every segment of the last render (a --draft is enough)
    python faces.py <project> --video <ID|path>   any video (a source clip or proxy), one window every --every s
    options: [--every 2] [--samples 5]

After a render it reads work/render/layout.json and the clean picture work/render/base.mp4 (cut, zoom
and crop applied, no text yet), so the boxes are in output pixels. For every segment it prints the face
box and the free bands above the head and below the chin inside the platform safe zone. Then it lists
every caption chunk or overlay of that render that covers a face: move those in edit.json and render again.
It decides nothing for you; open the review stills to confirm.

Boxes are fractions of the frame (x0-x1, y0-y1 from the top left), padded for hair and chin.
Results also go to work/faces.json. Needs opencv-python-headless; the YuNet face model (230 KB, MIT) is
downloaded once to ~/.cache/cutroom/ (or set CUTROOM_FACE_MODEL to a local copy). Detection misses faces
turned far away, very small or very dark faces: a segment reported without a face may still show one,
check its still.
"""
import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CutroomError, die, fmt_tc, project_dir, read_json, resolve_source, write_json  # noqa: E402

# YuNet face detector (MIT, OpenCV Zoo), downloaded once (230 KB). Works with OpenCV 4.8+ and 5.x.
# OpenCV 5 dropped the old Haar cascades; they are only the fallback for older OpenCV builds.
YUNET_URL = ("https://media.githubusercontent.com/media/opencv/opencv_zoo/48b10fa505aeb51fbd127fc60a5c660040e823b7/"
             "models/face_detection_yunet/face_detection_yunet_2023mar.onnx")
YUNET_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"


def load_cv2():
    os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
    try:
        import cv2
        return cv2
    except ImportError:
        raise CutroomError("opencv is not installed. Run: pip install opencv-python-headless")


def yunet_model():
    """Path of the YuNet model: $CUTROOM_FACE_MODEL, or ~/.cache/cutroom/ (downloaded on first use)."""
    if os.environ.get("CUTROOM_FACE_MODEL"):
        return Path(os.environ["CUTROOM_FACE_MODEL"])
    f = Path.home() / ".cache" / "cutroom" / "face_detection_yunet_2023mar.onnx"
    if f.exists() and hashlib.sha256(f.read_bytes()).hexdigest() == YUNET_SHA256:
        return f
    f.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading the face model once (230 KB) -> {f}")
    try:
        data = urllib.request.urlopen(YUNET_URL, timeout=60).read()
    except Exception as e:
        raise OSError(f"download failed ({e})")
    if hashlib.sha256(data).hexdigest() != YUNET_SHA256:
        raise OSError("downloaded face model has the wrong checksum")
    f.write_bytes(data)
    return f


class FaceFinder:
    """Largest face per frame: YuNet when possible, Haar cascades on older OpenCV without network."""

    def __init__(self, cv2):
        self.cv2, self.yunet, self.casc, why = cv2, None, None, ""
        if hasattr(cv2, "FaceDetectorYN"):
            try:
                self.yunet = cv2.FaceDetectorYN.create(str(yunet_model()), "", (320, 320), 0.7)
                self.name = "YuNet"
                return
            except (OSError, cv2.error) as e:
                why = str(e).splitlines()[0]
        if hasattr(cv2, "CascadeClassifier") and hasattr(cv2, "data"):
            self.casc = [cv2.CascadeClassifier(cv2.data.haarcascades + n)
                         for n in ("haarcascade_frontalface_alt2.xml", "haarcascade_frontalface_default.xml")]
            self.name = "Haar cascade" + (f" (YuNet unavailable: {why})" if why else "")
            return
        raise CutroomError(f"no face detector available ({why or 'OpenCV too old'}). Check the network for the "
                           f"one-time model download, or download {YUNET_URL} yourself and set CUTROOM_FACE_MODEL "
                           f"to its path.")

    def find(self, frame):
        """(x0, y0, x1, y1) of the largest face in pixels, or None."""
        cv2 = self.cv2
        h, w = frame.shape[:2]
        f = 640 / max(w, h)
        small = cv2.resize(frame, None, fx=f, fy=f)
        if self.yunet is not None:
            self.yunet.setInputSize((small.shape[1], small.shape[0]))
            _, r = self.yunet.detect(small)
            boxes = [] if r is None else [tuple(b[:4]) for b in r]
        else:
            g = cv2.equalizeHist(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY))
            boxes = []
            for cc in self.casc:
                boxes = [tuple(b) for b in cc.detectMultiScale(g, 1.1, 5, minSize=(24, 24))]
                if boxes:
                    break
        if not boxes:
            return None
        x, y, bw, bh = max(boxes, key=lambda b: b[2] * b[3])
        return float(x / f), float(y / f), float((x + bw) / f), float((y + bh) / f)


def detect(cv2, finder, cap, fps, t0, t1, samples):
    """Union face box (x0, y0, x1, y1) in pixels over `samples` frames between t0 and t1, hits, tries."""
    boxes, tried = [], 0
    for i in range(samples):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int((t0 + (i + 0.5) / samples * (t1 - t0)) * fps))
        ok, fr = cap.read()
        if not ok:
            continue
        tried += 1
        b = finder.find(fr)
        if b:
            boxes.append(b)
    if not boxes or len(boxes) < max(1, 0.4 * tried):
        return None, len(boxes), tried
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    hh = y1 - y0
    return (x0, y0 - 0.25 * hh, x1, y1 + 0.2 * hh), len(boxes), tried   # hair above, chin below


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project")
    ap.add_argument("--video", help="inventory ID or path; default: the last render (work/render/base.mp4)")
    ap.add_argument("--every", type=float, default=2.0, help="window length in seconds for --video (default 2)")
    ap.add_argument("--samples", type=int, default=5, help="frames checked per segment or window (default 5)")
    args = ap.parse_args()
    cv2 = load_cv2()
    finder = FaceFinder(cv2)
    proj = project_dir(args.project)
    layout = None
    if args.video:
        src = resolve_source(proj, args.video)
    else:
        layout = read_json(proj / "work" / "render" / "layout.json")
        src = proj / "work" / "render" / "base.mp4"
        if layout is None or not src.exists():
            raise CutroomError(f"no render found in {proj / 'work' / 'render'}. Run render.py --draft first, "
                               f"or pass --video <ID|path> to scan a source clip.")
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise CutroomError(f"OpenCV cannot open {src}.")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    dur = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps
    if layout and ((W, H) != (layout["w"], layout["h"]) or abs(dur - layout["segments"][-1]["t1"]) > 0.5):
        raise CutroomError(f"{src} does not match work/render/layout.json (the last render did not finish). "
                           f"Run render.py --draft again.")
    if layout:
        windows = [(s["n"], s["t0"], s["t1"], s["src"]) for s in layout["segments"]]
        top, bottom = layout["safe_zone"]
    else:
        n = max(1, int(dur / args.every + 0.999))
        windows = [(k + 1, k * args.every, min(dur, (k + 1) * args.every), src.name) for k in range(n)]
        top, bottom = (int(H * 0.14), int(H * 0.78)) if H > W else (int(H * 0.06), int(H * 0.93))

    print(f"{src.name}: {W}x{H}, safe zone y {top / H:.2f}-{bottom / H:.2f}, detector {finder.name}")
    print(f"{'seg':>4}  {'timeline':<17} {'face x':<11} {'face y':<11} free above   free below   source")
    results = []
    for n, t0, t1, name in windows:
        box, hits, tried = detect(cv2, finder, cap, fps, t0, t1, args.samples)
        row = {"n": n, "t0": t0, "t1": t1, "src": name, "face": None, "hits": f"{hits}/{tried}"}
        if box:
            x0, y0, x1, y1 = max(0, box[0]), max(0, box[1]), min(W, box[2]), min(H, box[3])
            row["face"] = [round(x0 / W, 3), round(y0 / H, 3), round(x1 / W, 3), round(y1 / H, 3)]
            above = (top, min(bottom, y0)) if y0 > top else None
            below = (max(top, y1), bottom) if y1 < bottom else None
            row["free_above"] = [round(above[0] / H, 3), round(above[1] / H, 3)] if above else None
            row["free_below"] = [round(below[0] / H, 3), round(below[1] / H, 3)] if below else None
            fa = f"{above[0] / H:.2f}-{above[1] / H:.2f}" if above else "none"
            fb = f"{below[0] / H:.2f}-{below[1] / H:.2f}" if below else "none"
            print(f"{n:>4}  {fmt_tc(t0)}-{fmt_tc(t1)}  {x0 / W:.2f}-{x1 / W:.2f}  {y0 / H:.2f}-{y1 / H:.2f}  "
                  f"{fa:<12} {fb:<12} {name}")
        else:
            print(f"{n:>4}  {fmt_tc(t0)}-{fmt_tc(t1)}  no face found ({hits}/{tried} frames)        {name}")
        results.append(row)
    cap.release()

    hits = []
    if layout:
        for b in layout.get("blocks", []):
            bx0, by0, bx1, by1 = b["box"]
            for r in results:
                if r["face"] and r["t0"] < b["t1"] and b["t0"] < r["t1"]:
                    fx0, fy0, fx1, fy1 = r["face"][0] * W, r["face"][1] * H, r["face"][2] * W, r["face"][3] * H
                    if bx0 < fx1 and fx0 < bx1 and by0 < fy1 and fy0 < by1:
                        hits.append((b, r))
                        break
        print()
        if hits:
            print(f"{len(hits)} text/image block(s) cover a face:")
            for b, r in hits:
                print(f"  COVERS FACE  {b['what']} at {fmt_tc(max(b['t0'], r['t0']))}, block y "
                      f"{b['box'][1] / H:.2f}-{b['box'][3] / H:.2f}, face y {r['face'][1]:.2f}-{r['face'][3]:.2f} "
                      f"(segment {r['n']})")
            print("Move them into a free band (caption_pos per clip, \"pos\" per overlay), shorten them, or change "
                  "zoom/focus so there is room. Then render again and run this again.")
        else:
            print("No caption or overlay covers a detected face. Still look at the review stills.")
    out = proj / "work" / "faces.json"
    write_json(out, {"video": str(src), "w": W, "h": H, "safe_zone": [top, bottom], "segments": results,
                     "covers_face": [{"what": b["what"], "t0": b["t0"], "segment": r["n"]} for b, r in hits]})
    print(f"-> {out}")


if __name__ == "__main__":
    try:
        main()
    except CutroomError as e:
        die(str(e))
