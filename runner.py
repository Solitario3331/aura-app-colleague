"""Qwen builds, tests decide, Claude only steps in after repeated failure.

Usage:  python runner.py tasks/hello_prompt
Each task folder needs: spec.md (what to build) and test_task.py (pytest tests).
Qwen's code is saved to src/<task_name>.py and the tests import it as `<task_name>`.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

def _ollama_url() -> str:
    try:
        sys.path.insert(0, str(Path(__file__).parent / "src"))
        from ollama_addr import read_address
        return read_address(Path(__file__).parent)
    except Exception:
        pass
    try:
        f = Path(__file__).parent / "ollama_url.txt"
        if f.exists():
            u = f.read_text(encoding="utf-8-sig").strip()
            if u.lower().startswith(("http://", "https://")):
                return u
    except OSError:
        pass
    return os.environ.get("OLLAMA_URL", "http://localhost:11434")


# ---- CONFIG: edit these two lines ------------------------------------------
OLLAMA_URL = _ollama_url()
QWEN_MODEL = (os.environ.get("QWEN_MODEL") or (Path(__file__).parent / "model.txt").read_text(encoding="utf-8").strip())
# -----------------------------------------------------------------------------
SUMMARY_MODEL = (os.environ.get("SUMMARY_MODEL") or (Path(__file__).parent / "model.txt").read_text(encoding="utf-8").strip())  # summarizes when context fills up
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")
MAX_QWEN_ATTEMPTS = 3
NUM_CTX = int(os.environ.get("QWEN_NUM_CTX", "65536"))  # Qwen context window: 64k
CTX_LIMIT = 0.8  # when a prompt fills 80% of the window, summarize and start fresh

ROOT = Path(__file__).parent
SRC = ROOT / "src"
LOGS = ROOT / "logs"
SRC.mkdir(exist_ok=True)
LOGS.mkdir(exist_ok=True)

sys.path.insert(0, str(SRC))
try:
    from no_window import child_python, hidden as _hidden  # no console windows popping up
except Exception:
    def _hidden():
        return {}

    def child_python():
        return sys.executable
PY = child_python()

SYSTEM = (
    "You write Python code. Reply with ONE complete Python file inside a single "
    "```python code block and nothing else. Follow the spec exactly."
)


def extract_code(text: str) -> str:
    m = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"


def ask_qwen(prompt: str, model: str = "", system: str = "") -> str:
    r = requests.post(
        f"{OLLAMA_URL}/api/chat",
        json={
            "model": model or QWEN_MODEL,
            "stream": False,
            "think": False,
            "options": {"temperature": 0.2, "num_ctx": NUM_CTX},
            "messages": [
                {"role": "system", "content": system or SYSTEM},
                {"role": "user", "content": prompt},
            ],
        },
        timeout=(10, 900),
    )
    r.raise_for_status()
    return r.json()["message"]["content"]


def ask_claude(prompt: str) -> str:
    import anthropic  # only imported when escalating

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


def ask_claude_cli(prompt: str) -> str | None:
    """Escalate with Claude Code in non-interactive mode (`claude -p`).
    Uses the user's normal Claude login (no API key). Returns None if unavailable."""
    import shutil
    import tempfile
    exe = shutil.which("claude")
    if not exe or os.environ.get("CLAUDE_ESCALATE", "1") == "0":
        return None
    try:
        r = subprocess.run(
            [exe, "-p", "--model", CLAUDE_MODEL, "Do not use any tools. " + SYSTEM + "\n\n" + prompt],
            capture_output=True, text=True, timeout=600,
            cwd=tempfile.gettempdir(),  # avoid loading the project's CLAUDE.md
            **_hidden(),
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    return r.stdout if r.returncode == 0 and r.stdout.strip() else None


def run_checks(task_dir: Path, name: str) -> tuple[bool, str]:
    """Free checks: syntax/lint, then the tests. No AI involved."""
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    out = ""
    lint = subprocess.run(
        [PY, "-m", "ruff", "check", "--select", "E9,F", str(SRC / f"{name}.py")],  # only real errors, not style
        capture_output=True, text=True, **_hidden(),
    )
    if lint.returncode != 0 and "No module named ruff" not in lint.stderr:
        out += "LINT ERRORS:\n" + lint.stdout + lint.stderr
    cmd = [PY, "-m", "pytest", "-x", "-q", str(task_dir / "test_task.py")]
    try:  # a test run can hang (endless loop in the new code): stop it after 5 minutes
        from no_window import run_capture
        t_out, t_err, t_code, reason = run_capture(cmd, 300, env=env)
        if reason == "timeout":
            t_err += "\nThe tests took longer than 5 minutes and were stopped (endless loop?)."
            t_code = 1
    except ImportError:
        test = subprocess.run(cmd, capture_output=True, text=True, env=env, **_hidden())
        t_out, t_err, t_code = test.stdout, test.stderr, test.returncode
    out += "\nTEST OUTPUT:\n" + t_out[-3000:] + t_err[-1000:]
    return (lint.returncode == 0 or "No module named ruff" in lint.stderr) and t_code == 0, out


def log(entry: dict) -> None:
    entry["time"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOGS / "attempts.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def main(task_path: str) -> None:
    task_dir = ROOT / task_path
    name = task_dir.name
    spec = (task_dir / "spec.md").read_text(encoding="utf-8")
    tests = (task_dir / "test_task.py").read_text(encoding="utf-8")
    target = SRC / f"{name}.py"

    base = (
        f"SPEC:\n{spec}\n\nThe code will be saved as module `{name}` and must pass "
        f"these tests:\n```python\n{tests}\n```"
    )
    prompt = base
    for attempt in range(1, MAX_QWEN_ATTEMPTS + 1):
        print(f"[Qwen] attempt {attempt}/{MAX_QWEN_ATTEMPTS}...")
        code = extract_code(ask_qwen(prompt))
        target.write_text(code, encoding="utf-8")
        ok, report = run_checks(task_dir, name)
        # Save every attempt so a supervisor (Claude Code) can read it live.
        (LOGS / f"{name}_attempt{attempt}.py").write_text(code, encoding="utf-8")
        (LOGS / f"{name}_attempt{attempt}_report.txt").write_text(report, encoding="utf-8")
        print(f"[Qwen] attempt {attempt} {'PASSED' if ok else 'FAILED'}", flush=True)
        log({"task": name, "who": "qwen", "attempt": attempt, "passed": ok})
        if ok:
            print(f"PASSED by Qwen on attempt {attempt}. Saved: {target}")
            return
        print(report[-800:])
        prompt = (
            base + f"\n\nYour previous code:\n```python\n{code}\n```\n"
            f"It failed with:\n{report}\nFix it and return the full file."
        )
        # Context nearly full (rough estimate: 1 token ~ 3.5 characters)?
        # Then summarize the failure with Qwen itself (free) and start with a fresh, small prompt.
        if len(prompt) / 3.5 > NUM_CTX * CTX_LIMIT:
            print("[Qwen] context almost full: summarizing and starting fresh...", flush=True)
            notes = ask_qwen(
                "Summarize in under 15 short lines: what is wrong with this code and what "
                f"must change to pass the tests.\n\nERRORS:\n{report[-4000:]}",
                model=SUMMARY_MODEL, system="You write short, clear summaries. Plain text only.",
            )
            prompt = (base + f"\n\nYour previous code:\n```python\n{code}\n```\n"
                      f"Notes from the last failed attempt:\n{notes}\nFix the code and return the full file.")

    # Qwen failed 3 times: try Claude automatically through Claude Code (`claude -p`).
    print("[Claude] Qwen failed 3 times, asking Claude Code to fix it...", flush=True)
    answer = ask_claude_cli(prompt)
    if answer:
        fixed = extract_code(answer)
        target.write_text(fixed, encoding="utf-8")
        ok, report = run_checks(task_dir, name)
        log({"task": name, "who": "claude", "attempt": 1, "passed": ok})
        if ok:
            print(f"PASSED after Claude fixed it. Saved: {target}")
            return
        code = fixed
        print("Claude's fix did not pass either.")
    # Last resort: save a failure report you can paste into the Claude chat.
    log({"task": name, "who": "needs_claude", "attempt": MAX_QWEN_ATTEMPTS, "passed": False})
    report_file = LOGS / f"{name}_FAILED.md"
    report_file.write_text(
        f"# Qwen failed task `{name}` {MAX_QWEN_ATTEMPTS} times\n\n"
        f"## Spec\n{spec}\n\n## Tests\n```python\n{tests}\n```\n\n"
        f"## Qwen's last code\n```python\n{code}\n```\n\n## Errors\n```\n{report}\n```\n",
        encoding="utf-8",
    )
    print(f"Qwen failed {MAX_QWEN_ATTEMPTS} times. Paste this file into the Claude chat:\n  {report_file}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python runner.py tasks/<task_name>")
    main(sys.argv[1])
