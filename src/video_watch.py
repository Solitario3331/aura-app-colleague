"""Watch a video link (TikTok, YouTube, Instagram, X ... anything yt-dlp knows): download a small copy, take a few
pictures from it, let the local vision model describe them, and add the title, caption and subtitles.
The speech is turned into text with faster-whisper when it is installed; otherwise the result says it cannot hear."""
import base64
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
FRAMES = 8


def pick_times(duration, n=FRAMES):
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


def vtt_text(raw):
    """Plain spoken text from a .vtt subtitle file (no timings, tags or repeated lines)."""
    out, last = [], ""
    for line in raw.splitlines():
        line = re.sub(r"<[^>]+>", "", line).strip()
        if not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or "-->" in line or line.isdigit():
            continue
        if line != last:
            out.append(line)
            last = line
    return " ".join(out)


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


def transcribe(wav, log=lambda m: None):
    """Speech in a 16 kHz wav file -> text with faster-whisper (GPU first, CPU as a fallback). '' if nothing was said,
    None if the speech helper is not installed."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    for device, ctype in (("cuda", "float16"), ("cpu", "int8")):
        try:
            if device not in _WHISPER:
                _WHISPER[device] = WhisperModel("small", device=device, compute_type=ctype)
            segs, _info = _WHISPER[device].transcribe(str(wav), vad_filter=True, beam_size=1)
            return " ".join(x.text.strip() for x in segs).strip()
        except Exception as exc:
            log("whisper on %s failed: %r" % (device, exc))
            _WHISPER.pop(device, None)
    return None


def _describe(frames, model, ollama_url, caption):
    imgs = [base64.b64encode(Path(f).read_bytes()).decode("ascii") for f in frames]
    prompt = ("These are %d pictures taken in order, evenly spaced, from one short video.%s Describe what happens in the "
              "video from start to end: the people or things, the setting, actions, any text on screen. Be concrete and "
              "brief (under 150 words). Do not guess what is said." % (
                  len(imgs), (" Its caption is: " + caption[:300] + ".") if caption else ""))
    body = {"model": model, "stream": False, "think": False, "options": {"temperature": 0.2, "num_predict": 400},
            "messages": [{"role": "user", "content": prompt, "images": imgs}]}
    r = requests.post(ollama_url.rstrip("/") + "/api/chat", json=body, timeout=240).json()
    return str(r.get("message", {}).get("content", "")).strip()


def watch(url, model, ollama_url, log=lambda m: None):
    """Return a plain-text report about the video at url, or a plain error sentence."""
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
        lines = ["Video: %s%s%s" % (title, (" by " + who) if who else "",
                                    (", %d s long" % dur) if dur else "")]
        if desc and desc != title:
            lines.append("Caption: " + desc[:600])
        ff = _ffmpeg()
        if not ff:
            lines.append("(I could not look at the pictures: ffmpeg is missing.)")
            return "\n".join(lines)
        vid = work / "v.mp4"
        cmd = base + ["--no-playlist", "--no-warnings", "-f", "best[height<=480]/best", "--max-filesize", "%dM" % MAX_MB,
                      "--download-sections", "*0-%d" % MAX_SECONDS, "--ffmpeg-location", str(Path(ff).parent),
                      "--write-subs", "--write-auto-subs", "--sub-langs", "en.*,en", "--sub-format", "vtt",
                      "-o", str(work / "v.%(ext)s"), url]
        out, err, code, _ = run_capture(cmd, 300)
        files = [p for p in work.glob("v.*") if p.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov")]
        if not files:
            lines.append("(I could not download the video itself, so I only have its title and caption.)")
            return "\n".join(lines)
        vid = files[0]
        subs = next(iter(work.glob("v.*.vtt")), None)
        if subs:
            text = vtt_text(subs.read_text(encoding="utf-8", errors="replace"))
            if text:
                lines.append("Subtitles: " + text[:1500])
        heard = None
        if not subs:
            wav = work / "a.wav"
            run_capture([ff, "-y", "-i", str(vid), "-vn", "-ac", "1", "-ar", "16000", str(wav)], 120)
            if wav.exists() and wav.stat().st_size > 1000:
                heard = transcribe(wav, log)
                if heard:
                    lines.append("Spoken words (speech-to-text): " + heard[:2500])
        frames = []
        for i, t in enumerate(pick_times(dur or 20)):
            fp = work / ("f%d.jpg" % i)
            run_capture([ff, "-y", "-ss", str(t), "-i", str(vid), "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "4",
                         str(fp)], 40)
            if fp.exists() and fp.stat().st_size > 0:
                frames.append(fp)
        if not frames:
            lines.append("(I could not take pictures from the video.)")
            return "\n".join(lines)
        try:
            seen = _describe(frames, model, ollama_url, desc or title)
        except Exception as exc:
            log("watch describe failed: " + repr(exc))
            seen = ""
        lines.append("What I see (%d pictures): %s" % (len(frames), seen or "could not describe them."))
        if not subs and heard is None:
            lines.append("Note: I cannot hear the audio (speech helper missing), so I do not know what is said.")
        elif not subs and not heard:
            lines.append("Note: I listened and heard no speech (maybe only music or sound).")
        return "\n".join(lines)
    except Exception as exc:
        log("watch failed: " + repr(exc))
        return "Something went wrong while watching that video."
    finally:
        shutil.rmtree(work, ignore_errors=True)
