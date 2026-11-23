import json


def tool_kind_from_line(line):
    try:
        obj = json.loads(line)
    except Exception:
        return None

    if not isinstance(obj, dict):
        return None

    if obj.get("type") != "assistant":
        return None

    message = obj.get("message")
    if not isinstance(message, dict):
        return None

    content = message.get("content")
    if not isinstance(content, list):
        return None

    first_tool_use = None
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            first_tool_use = block
            break

    if first_tool_use is None:
        return None

    name = first_tool_use.get("name")
    if not isinstance(name, str):
        return "work"

    inp = first_tool_use.get("input")
    if not isinstance(inp, dict):
        inp = {}

    if name == "Read":
        return "read"
    if name in ("Glob", "Grep"):
        return "search"
    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return "edit"
    if name in ("WebFetch", "WebSearch"):
        return "web"
    if name == "Task":
        return "plan"
    if name in ("Bash", "PowerShell"):
        command = inp.get("command")
        if not isinstance(command, str):
            command = ""
        command_lower = command.lower()
        if "runner.py" in command_lower:
            return "build"
        if "polish" in command_lower:
            return "build"
        if "do.bat" in command_lower:
            return "work"
        return "run"
    return "work"
