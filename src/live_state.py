"""The live picture of a chat that is still working, kept on the Python side.

The page may switch to another chat and come back later; it then gets this snapshot (text so far + step
lines) instead of the events it missed. Events are small dicts:
  {"t": "new_bubble"} | {"t": "text", "text": <whole text of the current bubble>} |
  {"t": "tool"/"step", "kind": ..., "text": ..., "detail": ...} | {"t": "result", "out": ...} |
  {"t": "phase", "kind": ...} | {"t": "done"/"closed", ...}

apply(items, ev)  updates the snapshot list in place.
push(queue, ev)   adds an event to a waiting queue; consecutive "text" events collapse into the newest one
                  (each carries the whole bubble text), so a queue nobody reads stays small.
"""
MAX_ITEMS = 200
MAX_QUEUE = 500
MAX_OUT = 1500


def apply(items, ev) -> None:
    if not isinstance(items, list) or not isinstance(ev, dict):
        return
    t = ev.get("t")
    if t == "new_bubble":
        _close(items)
        items.append({"k": "text", "text": "", "open": True})
    elif t == "text":
        text = ev.get("text") if isinstance(ev.get("text"), str) else ""
        if items and items[-1].get("k") == "text" and items[-1].get("open"):
            items[-1]["text"] = text
        else:
            items.append({"k": "text", "text": text, "open": True})
    elif t in ("tool", "step"):
        _close(items)
        chip = {"k": "chip", "kind": ev.get("kind") or "work", "text": str(ev.get("text") or "")}
        if isinstance(ev.get("detail"), str) and ev["detail"]:
            chip["detail"] = ev["detail"]
        items.append(chip)
    elif t == "result":  # the output of the newest step (Details view)
        out = ev.get("out")
        if isinstance(out, str) and out:
            for it in reversed(items):
                if it.get("k") == "chip":
                    prev = it.get("out")
                    it["out"] = ((prev + "\n" + out) if prev else out)[:MAX_OUT]
                    break
    elif t in ("done", "closed"):
        del items[:]
    if len(items) > MAX_ITEMS:  # drop the oldest step lines first, never text
        for i, it in enumerate(items):
            if it.get("k") == "chip":
                del items[i]
                break
        else:
            del items[0]


def _close(items) -> None:
    if items and items[-1].get("k") == "text":
        items[-1]["open"] = False


def push(queue, ev) -> None:
    if not isinstance(queue, list) or not isinstance(ev, dict):
        return
    if ev.get("t") == "text" and queue and queue[-1].get("t") == "text":
        queue[-1] = ev
    else:
        queue.append(ev)
    if len(queue) > MAX_QUEUE:
        del queue[: len(queue) - MAX_QUEUE]


def snapshot(items) -> list:
    """A copy safe to hand to the page (no 'open' flags)."""
    return [{k: v for k, v in it.items() if k != "open"} for it in items if it.get("k") != "text" or it.get("text")]
