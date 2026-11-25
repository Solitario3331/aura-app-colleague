"""Short, friendly step lines for the live "what Atlas is doing" list.

agent_step(name, args)  -> {"kind": ..., "text": ...} for a local-helper tool call (agent.py)
claude_step(line)       -> the same for a stream-json line with a tool call, or None
Only file NAMES are shown (never full paths), and vendor/model names are masked.
Kinds match the page's status words: read, search, edit, run, web, work, build, plan.
"""
import json
import re
from pathlib import PureWindowsPath

try:
    from aura_mask import mask_names
except Exception:  # never let a missing helper break a step line
    def mask_names(text):
        return text

MAX = 70


def _name(path) -> str:
    """Last part of a path or address, shortened."""
    if not isinstance(path, str):
        return ""
    p = path.strip().strip('"').strip("'").rstrip("\\/")
    if not p:
        return ""
    if re.match(r"^https?://", p, re.I):
        p = re.sub(r"^https?://(www\.)?", "", p, flags=re.I).split("/", 1)[0]
    else:
        p = PureWindowsPath(p).name or p
    return p if len(p) <= 40 else p[:37] + "..."


def _done(kind, text) -> dict:
    text = mask_names(re.sub(r"\s+", " ", text).strip())
    if len(text) > MAX:
        text = text[:MAX - 3].rstrip() + "..."
    return {"kind": kind, "text": text}


# (pattern, kind, text with a target "{}", text without one)
_PS_RULES = (
    (r"\b(invoke-webrequest|iwr|curl(\.exe)?|wget|start-bitstransfer)\b", "web", "Downloading {}", "Downloading"),
    (r"\b(winget|pip|choco|scoop|npm)\b.*\binstall\b|\bollama\s+pull\b", "run", "Installing {}", "Installing"),
    (r"\b(start-process|invoke-item|start|ii)\b", "run", "Opening {}", "Opening a program"),
    (r"\b(get-childitem|gci|dir|ls|get-item|test-path|resolve-path)\b", "search", "Looking through {}",
     "Looking through folders"),
    (r"\b(select-string|findstr|where-object)\b", "search", "Searching {}", "Searching"),
    (r"\b(get-content|gc|cat|type)\b", "read", "Reading {}", "Reading a file"),
    (r"\b(new-item|ni|mkdir|md|add-content|copy-item|cp|copy|expand-archive|compress-archive)\b", "edit",
     "Creating {}", "Creating files"),
    (r"\b(get-process|get-service|get-computerinfo|systeminfo|get-ciminstance|get-wmiobject)\b", "run",
     "Checking the PC", "Checking the PC"),
)


def _ps_target(cmd: str) -> str:
    """The first path-like or quoted argument of a command, as a short name."""
    m = re.search(r"https?://[^\s'\"]+", cmd)
    if m:
        return _name(m.group(0))
    m = re.search(r"['\"]([^'\"]*[\\/.][^'\"]*)['\"]", cmd)
    if m:
        return _name(m.group(1))
    m = re.search(r"(?<![\w-])([A-Za-z]:\\[^\s'\"|;]+|[\w~.-]+\.[A-Za-z0-9]{1,5})(?![\w])", cmd)
    return _name(m.group(1)) if m else ""


def agent_step(name, args) -> dict:
    try:
        args = args if isinstance(args, dict) else {}
        if name == "read_file":
            n = _name(args.get("path"))
            return _done("read", "Reading " + n if n else "Reading a file")
        if name == "write_file":
            n = _name(args.get("path"))
            return _done("edit", "Saving " + n if n else "Saving a file")
        if name == "delete_to_recycle_bin":
            n = _name(args.get("path"))
            return _done("edit", "Moving " + n + " to the Recycle Bin" if n else "Moving a file to the Recycle Bin")
        if name == "open_website":
            n = _name(args.get("url"))
            return _done("web", "Opening " + n if n else "Opening a website")
        if name == "run_powershell":
            cmd = str(args.get("command") or "")
            low = cmd.lower()
            for pattern, kind, with_target, alone in _PS_RULES:
                if re.search(pattern, low):
                    target = _ps_target(cmd)
                    return _done(kind, with_target.format(target) if target and "{}" in with_target else alone)
            return _done("run", "Running a command")
        return _done("work", "Working on it")
    except Exception:
        return {"kind": "work", "text": "Working on it"}


def claude_step(line):
    """Step line for an assistant message that calls a tool (stream-json), or None."""
    try:
        obj = json.loads(line)
    except Exception:
        return None
    if not isinstance(obj, dict) or obj.get("type") != "assistant":
        return None
    msg = obj.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if not isinstance(content, list):
        return None
    for block in content:
        if not (isinstance(block, dict) and block.get("type") == "tool_use"):
            continue
        tool = block.get("name")
        inp = block.get("input") if isinstance(block.get("input"), dict) else {}
        if tool == "Read":
            n = _name(inp.get("file_path"))
            return _done("read", "Reading " + n if n else "Reading a file")
        if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            n = _name(inp.get("file_path") or inp.get("notebook_path"))
            return _done("edit", ("Saving " if tool == "Write" else "Editing ") + n if n else "Editing a file")
        if tool in ("Glob", "Grep"):
            return _done("search", "Searching the files")
        if tool in ("WebFetch", "WebSearch"):
            n = _name(inp.get("url")) if tool == "WebFetch" else ""
            return _done("web", "Looking at " + n if n else "Looking it up online")
        if tool in ("Bash", "PowerShell"):
            cmd = str(inp.get("command") or "").lower()
            if "runner.py" in cmd:
                return _done("build", "Building and testing a piece")
            if "do.bat" in cmd:
                return _done("work", "Handing a task to the helper")
            return _done("run", "Running a command")
        if tool == "Task":
            return _done("plan", "Planning the steps")
        return _done("work", "Working on it")
    return None


HOUSEKEEPING = ("ToolSearch", "TodoWrite")  # internal bookkeeping: never shown as a step


def housekeeping_calls(line):
    """(only_housekeeping, ids) for a stream-json assistant line: True when every tool call in it is internal
    bookkeeping, plus the ids of those calls (so their results can be hidden too)."""
    try:
        obj = json.loads(line)
        msg = obj.get("message") if isinstance(obj, dict) and obj.get("type") == "assistant" else None
        content = msg.get("content") if isinstance(msg, dict) else None
        blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"] if isinstance(content, list) else []
    except Exception:
        return False, []
    ids = [b.get("id") for b in blocks if b.get("name") in HOUSEKEEPING and isinstance(b.get("id"), str)]
    return bool(blocks) and all(b.get("name") in HOUSEKEEPING for b in blocks), ids

