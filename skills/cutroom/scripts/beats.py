#!/usr/bin/env python3
"""Beat, downbeat and energy-section analysis of a music track (numpy + ffmpeg only).

Usage:
    python beats.py <audio-or-video-file> [--project <dir>] [--out name]

Writes <project>/work/beats/<name>.json (or ./<name>.json without --project):
    {"file", "duration", "bpm", "beats": [s...], "downbeats": [s...],
     "energy": [[t, rms]...] (1 per second), "sections": [s...]}
`sections` are moments where the loudness jumps up by 3 dB or more (a drop or a new part),
snapped to the nearest beat. Cut on `beats`; put big transitions on `downbeats` or `sections`.
Tempo is searched in 60-180 BPM with a preference for 90-140. Half/double-tempo mix-ups
are the usual failure: check the printed BPM against what you hear.
"""
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CutroomError, die, need, project_dir, write_json  # noqa: E402

try:
    import numpy as np
except ImportError:
    np = None

SR, NFFT, HOP = 22050, 1024, 256


def decode(path):
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(path), "-vn",
                        "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True)
    if p.returncode != 0 or not p.stdout:
        raise CutroomError(f"ffmpeg could not decode audio from {path}: "
                           f"{p.stderr.decode(errors='replace').strip()[-300:] or 'no audio stream'}")
    return np.frombuffer(p.stdout, dtype=np.float32)


def mel_bank(n_mels=40, fmin=30.0, fmax=8000.0):
    def hz2mel(f): return 2595 * np.log10(1 + f / 700)
    def mel2hz(m): return 700 * (10 ** (m / 2595) - 1)
    pts = mel2hz(np.linspace(hz2mel(fmin), hz2mel(fmax), n_mels + 2))
    freqs = np.linspace(0, SR / 2, NFFT // 2 + 1)
    bank = np.zeros((n_mels, len(freqs)), dtype=np.float32)
    for i in range(n_mels):
        lo, c, hi = pts[i], pts[i + 1], pts[i + 2]
        bank[i] = np.clip(np.minimum((freqs - lo) / (c - lo), (hi - freqs) / (hi - c)), 0, None)
    return bank, pts[1:-1]


def onset_envelopes(y):
    """(all-band onset strength, bass-band onset strength), one value per hop."""
    y = np.pad(y, NFFT // 2, mode="reflect")
    n = 1 + (len(y) - NFFT) // HOP
    win = np.hanning(NFFT).astype(np.float32)
    bank, centers = mel_bank()
    mels = []
    for s in range(0, n, 2000):  # chunked to keep memory low on long tracks
        idx = np.arange(s, min(n, s + 2000))[:, None] * HOP + np.arange(NFFT)[None, :]
        spec = np.abs(np.fft.rfft(y[idx] * win, axis=1)) ** 2
        mels.append(spec @ bank.T)
    mel = np.concatenate(mels)
    db = 10 * np.log10(mel + 1e-10)
    db = np.maximum(db, db.max() - 80)
    flux = np.maximum(0, np.diff(db, axis=0, prepend=db[:1]))
    bass = flux[:, centers < 200].mean(axis=1) if (centers < 200).any() else flux.mean(axis=1)
    return flux.mean(axis=1), bass


def normalize(env):
    k = int(0.5 / (HOP / SR))
    local = np.convolve(env, np.ones(k) / k, mode="same")
    env = np.maximum(env - local, 0)
    return env / (env.std() + 1e-9)


def estimate_tempo(env):
    fps = SR / HOP
    x = env - env.mean()
    ac = np.correlate(x, x, mode="full")[len(x) - 1:]
    ac /= ac[0] + 1e-9
    lags = np.arange(int(fps * 60 / 180), int(fps * 60 / 60) + 1)
    bpms = 60 * fps / lags
    score = np.array([ac[l] + 0.5 * (ac[2 * l] if 2 * l < len(ac) else 0) + 0.25 * (ac[4 * l] if 4 * l < len(ac) else 0)
                      for l in lags])
    score *= np.exp(-0.5 * (np.log2(bpms / 115) / 0.6) ** 2)  # prior: 90-140 BPM
    i = int(np.argmax(score))
    lag = lags[i]
    if 0 < i < len(lags) - 1:  # parabolic interpolation: frame resolution alone is ~2 % at 120 BPM
        a, b, c = score[i - 1], score[i], score[i + 1]
        d = a - 2 * b + c
        lag = lag + (0.5 * (a - c) / d if d else 0)
    return 60 * fps / lag, lag


def track_beats(env, period):
    """Ellis (2007) dynamic programming: maximise onset strength with a steady spacing."""
    n, T = len(env), period
    score, prev = env.astype(np.float64).copy(), -np.ones(n, dtype=int)
    for t in range(1, n):
        lo, hi = max(0, int(t - 2 * T)), int(t - T / 2)
        if hi <= lo:
            continue
        p = np.arange(lo, hi)
        cand = score[lo:hi] - 100 * np.log((t - p) / T) ** 2
        j = int(np.argmax(cand))
        score[t] = env[t] + cand[j]
        prev[t] = p[j]
    start = max(0, n - int(T))
    t = start + int(np.argmax(score[start:]))
    beats = [t]
    while prev[t] >= 0:
        t = prev[t]
        beats.append(t)
    return np.array(beats[::-1])


def refine(beats, env):
    """Sub-frame beat times via parabolic interpolation of the onset peak."""
    out = []
    for b in beats:
        if 0 < b < len(env) - 1:
            a, c = env[b - 1], env[b + 1]
            d = a - 2 * env[b] + c
            b = b + (0.5 * (a - c) / d if d else 0)
        out.append(b * HOP / SR)
    return np.array(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("audio", help="music file (any audio/video file ffmpeg can read)")
    ap.add_argument("--project", help="project folder; output goes to <project>/work/beats/")
    ap.add_argument("--out", help="output name without extension (default: audio file name)")
    args = ap.parse_args()
    if np is None:
        raise CutroomError("numpy is not installed. Run: pip install numpy")
    need("ffmpeg")
    src = Path(args.audio).expanduser().resolve()
    if not src.exists():
        raise CutroomError(f"{src} does not exist. Pass the path of a music file.")
    y = decode(src)
    dur = len(y) / SR
    if dur < 4:
        raise CutroomError(f"{src.name} is only {dur:.1f} s long; beat tracking needs at least 4 s.")

    flux, bass = onset_envelopes(y)
    env, benv = normalize(flux), normalize(bass)
    env = env + 0.7 * benv  # kick drums mark the beat; hi-hats alone would pull it onto the offbeat
    bpm0, lag = estimate_tempo(env)
    idx = track_beats(env, lag)
    keep = env[idx] >= 0.25 * np.median(env[idx])  # drop spurious beats in silence at the start/end
    first, last = np.argmax(keep), len(keep) - np.argmax(keep[::-1])
    idx = idx[first:last]
    times = refine(idx, env)
    if len(times) >= 8:  # regression over all beats is more precise than the autocorrelation bin
        slope = np.polyfit(np.arange(len(times)), times, 1)[0]
        bpm = 60 / slope if abs(60 / slope - bpm0) / bpm0 < 0.05 else bpm0
    else:
        bpm = bpm0

    # downbeats: the phase (0-3) whose beats carry the most bass onset energy
    phase = int(np.argmax([benv[idx[p::4]].sum() for p in range(4)])) if len(idx) >= 8 else 0
    downbeats = times[phase::4]

    # energy: RMS in 0.5 s windows, reported once per second; sections = jumps of >= 3 dB
    w = int(0.5 * SR)
    rms = np.sqrt((y[: len(y) // w * w].reshape(-1, w) ** 2).mean(axis=1))
    db = 20 * np.log10(rms + 1e-6)
    sections = []
    for i in range(4, len(db) - 4):
        jump = db[i:i + 4].mean() - db[i - 4:i].mean()
        if jump >= 3 and jump == max(db[j:j + 4].mean() - db[j - 4:j].mean() for j in range(max(4, i - 4), min(len(db) - 4, i + 5))):
            t = i * 0.5
            t = float(times[np.argmin(np.abs(times - t))]) if len(times) and np.abs(times - t).min() < 1 else t
            if not sections or t - sections[-1] >= 6:
                sections.append(round(t, 3))
    one = rms[: len(rms) // 2 * 2].reshape(-1, 2).mean(axis=1)
    energy = [[float(t), round(float(v), 4)] for t, v in enumerate(one)]

    name = args.out or src.stem
    out = (project_dir(args.project) / "work" / "beats" if args.project else Path.cwd()) / f"{name}.json"
    write_json(out, {"file": str(src), "duration": round(dur, 2), "bpm": round(float(bpm), 2),
                     "beats": [round(float(t), 3) for t in times],
                     "downbeats": [round(float(t), 3) for t in downbeats],
                     "energy": energy, "sections": sections})
    print(f"{src.name}: {dur:.1f} s, {bpm:.1f} BPM, {len(times)} beats, {len(downbeats)} downbeats")
    print("first beats (s): " + ", ".join(f"{t:.2f}" for t in times[:8]))
    print("first downbeats (s): " + ", ".join(f"{t:.2f}" for t in downbeats[:4]))
    print("sections (s): " + (", ".join(f"{t:.1f}" for t in sections) or "none found"))
    print(f"-> {out}")


if __name__ == "__main__":
    try:
        main()
    except CutroomError as e:
        die(str(e))
