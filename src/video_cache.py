"""Disk memory for the video analyst: one folder per video, one json file per stage, so work is never repeated and a job
that was cut off continues where it stopped. Also a tiny progress file that the waiting tool reads."""
import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def video_key(url):
    """A stable folder name for a link: 'yt_<id>' for YouTube links, else a hash of the link without tracking junk."""
    u = urlparse((url or "").strip())
    host = (u.hostname or "").lower()
    vid = ""
    if host.endswith("youtu.be"):
        vid = u.path.strip("/").split("/")[0]
    elif host.endswith("youtube.com"):
        vid = (parse_qs(u.query).get("v") or [""])[0]
        if not vid:
            m = re.match(r"/(?:shorts|embed|live)/([^/?]+)", u.path)
            vid = m.group(1) if m else ""
    if vid and re.fullmatch(r"[A-Za-z0-9_-]{6,20}", vid):
        return "yt_" + vid
    clean = "%s%s" % (host, u.path.rstrip("/"))
    return "u_" + hashlib.sha1(clean.encode("utf-8")).hexdigest()[:16]


class Store:
    """folder/<name>.json read and written atomically. Missing or damaged files read as None."""

    def __init__(self, folder):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)

    def get(self, name, default=None):
        try:
            return json.loads((self.dir / (name + ".json")).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def put(self, name, data):
        tmp = self.dir / (name + ".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.dir / (name + ".json"))

    def has(self, name):
        return self.get(name) is not None

    def progress(self, stage, done=0, total=0, text="", finished=False, failed=False):
        self.put("progress", {"stage": stage, "done": done, "total": total, "text": text, "finished": finished,
                              "failed": failed, "t": time.time()})


def job_state(store, stale_after=180):
    """('none'|'running'|'finished'|'failed', progress dict). 'running' that has not moved for stale_after seconds is 'failed'
    (the worker died)."""
    p = store.get("progress")
    if not p:
        return "none", {}
    if p.get("failed"):
        return "failed", p
    if p.get("finished"):
        return "finished", p
    if time.time() - float(p.get("t", 0)) > stale_after:
        return "failed", p
    return "running", p


def prune(root, max_items=40):
    """Keep only the newest max_items video folders."""
    try:
        kids = [p for p in Path(root).iterdir() if p.is_dir()]
        kids.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for old in kids[max_items:]:
            for f in old.iterdir():
                try:
                    f.unlink()
                except OSError:
                    pass
            try:
                old.rmdir()
            except OSError:
                pass
    except OSError:
        pass
