"""The video analyst. Turns a video link into something you can ask questions about.

Stages (each one is saved to disk, so a cut-off job continues where it stopped and nothing is done twice):
  meta      title, length, chapters, subtitles?                    (yt-dlp, seconds)
  speech    subtitles, or Whisper on the GPU                        (whole video, any length)
  frames    pictures: scene changes + chapters + even spacing       (file for short videos, streamed for long ones)
  captions  the vision model describes the pictures in batches      (what happens + text on screen)
  chunks    each few-minute part is summarised from its speech + pictures
  report    the parts are merged into one overview with key moments and a timeline
The job runs in a detached background process (video_job.py); watch() starts or joins it and waits up to a limit."""
import json
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import no_window  # noqa: E402
import video_watch as vw  # noqa: E402
from video_cache import Store, job_state, prune, video_key  # noqa: E402
from video_plan import chunk_plan, fmt_time, group_by_chunk, long_budget, plan_frames  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "logs" / "video_cache"
SHORT_LIMIT = 1200          # seconds: up to 20 minutes the video file is downloaded (scene changes, fast frames); longer = streamed
BATCH = 8                   # pictures per vision call
DL_MB = 400


def short_text(text, limit):
    """Cut text to at most `limit` characters, at the end of a sentence when possible."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    k = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[:k + 1] if k > limit * 0.4 else cut.rstrip() + "...")


def _noop(_msg):
    pass


# ---------- model calls ----------

def _chat(model, ollama_url, content, images=None, tokens=500, timeout=300):
    msg = {"role": "user", "content": content}
    if images:
        msg["images"] = images
    body = {"model": model, "stream": False, "think": False, "options": {"temperature": 0.2, "num_predict": tokens}, "messages": [msg]}
    r = requests.post(ollama_url.rstrip("/") + "/api/chat", json=body, timeout=timeout).json()
    return str(r.get("message", {}).get("content", "")).strip()


def parse_captions(text, times):
    """Parse the vision answer: lines '<n>: description' and a 'TEXT: ...' line.
    Returns ([(time, description)], screen_text). If the format was ignored the whole answer is attached to the first time."""
    caps, screen = [], ""
    for ln in (text or "").splitlines():
        m = re.match(r"^\W*(?:picture\s*)?(\d{1,2})\s*[:.)\-]\s*(.+)$", ln.strip(), re.I)
        if m and 1 <= int(m.group(1)) <= len(times) and not ln.strip().upper().startswith("TEXT"):
            caps.append((times[int(m.group(1)) - 1], m.group(2).strip()))
            continue
        m = re.match(r"^\W*(?:on-screen\s*)?text\s*:\s*(.*)$", ln.strip(), re.I)
        if m:
            screen = m.group(1).strip()
    if not caps and (text or "").strip():
        caps = [(times[0], " ".join((text or "").split())[:600])] if times else []
    return caps, ("" if screen.lower() in ("none", "n/a", "") else screen)


def describe_batch(batch, model, ollama_url, context):
    """batch = [(time, jpg path)] -> ([(time, description)], on_screen_text)"""
    import base64
    imgs = [base64.b64encode(Path(p).read_bytes()).decode("ascii") for _t, p in batch]
    times = [t for t, _p in batch]
    listing = "\n".join("Picture %d is at %s" % (i + 1, fmt_time(t)) for i, t in enumerate(times))
    prompt = ("These %d pictures come from ONE video, in time order.%s\n%s\n\nAnswer in EXACTLY this format. One line per picture: "
              "'<number>: <what is shown: people, objects, setting, action, and what changed since the previous picture; 1-2 "
              "sentences>'. Then one last line: 'TEXT: <every piece of readable on-screen text (captions, titles, names, prices, "
              "logos), or none>'. Only say what you can actually see; if something is unclear say 'unclear'."
              % (len(imgs), (" Context: " + context[:300]) if context else "", listing))
    return parse_captions(_chat(model, ollama_url, prompt, imgs, tokens=700), times)


def summarise_chunk(model, ollama_url, meta, chunk, speech, caps, screen):
    title = chunk.get("title") or ""
    parts = ["Video: %s." % meta.get("title", ""), "Part %s to %s%s." % (fmt_time(chunk["start"]), fmt_time(chunk["end"]),
                                                                        (" (" + title + ")") if title else "")]
    if speech:
        parts.append("What is said:\n" + speech[:7000])
    if caps:
        parts.append("What is shown:\n" + "\n".join("%s %s" % (fmt_time(t), d) for t, d in caps)[:3500])
    if screen:
        parts.append("Text on screen: " + screen[:600])
    parts.append("Summarise this part of the video in 3 to 5 plain sentences: what happens, the main points or events, and "
                 "anything important on screen. Do not invent anything that is not in the material above.")
    return _chat(model, ollama_url, "\n\n".join(parts), tokens=350, timeout=200)


def final_overview(model, ollama_url, meta, summaries):
    each = max(220, min(600, 15000 // max(1, len(summaries))))
    body = "\n".join("[%s-%s] %s" % (fmt_time(c["start"]), fmt_time(c["end"]), short_text(s, each)) for c, s in summaries)
    prompt = ("Video: %s (by %s, %s long).\n\nSummaries of each part, in order:\n%s\n\nWrite: 1) 'Overview:' 3-6 sentences on what the "
              "whole video is about and how it develops. 2) 'Key moments:' up to 8 bullet points, each starting with its time "
              "like '12:30 - ...'. Use only the summaries above." % (
                  meta.get("title", ""), meta.get("uploader", ""), fmt_time(meta.get("duration") or 0), body))
    return _chat(model, ollama_url, prompt, tokens=700, timeout=240)


# ---------- media ----------

def probe(url, base):
    out, err, code, _ = no_window.run_capture(base + ["--no-playlist", "-J", "--no-warnings", url], 120)
    if code != 0 or not out.strip():
        if "No module named" in err:
            raise RuntimeError("Video watching is not installed yet (yt-dlp is missing).")
        raise RuntimeError("I could not open that link (%s)." % ((err.strip().splitlines() or ["unknown error"])[-1][:160]))
    j = json.loads(out)
    subs = list((j.get("subtitles") or {}).keys()) + list((j.get("automatic_captions") or {}).keys())
    return {"id": j.get("id"), "title": j.get("title") or "", "uploader": j.get("uploader") or j.get("channel") or "",
            "description": (j.get("description") or "")[:1500], "duration": j.get("duration") or 0,
            "chapters": [{"start": c.get("start_time"), "end": c.get("end_time"), "title": c.get("title")} for c in (j.get("chapters") or [])],
            "has_subs": any(k.startswith("en") for k in subs), "url": url}


def get_subtitles(url, base, work):
    no_window.run_capture(base + ["--no-playlist", "--skip-download", "--write-subs", "--write-auto-subs", "--sub-langs", "en,en-orig",
                                  "--sub-format", "vtt", "--ignore-errors", "--no-warnings", "-o", str(work / "s.%(ext)s"), url], 120)
    files = sorted(work.glob("s.*.vtt"), key=lambda p: (".en." not in p.name, p.name))
    if not files:
        return []
    return vw.vtt_segments(files[0].read_text(encoding="utf-8", errors="replace"))


def download_video(url, base, ff, work):
    cmd = base + ["--no-playlist", "--no-warnings", "-f", "bv*[height<=360]+ba/b[height<=360]/bv*[height<=480]+ba/b/worst",
                  "--merge-output-format", "mp4", "--max-filesize", "%dM" % DL_MB, "--ffmpeg-location", str(Path(ff).parent),
                  "-o", str(work / "v.%(ext)s"), url]
    _o, err, _c, why = no_window.run_capture(cmd, 900)
    files = [p for p in work.glob("v.*") if p.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov")]
    if not files:
        raise RuntimeError("I could not download the video (%s)." % ("it took too long" if why else ((err.strip().splitlines() or ["unknown"])[-1][:160])))
    return files[0]


def audio_wav(url, base, ff, work, video=None):
    """16 kHz mono wav of the whole audio: from the downloaded video, or (long videos) from the audio stream alone."""
    src = video
    if src is None:
        no_window.run_capture(base + ["--no-playlist", "--no-warnings", "-f", "ba/b", "--max-filesize", "%dM" % DL_MB,
                                      "-o", str(work / "a.%(ext)s"), url], 1200)
        cand = [p for p in work.glob("a.*") if p.suffix.lower() not in (".wav", ".part", ".json")]
        if not cand:
            return None
        src = cand[0]
    wav = work / "a16.wav"
    no_window.run_capture([ff, "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", str(wav)], 900)
    return wav if wav.exists() and wav.stat().st_size > 1000 else None


def stream_url(url, base):
    out, _e, code, _ = no_window.run_capture(base + ["--no-playlist", "--no-warnings", "-g", "-f", "bv*[height<=480]/b[height<=480]/b", url], 90)
    lines = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("http")]
    return lines[0] if code == 0 and lines else None


def grab_frames(ff, source, times, outdir, width, workers=4):
    """One picture per time from a file or a stream URL, a few at once. Returns [(time, path)] in time order."""
    def one(item):
        i, t = item
        fp = outdir / ("f%03d.jpg" % i)
        no_window.run_capture([ff, "-y", "-ss", str(t), "-i", str(source), "-frames:v", "1", "-vf", "scale=%d:-2" % width, "-q:v", "4", str(fp)], 90)
        return (t, fp) if fp.exists() and fp.stat().st_size > 0 else None
    with ThreadPoolExecutor(max_workers=workers) as ex:
        res = list(ex.map(one, list(enumerate(times))))
    return [r for r in res if r]


# ---------- the pipeline ----------

def analyze(url, model, ollama_url, log=_noop):
    """Run (or resume) the whole analysis. Writes everything to the cache folder. Returns the final report text."""
    key = video_key(url)
    store = Store(CACHE / key)
    work = store.dir / "work"
    work.mkdir(exist_ok=True)
    base = vw._ytdlp()
    ff = vw._ffmpeg()
    if not ff:
        raise RuntimeError("ffmpeg is missing, so I cannot look at videos.")

    def step(stage, done=0, total=0, text=""):
        store.progress(stage, done, total, text)
        log("video: %s %s" % (stage, text))

    # 1 meta
    step("meta", 0, 1, "Opening the link")
    meta = store.get("meta") or probe(url, base)
    store.put("meta", meta)
    d = float(meta.get("duration") or 0)
    short = 0 < d <= SHORT_LIMIT or d == 0
    chunks = chunk_plan(d or 60, meta.get("chapters"))
    video = None

    # 2 speech
    seg = store.get("speech")
    if seg is None:
        step("speech", 0, 1, "Getting the speech")
        segments = get_subtitles(url, base, work) if meta.get("has_subs") else []
        source = "subtitles"
        if not segments:
            source = "whisper"
            if short:
                step("download", 0, 1, "Downloading a small copy")
                video = download_video(url, base, ff, work)
            step("speech", 0, 1, "Listening to the audio on the graphics card")
            wav = audio_wav(url, base, ff, work, video)
            heard = vw.transcribe(wav, log) if wav else None
            segments = heard or []
            if heard is None:
                source = "none (speech helper missing)"
            elif not heard:
                source = "none (no speech found)"
        seg = {"source": source, "segments": [list(s) for s in segments]}
        store.put("speech", seg)
    segments = [tuple(s) for s in seg["segments"]]

    # 3 frames
    caps = store.get("captions")
    if caps is None:
        scenes = []
        if short:
            if video is None:
                cand = [p for p in work.glob("v.*") if p.suffix.lower() in (".mp4", ".webm", ".mkv", ".mov")]
                video = cand[0] if cand else None
            if video is None:
                step("download", 0, 1, "Downloading a small copy")
                video = download_video(url, base, ff, work)
            step("frames", 0, 1, "Looking for scene changes")
            scenes = vw.scene_times(ff, video, log)
        budget = vw.frame_budget(d) if short else long_budget(d)
        times = plan_frames(d or 60, chunks, scenes, budget)
        step("frames", 0, len(times), "Taking %d pictures" % len(times))
        source = video if short else stream_url(url, base)
        if source is None:
            raise RuntimeError("I could not get the picture stream of that video.")
        shots_dir = work / "shots"
        shots_dir.mkdir(exist_ok=True)
        frames = grab_frames(ff, source, times, shots_dir, 720 if short else 640)
        if not frames:
            raise RuntimeError("I could not take pictures from that video.")

        # 4 captions
        all_caps, texts = [], []
        batches = [frames[i:i + BATCH] for i in range(0, len(frames), BATCH)]
        for n, b in enumerate(batches, 1):
            step("captions", n - 1, len(batches), "Looking at pictures %d-%d of %d" % ((n - 1) * BATCH + 1, (n - 1) * BATCH + len(b), len(frames)))
            try:
                c, t = describe_batch(b, model, ollama_url, meta.get("title", ""))
                all_caps += c
                if t:
                    texts.append((b[0][0], t))
            except Exception as exc:
                log("caption batch failed: " + repr(exc))
        caps = {"captions": [[t, x] for t, x in all_caps], "screen_text": [[t, x] for t, x in texts], "pictures": len(frames),
                "scenes": len(scenes)}
        store.put("captions", caps)
        shutil.rmtree(shots_dir, ignore_errors=True)
    cap_list = [tuple(c) for c in caps["captions"]]
    txt_list = [tuple(c) for c in caps["screen_text"]]

    # 5 chunk summaries (resumable one by one)
    sums = store.get("chunks") or []
    sp_groups = group_by_chunk(chunks, segments)
    cap_groups = group_by_chunk(chunks, cap_list)
    txt_groups = group_by_chunk(chunks, txt_list)
    for i, c in enumerate(chunks):
        if i < len(sums):
            continue
        step("chunks", i, len(chunks), "Understanding part %d of %d (%s)" % (i + 1, len(chunks), fmt_time(c["start"])))
        speech = " ".join(s[2] for s in sp_groups[i])
        screen = " | ".join(t[1] for t in txt_groups[i])
        try:
            text = summarise_chunk(model, ollama_url, meta, c, speech, cap_groups[i], screen)
        except Exception as exc:
            log("chunk summary failed: " + repr(exc))
            text = ""
        sums.append(text or "(could not summarise this part)")
        store.put("chunks", sums)

    # 6 report
    step("report", 0, 1, "Putting it all together")
    summaries = list(zip(chunks, sums))
    overview = ""
    try:
        overview = final_overview(model, ollama_url, meta, summaries)
    except Exception as exc:
        log("overview failed: " + repr(exc))
    report = build_report(meta, seg, caps, chunks, sums, overview)
    store.put("report", {"text": report})
    shutil.rmtree(work, ignore_errors=True)
    prune(CACHE)
    store.progress("done", 1, 1, "Done", finished=True)
    return report


def build_report(meta, seg, caps, chunks, sums, overview):
    d = meta.get("duration") or 0
    lines = ["Video: %s%s%s" % (meta.get("title", ""), (" by " + meta["uploader"]) if meta.get("uploader") else "",
                               (", %s long" % fmt_time(d)) if d else "")]
    if meta.get("description"):
        lines.append("Caption: " + meta["description"][:500])
    if overview:
        lines.append(overview)
    lines.append("Timeline:")
    each = 900 if len(chunks) <= 8 else 260
    for c, s in zip(chunks, sums):
        lines.append("[%s-%s]%s %s" % (fmt_time(c["start"]), fmt_time(c["end"]), (" " + c["title"]) if c.get("title") else "", short_text(s, each)))
    n = len(seg.get("segments", []))
    src = seg.get("source", "")
    note = "How I watched it: %d pictures (%d scene changes found), " % (caps.get("pictures", 0), caps.get("scenes", 0))
    if src == "subtitles":
        note += "the full subtitles (%d lines)." % n
    elif src == "whisper":
        note += "the full speech turned into text (%d lines)." % n
    else:
        note += "no speech: " + src + "."
    note += " I cannot hear music or sound effects, and I only see snapshots, not every moment."
    lines.append(note)
    lines.append("I can answer detailed questions about any moment with the ask_video tool (same link).")
    return "\n".join(lines)


# ---------- starting and joining the background job ----------

def start_job(url, model, ollama_url):
    cmd = [no_window.child_python(), str(Path(__file__).resolve().parent / "video_job.py"), url, model, ollama_url]
    return no_window.start_detached(cmd, str(ROOT))


def start_and_wait(url, model, ollama_url, log=_noop, max_wait=480):
    """Return the report if it is ready (or becomes ready within max_wait seconds); otherwise a plain progress message.
    The analysis keeps running in the background and a second call with the same link joins it."""
    key = video_key(url)
    store = Store(CACHE / key)
    rep = store.get("report")
    if rep and rep.get("text"):
        return rep["text"]
    state, prog = job_state(store)
    if state in ("none", "failed"):
        store.progress("start", 0, 1, "Starting")
        start_job(url, model, ollama_url)
    t0, last = time.time(), ""
    while time.time() - t0 < max_wait:
        time.sleep(3)
        rep = store.get("report")
        if rep and rep.get("text"):
            return rep["text"]
        state, prog = job_state(store)
        if state == "failed":
            return "I could not finish watching that video: %s" % (prog.get("text") or "the helper stopped")
        txt = prog.get("text", "")
        if txt and txt != last:
            last = txt
            log("video: " + txt)
    state, prog = job_state(store)
    where = ""
    if prog.get("total"):
        where = " (%s: %s of %s)" % (prog.get("stage", ""), prog.get("done", 0), prog.get("total", 0))
    return ("Still watching that video%s. It keeps working in the background; ask me again with the same link in a few minutes "
            "and I will continue from where it is. Current step: %s." % (where, prog.get("text", "working")))
