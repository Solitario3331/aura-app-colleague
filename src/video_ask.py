"""Answer questions about a video that was already watched (video_brain.py): finds the right speech, pictures and part
summaries in the saved results by time ("at 20:00") and by words. No model call here: the calling assistant answers from
the excerpts this returns."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from video_plan import chunk_plan, fmt_time  # noqa: E402

STOP = set("the a an and or but is are was were be been to of in on at for with about what who when where why how did does do "
           "this that these those it its he she they them his her their you your me my i we our can could would should will "
           "video says said say tell talk talks mention mentions happen happens part parts please explain explains explained "
           "show shows showed".split())


def question_times(q):
    """Seconds mentioned in a question: '20:00', '1:02:30', 'minute 20', 'at 15 min'. Duplicates removed."""
    out = []
    for m in re.finditer(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b", q or ""):
        a, b, c = int(m.group(1)), int(m.group(2)), m.group(3)
        out.append(a * 3600 + b * 60 + int(c) if c is not None else a * 60 + b)
    for m in re.finditer(r"\b(?:minute|min|minutes)\s*(\d{1,3})\b|\b(\d{1,3})\s*(?:minutes?|mins?)\b", q or "", re.I):
        n = int(m.group(1) or m.group(2))
        out.append(n * 60)
    seen, res = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            res.append(t)
    return res


def keywords(q):
    words = re.findall(r"[a-z0-9']{3,}", (q or "").lower())
    return [w for w in words if w not in STOP and not w.isdigit()]


def _hits(text, kws):
    low = text.lower()
    n = 0
    for k in kws:
        stem = k[:5] if len(k) > 5 else k
        if stem in low:
            n += 1
    return n


def windows(segments, span=25.0):
    """Merge consecutive speech lines into windows of about `span` seconds: [(start, end, text)]"""
    out, cur = [], None
    for a, b, txt in segments:
        if cur is None:
            cur = [a, b, txt]
        elif b - cur[0] <= span:
            cur[1] = b
            cur[2] += " " + txt
        else:
            out.append(tuple(cur))
            cur = [a, b, txt]
    if cur:
        out.append(tuple(cur))
    return out


def retrieve(question, meta, segments, captions, screen_text, chunks, sums, limit=5500):
    """Plain-text excerpts that answer the question, or an overview hint if nothing matches."""
    times = question_times(question)
    kws = keywords(question)
    parts = []
    used = 0

    def add(title, lines):
        nonlocal used
        if not lines:
            return
        block = title + "\n" + "\n".join(lines)
        if used + len(block) > limit:
            block = block[:max(0, limit - used)]
        if block.strip():
            parts.append(block)
            used += len(block) + 1

    for t in times[:3]:
        sp = [("%s %s" % (fmt_time(a), x)) for a, b, x in segments if t - 75 <= a <= t + 75]
        cp = [("%s %s" % (fmt_time(c), x)) for c, x in captions if t - 60 <= c <= t + 60]
        tx = [("%s %s" % (fmt_time(c), x)) for c, x in screen_text if t - 90 <= c <= t + 90]
        add("Around %s - what is said:" % fmt_time(t), sp[:24])
        add("Around %s - what is shown:" % fmt_time(t), cp[:6])
        add("Around %s - text on screen:" % fmt_time(t), tx[:3])
        for c, s in zip(chunks, sums):
            if c["start"] <= t < c["end"] or (t >= c["end"] and c is chunks[-1]):
                add("Part summary %s-%s:" % (fmt_time(c["start"]), fmt_time(c["end"])), [s])
                break
    if kws:
        wins = windows(segments)
        scored = sorted(((_hits(w[2], kws), -i, w) for i, w in enumerate(wins)), reverse=True)
        top = [w for sc, _i, w in scored[:4] if sc > 0]
        add("Where the speech matches your words (%s):" % ", ".join(kws[:6]),
            ["%s-%s %s" % (fmt_time(a), fmt_time(b), x[:700]) for a, b, x in sorted(top)])
        capmatch = [(c, x) for c, x in captions if _hits(x, kws) > 0][:4]
        add("Pictures that match:", ["%s %s" % (fmt_time(c), x) for c, x in capmatch])
        txmatch = [(c, x) for c, x in screen_text if _hits(x, kws) > 0][:3]
        add("Text on screen that matches:", ["%s %s" % (fmt_time(c), x) for c, x in txmatch])
        sm = sorted(((_hits(s, kws), -i, c, s) for i, (c, s) in enumerate(zip(chunks, sums))), reverse=True)
        add("Part summaries that match:", ["[%s-%s] %s" % (fmt_time(c["start"]), fmt_time(c["end"]), s) for sc, _i, c, s in sm[:2] if sc > 0])
    if not parts:
        head = ["[%s-%s] %s" % (fmt_time(c["start"]), fmt_time(c["end"]), s) for c, s in list(zip(chunks, sums))[:6]]
        add("I found nothing that matches those words exactly. The video, part by part (ask with a time like 12:30 or other words):", head)
    return "\n\n".join(parts)


def ask(url, question, model, ollama_url, log=lambda m: None, wait=480):
    """Excerpts for the question; watches the video first if that has not happened yet."""
    from video_brain import CACHE, start_and_wait
    from video_cache import Store, video_key
    store = Store(CACHE / video_key(url))
    if not (store.get("report") or {}).get("text"):
        first = start_and_wait(url, model, ollama_url, log, max_wait=wait)
        if not (store.get("report") or {}).get("text"):
            return first
    meta = store.get("meta") or {}
    seg = [tuple(s) for s in (store.get("speech") or {}).get("segments", [])]
    caps = store.get("captions") or {}
    chunks = chunk_plan(meta.get("duration") or 60, meta.get("chapters"))
    sums = store.get("chunks") or []
    body = retrieve(question, meta, seg, [tuple(c) for c in caps.get("captions", [])],
                    [tuple(c) for c in caps.get("screen_text", [])], chunks, sums)
    return "Video: %s (%s long)\nQuestion: %s\n\n%s" % (meta.get("title", ""), fmt_time(meta.get("duration") or 0), question, body)
