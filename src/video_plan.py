"""Pure planning helpers for the video analyst: how to cut a video into chunks and where to take pictures.
No network, no files: everything here is tested."""
import math

MIN_GAP = 1.0   # seconds: two pictures closer than this are the same moment


def fmt_time(t):
    """65.4 -> '1:05', 3725 -> '1:02:05'"""
    t = max(0, int(round(float(t))))
    h, m, s = t // 3600, (t % 3600) // 60, t % 60
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def chunk_plan(duration, chapters=None, target=300, min_len=120):
    """Cut a video into chunks [{'start','end','title'}] that cover 0..duration with no gaps.
    Uses the video's own chapters when there are at least 3; chapters shorter than min_len are merged into the next one
    and chapters longer than 2*target are split. Without chapters: equal pieces of about `target` seconds."""
    try:
        d = float(duration)
    except (TypeError, ValueError):
        d = 0.0
    if d <= 0:
        return []
    out = []
    chs = []
    for c in chapters or []:
        try:
            chs.append({"start": float(c.get("start_time", c.get("start", 0))), "end": float(c.get("end_time", c.get("end", 0))),
                        "title": str(c.get("title") or "").strip()})
        except (TypeError, ValueError, AttributeError):
            continue
    chs = sorted((c for c in chs if c["end"] > c["start"]), key=lambda c: c["start"])
    if len(chs) >= 3:
        chs[0]["start"] = 0.0                       # cover the very start and very end
        chs[-1]["end"] = max(chs[-1]["end"], d)
        merged = []
        for c in chs:
            if merged and (merged[-1]["end"] - merged[-1]["start"]) < min_len:
                merged[-1]["end"] = c["end"]
                merged[-1]["title"] = (merged[-1]["title"] + " / " + c["title"]).strip(" /") if c["title"] else merged[-1]["title"]
            else:
                merged.append(dict(c))
        if len(merged) > 1 and (merged[-1]["end"] - merged[-1]["start"]) < min_len:   # a tiny last one joins the one before
            merged[-2]["end"] = merged[-1]["end"]
            merged.pop()
        for c in merged:
            length = c["end"] - c["start"]
            parts = max(1, int(round(length / (2 * target)))) if length > 2 * target else 1
            for i in range(parts):
                a = c["start"] + length * i / parts
                b = c["start"] + length * (i + 1) / parts
                out.append({"start": round(a, 2), "end": round(b, 2), "title": c["title"] + (" (%d/%d)" % (i + 1, parts) if parts > 1 else "")})
    else:
        n = max(1, int(math.ceil(d / float(target))))
        for i in range(n):
            out.append({"start": round(d * i / n, 2), "end": round(d * (i + 1) / n, 2), "title": ""})
    return out


def long_budget(duration):
    """Pictures for a video: 24 to 90, about one per 90 seconds."""
    try:
        d = float(duration)
    except (TypeError, ValueError):
        d = 0.0
    return int(min(90, max(24, d / 90.0)))


def plan_frames(duration, chunks, scenes=None, budget=None):
    """Times (seconds) to take pictures: every chunk gets its share (at least 2), spread evenly inside the chunk; scene
    changes (if known) are added first and win over even spacing; no two pictures closer than MIN_GAP; at most `budget`."""
    try:
        d = float(duration)
    except (TypeError, ValueError):
        d = 0.0
    if d <= 0:
        return [0.5]
    budget = int(budget or long_budget(d))
    chunks = chunks or [{"start": 0.0, "end": d}]
    # share of the budget per chunk, proportional to its length, at least 2
    total = sum(max(0.001, c["end"] - c["start"]) for c in chunks)
    even = []
    for c in chunks:
        span = max(0.001, c["end"] - c["start"])
        k = max(2, int(round(budget * span / total)))
        even += [c["start"] + span * (i + 0.5) / k for i in range(k)]
    cuts = [round(s + 0.4, 2) for s in sorted(set(scenes or [])) if 0 <= s + 0.4 < d]
    room = max(0, budget - max(2, budget // 2))
    if len(cuts) > room:
        step = len(cuts) / room if room else 0
        cuts = [cuts[int(i * step)] for i in range(room)] if room else []
    picked = []
    for c in cuts:
        if not picked or c - picked[-1] >= MIN_GAP:
            picked.append(c)
    free = [t for t in even if all(abs(t - p) >= MIN_GAP for p in picked)]
    allowed = max(0, budget - len(picked))          # scene pictures are kept; the even ones are thinned to fit
    if len(free) > allowed:
        step = len(free) / float(allowed) if allowed else 0
        free = [free[int(i * step)] for i in range(allowed)] if allowed else []
    chosen = [(t, True) for t in picked] + [(t, False) for t in free]
    chosen.sort()
    out, last = [], None
    for t, _is_cut in chosen:                        # even ones thinned above may still touch each other: enforce the gap
        t = round(min(max(t, 0.3), d - 0.3), 2)
        if last is None or t - last >= MIN_GAP:
            out.append(t)
            last = t
    picked = out
    return picked


def chunk_index(chunks, t):
    """Index of the chunk that contains time t (last chunk for anything past the end)."""
    for i, c in enumerate(chunks):
        if t < c["end"]:
            return i
    return max(0, len(chunks) - 1)


def group_by_chunk(chunks, items, time_key=0):
    """Split items (tuples/lists or dicts) into one list per chunk by their time (item[time_key])."""
    groups = [[] for _ in chunks]
    for it in items:
        groups[chunk_index(chunks, it[time_key])].append(it)
    return groups
