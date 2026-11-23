"""Raw detail for the "Details" view: what a step runs, and what came back (terminal style).

agent_detail(name, args)  -> one line for a local-helper tool call (agent.py)
claude_detail(line)       -> one line per tool call in a stream-json assistant line ("" if none)
claude_result(line)       -> the output text of tool results in a stream-json "user" line ("" if none)
clip(text, limit)         -> tidy, vendor/model names masked, capped
"""
import json
import re

try:
    from aura_mask import mask_names
except Exception:  # never let a missing helper break a detail line
    def mask_names(text):
        return text

MAX_DETAIL = 400
MAX_OUT = 700

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def clip(text, limit) -> str:
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    text = _CTRL.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    text = mask_names(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + " ..."
    return text


def _s(v) -> str:
    return v if isinstance(v, str) else ""


def agent_detail(name, args) -> str:
    try:
        args = args if isinstance(args, dict) else {}
        if name == "run_powershell":
            return clip("PS> " + _s(args.get("command")), MAX_DETAIL)
        if name == "write_file":
            return clip("write " + _s(args.get("path")) + " (" + str(len(_s(args.get("content")))) + " characters)", MAX_DETAIL)
        if name == "read_file":
            return clip("read " + _s(args.get("path")), MAX_DETAIL)
        if name == "delete_to_recycle_bin":
            return clip("recycle bin: " + _s(args.get("path")), MAX_DETAIL)
        if name == "open_website":
            return clip("open " + (_s(args.get("url")) or _s(args.get("path"))), MAX_DETAIL)
        return clip(str(name) + " " + json.dumps(args)[:200], MAX_DETAIL)
    except Exception:
        return ""


def _blocks(line, kind):
    try:
        obj = json.loads(line)
    except Exception:
        return []
    if not isinstance(obj, dict) or obj.get("type") != kind:
        return []
    msg = obj.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def claude_detail(line) -> str:
    try:
        out = []
        for b in _blocks(line, "assistant"):
            if b.get("type") != "tool_use":
                continue
            tool = _s(b.get("name"))
            inp = b.get("input") if isinstance(b.get("input"), dict) else {}
            if tool in ("Bash", "PowerShell"):
                out.append("$ " + _s(inp.get("command")))
            elif tool == "Read":
                out.append("read " + _s(inp.get("file_path")))
            elif tool == "Write":
                out.append("write " + _s(inp.get("file_path")) + " (" + str(len(_s(inp.get("content")))) + " characters)")
            elif tool in ("Edit", "MultiEdit", "NotebookEdit"):
                out.append("edit " + (_s(inp.get("file_path")) or _s(inp.get("notebook_path"))))
            elif tool in ("Glob", "Grep"):
                out.append("search " + (_s(inp.get("pattern")) or _s(inp.get("path"))))
            elif tool == "WebFetch":
                out.append("fetch " + _s(inp.get("url")))
            elif tool == "WebSearch":
                out.append("search the web: " + _s(inp.get("query")))
            else:
                out.append(tool or "step")
        return clip("\n".join(out), MAX_DETAIL)
    except Exception:
        return ""


def claude_result(line) -> str:
    try:
        parts = []
        for b in _blocks(line, "user"):
            if b.get("type") != "tool_result":
                continue
            c = b.get("content")
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, list):
                parts.append("\n".join(_s(x.get("text")) for x in c if isinstance(x, dict)))
        return clip("\n".join(p for p in parts if p), MAX_OUT)
    except Exception:
        return ""


def result_tool_ids(line) -> list:
    """The tool_use ids that a stream-json "user" line carries results for."""
    try:
        return [b.get("tool_use_id") for b in _blocks(line, "user")
                if b.get("type") == "tool_result" and isinstance(b.get("tool_use_id"), str)]
    except Exception:
        return []

