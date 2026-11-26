"""Watch a video link (TikTok, YouTube, Instagram, X, Facebook ... anything yt-dlp knows).
Downloads a small copy, takes pictures at even spacing AND at every scene change (each with its time), has the local
vision model describe them in batches (what happens + the text on screen), turns the speech into timed text with
faster-whisper (or uses subtitles), and lines the two up on one timeline. Honest about what it could not see or hear."""
import base64
import html
import glob
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from no_window import run_capture  # noqa: E402

MAX_SECONDS = 15 * 60       # longer videos: only the first 15 minutes are looked at
MAX_MB = 120
BATCH = 8                   # pictures per vision call
MIN_GAP = 1.0               # seconds: two pictures closer than this are the same moment


# ---------- pure helpers (tested) ----------

def fmt_time(t):
    """65.4 -> '1:05'"""
    t = max(0, int(round(float(t))))
    return "%d:%02d" % (t // 60, t % 60)


def frame_budget(duration):
    """How many pictures a video of this length gets."""
    try:
        d = float(duration)
    except (TypeError, ValueError):
        d = 0.0
    if d <= 0:
        return 8
    if d <= 30:
        return 10
    if d <= 120:
        return 14
    if d <= 480:
        return 18
    return 24


def pick_times(duration, n=8):
    """n moments (seconds) spread evenly through a video, never at the very start or end."""
    try:
        d = float(duration)
    except (TypeError, ValueError):
        d = 0.0
    if d <= 0:
        return [0.5]
    d = min(d, MAX_SECONDS)
    n = max(1, int(n))
    return [round(d * (i + 0.5) / n, 2) for i in range(n)]


def parse_scene_times(text):
    """Seconds of scene changes from ffmpeg's showinfo output (lines with 'pts_time:12.34')."""
    out = []
    for m in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", text or ""):
        out.append(float(m.group(1)))
    return out


def choose_times(duration, scenes, budget=None):
    """Pictures to take: evenly spaced ones plus one just after every scene change, merged, no two within MIN_GAP,
    at most `budget` (default by length). If there are too many, scene changes are thinned evenly."""
    budget = budget or frame_budget(duration)
    try:
        d = float(duration)
    except (TypeError, ValueError):
        d = 0.0
    limit = min(d, MAX_SECONDS) if d > 0 else None
    cut = [round(s + 0.4, 2) for s in sorted(set(scenes or [])) if limit is None or s + 0.4 < limit]
    spaced = []
    for c in cut:                           # two cuts within MIN_GAP are one moment
        if not spaced or c - spaced[-1] >= MIN_GAP:
            spaced.append(c)
    cut = spaced
    keep_even = max(2, budget // 2)         # always at least half the budget is evenly spaced
    room = max(0, budget - keep_even)
    if len(cut) > room:                     # too many cuts: thin them evenly
        step = len(cut) / room if room else 0
        cut = [cut[int(i * step)] for i in range(room)] if room else []
    even = pick_times(duration, max(keep_even, budget - len(cut)))   # no/few cuts: even pictures use the rest
    merged = list(cut)                      # scene changes first: they are the most informative moments
    for t in even:                          # even spacing fills in, unless a scene picture is already that close
        if all(abs(t - m) >= MIN_GAP for m in merged):
            merged.append(t)
    merged.sort()
    return merged[:budget]


def speech_near(segments, t, window=4.0):
    """Words spoken within `window` seconds of time t. segments = [(start, end, text), ...]"""
    said = [txt for (a, b, txt) in segments if a - window <= t <= b + window and txt]
    return " ".join(said).strip()


def vtt_segments(raw):
    """[(start, end, text)] from a .vtt subtitle file; repeated lines (auto-captions) are merged away."""
    segs, last = [], ""
    cur = None
    for line in (raw or "").splitlines():
        m = re.match(r"\s*(?:(\d+):)?(\d+):(\d+)[.,](\d+)\s*-->\s*(?:(\d+):)?(\d+):(\d+)[.,](\d+)", line)
        if m:
            g = [int(x) if x else 0 for x in m.groups()]
            a = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0
            b = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000.0
            cur = (a, b)
            continue
        line = html.unescape(re.sub(r"<[^>]+>", "", line)).replace(" ", " ").strip()
        if not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or line.isdigit() or cur is None:
            continue
        if line != last:
            segs.append((cur[0], cur[1], line))
            last = line
    return segs


def vtt_text(raw):
    """Plain spoken text from a .vtt subtitle file (no timings, tags or repeated lines)."""
    return " ".join(t for (_a, _b, t) in vtt_segments(raw))


def speech_timeline(segments, limit=2500):
    """'0:03 hello there / 0:07 how are you' style text, cut at `limit` characters."""
    out, size = [], 0
    for a, _b, txt in segments:
        ln = "%s %s" % (fmt_time(a), txt)
        if size + len(ln) > limit:
            out.append("...")
            break
        out.append(ln)
        size += len(ln) + 1
    return "\n".join(out)


# ---------- tools ----------

def _ytdlp():
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    return [sys.executable.replace("pythonw.exe", "python.exe"), "-m", "yt_dlp"]


def _ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    hits = glob.glob(str(Path.home() / "AppData/Local/Microsoft/WinGet/Packages/*FFmpeg*/**/bin/ffmpeg.exe"), recursive=True)
    return hits[0] if hits else None


_WHISPER = {}


def _read_wav(path):
    """16 kHz mono 16-bit wav -> float32 samples (read here so faster-whisper's own audio decoder is not needed)."""
    import wave

    import numpy as np
    with wave.open(str(path), "rb") as w:
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _add_cuda_dlls():
    """Make the pip-installed CUDA libraries (nvidia-cublas-cu12, nvidia-cudnn-cu12) loadable by faster-whisper."""
    import os
    try:
        base = Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"
        for d in glob.glob(str(base / "*" / "bin")):
            os.add_dll_directory(d)
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


def transcribe(wav, log=lambda m: None):
    """Speech in a 16 kHz wav -> [(start, end, text)] with faster-whisper (GPU first, CPU as a fallback).
    [] if nothing was said, None if the speech helper is not installed or failed."""
    _add_cuda_dlls()
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    for device, ctype in (("cuda", "float16"), ("cpu", "int8")):
        try:
            if device not in _WHISPER:
                _WHISPER[device] = WhisperModel("small", device=device, compute_type=ctype)
            segs, _info = _WHISPER[device].transcribe(_read_wav(wav), vad_filter=True, beam_size=1)
            return [(float(x.start), float(x.end), x.text.strip()) for x in segs if x.text.strip()]
        except Exception as exc:
            log("whisper on %s failed: %r" % (device, exc))
            _WHISPER.pop(device, None)
    return None


def scene_times(ff, vid, log=lambda m: None):
    """Seconds where the picture changes a lot (cuts), using ffmpeg's scene detector."""
    try:
        _o, err, _c, _r = run_capture([ff, "-hide_banner", "-i", str(vid), "-vf", "select='gt(scene,0.3)',showinfo",
                                       "-an", "-f", "null", "-"], 120)
        return parse_scene_times(err)
    except Exception as exc:
        log("scene detect failed: " + repr(exc))
        return []


def _describe(batch, model, ollama_url, caption, speech_by_time):
    """batch = [(time, path)]. Returns the model's text: one line per picture + the text visible on screen."""
    imgs = [base64.b64encode(Path(p).read_bytes()).decode("ascii") for _t, p in batch]
    listing = "\n".join("Picture %d is at %s%s" % (i + 1, fmt_time(t), (' (spoken around then: "%s")' % speech_by_time[i][:160])
                                                  if speech_by_time[i] else "")
                        for i, (t, _p) in enumerate(batch))
    prompt = ("These %d pictures come from ONE video, in time order.%s\n%s\n\n"
              "Answer in two parts.\nPART 1: one line per picture, starting with its time (like '0:12 - '), saying "
              "concretely what is shown: people, objects, setting, action, and what changed since the previous picture.\n"
              "PART 2: 'On-screen text:' followed by every piece of text you can read in the pictures (captions, prices, "
              "names, logos), or 'none'.\n"
              "Only say what you can actually see. If something is unclear or too small, say 'unclear' instead of guessing."
              % (len(imgs), (" Its caption is: " + caption[:300] + ".") if caption else "", listing))
    body = {"model": model, "stream": False, "think": False, "options": {"temperature": 0.2, "num_predict": 700},
            "messages": [{"role": "user", "content": prompt, "images": imgs}]}
    r = requests.post(ollama_url.rstrip("/") + "/api/chat", json=body, timeout=300).json()
    return str(r.get("message", {}).get("content", "")).strip()


def watch(url, model, ollama_url, log=lambda m: None, wait=480):
    """Analyse a video of any length in a background job (see video_brain.py) and return its report, or a progress note."""
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return "That does not look like a video link."
    import video_brain
    return video_brain.start_and_wait(url, model, ollama_url, log, max_wait=wait)


def watch_quick(url, model, ollama_url, log=lambda m: None):
    """The old one-shot version (first 15 minutes only, no memory). Kept for reference and tests."""
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return "That does not look like a video link."
    work = Path(tempfile.mkdtemp(prefix="atlas_watch_"))
    try:
        base = _ytdlp()
        out, err, code, _ = run_capture(base + ["--no-playlist", "--dump-json", "--no-warnings", url], 90)
        if code != 0 or not out.strip():
            if "No module named" in err:
                return "Video watching is not installed yet (yt-dlp is missing)."
            return "I could not open that link (" + (err.strip().splitlines() or ["unknown error"])[-1][:160] + ")."
        info = json.loads(out.strip().splitlines()[0])
        title = info.get("title") or ""
        who = info.get("uploader") or info.get("channel") or ""
        desc = (info.get("description") or "").strip()
        dur = info.get("duration") or 0
        lines = ["Video: %s%s%s" % (title, (" by " + who) if who else "", (", %s long" % fmt_time(dur)) if dur else "")]
        if dur and dur > MAX_SECONDS:
            lines.append("(It is long: I only looked at the first %d minutes.)" % (MAX_SECONDS // 60))
        if desc and desc != title:
            lines.append("Caption: " + desc[:600])
        ff = _ffmpeg()
        if not ff:
            lines.append("(I could not look at the pictures: ffmpeg is missing.)")
            return "\n".join(lines)
        cmd = base + ["--no-playlist", "--no-warnings", "-f", "bv*[height<=360]+ba/b[height<=360]/bv*[height<=480]+ba/b/worst",
                      "--merge-output-format", "mp4", "--max-filesize", "%dM" % MAX_MB, "--ffmpeg-location", str(Path(ff).parent),
                      "--write-subs", "--write-auto-subs", "--sub-langs", "en,en-orig", "--ignore-errors", "--sub-format", "vtt",
                      "-o", str(work / "v.%(ext)s"), url]
        _o, derr, _c, why = run_capture(cmd, 480)
        files = [p for p in work.glob("v.*") if p.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov")]
        if not files:
            tail = (derr.strip().splitlines() or [""])[-1][:200]
            log("watch download failed: %s | %s" % (why, tail))
            reason = "it took too long" if why else (tail or "unknown reason")
            if "larger than max-filesize" in derr or "File is larger" in derr:
                reason = "the video file is too big"
            lines.append("(I could not download the video itself (%s), so I only have its title and caption.)" % reason)
            return "\n".join(lines)
        vid = files[0]

        # speech: subtitles if the site has them, else listen
        segments, heard = [], None
        subs = next(iter(work.glob("v.*.vtt")), None)
        if subs:
            segments = vtt_segments(subs.read_text(encoding="utf-8", errors="replace"))
            heard = segments
        else:
            wav = work / "a.wav"
            run_capture([ff, "-y", "-i", str(vid), "-t", str(MAX_SECONDS), "-vn", "-ac", "1", "-ar", "16000", str(wav)], 180)
            if wav.exists() and wav.stat().st_size > 1000:
                heard = transcribe(wav, log)
                segments = heard or []
        if segments:
            lines.append("Speech with times%s:\n%s" % (" (from subtitles)" if subs else "", speech_timeline(segments)))

        # pictures: even spacing + every scene change
        scenes = scene_times(ff, vid, log)
        times = choose_times(dur or 20, scenes)
        frames = []
        for i, t in enumerate(times):
            fp = work / ("f%d.jpg" % i)
            run_capture([ff, "-y", "-ss", str(t), "-i", str(vid), "-frames:v", "1", "-vf", "scale=720:-2", "-q:v", "3",
                         str(fp)], 40)
            if fp.exists() and fp.stat().st_size > 0:
                frames.append((t, fp))
        if not frames:
            lines.append("(I could not take pictures from the video.)")
            return "\n".join(lines)
        seen_parts = []
        for k in range(0, len(frames), BATCH):
            batch = frames[k:k + BATCH]
            try:
                seen_parts.append(_describe(batch, model, ollama_url, desc or title,
                                            [speech_near(segments, t) for t, _p in batch]))
            except Exception as exc:
                log("watch describe failed: " + repr(exc))
        lines.append("What I see (%d pictures, %d scene changes found):\n%s" % (
            len(frames), len(scenes), "\n".join(seen_parts) if seen_parts else "I could not describe the pictures."))
        if heard is None:
            lines.append("Note: I cannot hear the audio (speech helper missing), so I do not know what is said.")
        elif not segments:
            lines.append("Note: I listened and heard no speech (maybe only music or sound); I cannot tell what the music is.")
        return "\n".join(lines)
    except Exception as exc:
        log("watch failed: " + repr(exc))
        return "Something went wrong while watching that video."
    finally:
        shutil.rmtree(work, ignore_errors=True)
