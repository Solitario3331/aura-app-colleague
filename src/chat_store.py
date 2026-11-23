import json
import os
import time
from pathlib import Path

_last_ns = 0


def _now_ns() -> int:
    global _last_ns
    v = time.time_ns()
    if v <= _last_ns:
        v = _last_ns + 1
    _last_ns = v
    return v


def _validate_sid(sid: str) -> None:
    if not isinstance(sid, str) or not sid or not sid.isalnum():
        raise ValueError(f"invalid session id: {sid!r}")


def _dir(d):
    p = Path(d)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _path(d, sid: str) -> Path:
    return Path(d) / (sid + ".json")


def _read(d, sid: str) -> dict:
    p = _path(d, sid)
    if not p.exists():
        raise KeyError(sid)
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["id"] = sid
    return data


def _write(d, sid: str, data: dict) -> None:
    """Write to a temporary file first, then swap it in: a crash or a reader at the same moment never
    sees a half-written chat."""
    p = _path(d, sid)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    for attempt in range(5):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:  # Windows: another program (virus scanner...) has it open for a moment
            if attempt == 4:
                raise
            time.sleep(0.05)


def new_session(d) -> str:
    p = _dir(d)
    import random
    while True:
        sid = "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(12))
        if not (p / (sid + ".json")).exists():
            break
    now = _now_ns()
    data = {"id": sid, "title": "New chat", "messages": [], "created": now, "updated": now}
    _write(d, sid, data)
    return sid


def add_message(d, sid: str, role: str, text: str) -> None:
    _validate_sid(sid)
    if role not in ("user", "assistant"):
        raise ValueError(f"invalid role: {role!r}")
    data = _read(d, sid)
    data["messages"].append({"role": role, "text": text})
    if data["title"] == "New chat" and role == "user":
        stripped = text.strip()
        if stripped:
            data["title"] = stripped[:40]
    data["updated"] = _now_ns()
    _write(d, sid, data)


def load_session(d, sid: str) -> dict:
    _validate_sid(sid)
    data = _read(d, sid)
    return {"id": data["id"], "title": data["title"], "messages": data["messages"]}


def empty_session_ids(d) -> list:
    """Ids of saved chats that have no messages at all (nothing worth keeping)."""
    p = Path(d)
    if not p.exists() or not p.is_dir():
        return []
    out = []
    for f in p.iterdir():
        if not f.is_file() or f.suffix != ".json" or not f.stem.isalnum():
            continue
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        if isinstance(data, dict) and not data.get("messages"):
            out.append(f.stem)
    return out


def list_sessions(d, include_empty=True, with_time=False) -> list:
    p = Path(d)
    if not p.exists() or not p.is_dir():
        return []
    items = []
    for f in p.iterdir():
        if not f.is_file() or f.suffix != ".json":
            continue
        sid = f.stem
        if not sid.isalnum():
            continue
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        if not include_empty and not data.get("messages"):
            continue
        updated = data.get("updated")
        created = data.get("created")
        if updated is None or created is None:
            mtime_ns = int(f.stat().st_mtime * 1_000_000_000)
            updated = mtime_ns
            created = mtime_ns
        items.append({"id": sid, "title": data.get("title", "New chat"), "_u": updated, "_c": created})
    items.sort(key=lambda x: (x["_u"], x["_c"]), reverse=True)
    if with_time:
        return [{"id": x["id"], "title": x["title"], "updated": x["_u"]} for x in items]
    return [{"id": x["id"], "title": x["title"]} for x in items]


def delete_session(d, sid: str) -> bool:
    _validate_sid(sid)
    p = _path(d, sid)
    if p.exists():
        p.unlink()
        return True
    return False
