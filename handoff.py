"""Qwen (free) writes the session handoff into PROGRESS.md, so Claude does not spend tokens summarizing.
Usage: python handoff.py   (then start a fresh Claude chat: it reads PROGRESS.md automatically)."""
import json
import os
import re
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


OLLAMA_URL = _ollama_url()
MODEL = (os.environ.get("SUMMARY_MODEL") or (Path(__file__).parent / "model.txt").read_text(encoding="utf-8").strip())
NUM_CTX = int(os.environ.get("QWEN_NUM_CTX", "65536"))
ROOT = Path(__file__).parent
PROJ = Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(ROOT.resolve()))  # this folder's chats
CHUNK = 60000  # characters per Qwen call


def ask(prompt: str) -> str:
    r = requests.post(f"{OLLAMA_URL}/api/chat", timeout=(10, 900), json={
        "model": MODEL, "stream": False, "think": False,
        "options": {"temperature": 0.2, "num_ctx": NUM_CTX},
        "messages": [{"role": "system", "content": "You write short, clear project notes. Plain text, bullet points."},
                     {"role": "user", "content": prompt}]})
    r.raise_for_status()
    return r.json()["message"]["content"].strip()


def conversation(path: Path) -> str:
    """Only what the user and Claude said (no tool output, no file contents)."""
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") not in ("user", "assistant"):
            continue
        c = d.get("message", {}).get("content")
        if isinstance(c, list):
            c = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
        if isinstance(c, str) and c.strip() and not c.lstrip().startswith("<system-reminder>"):
            out.append(f"{d['type'].upper()}: {c.strip()[:1500]}")
    return "\n".join(out)


def main() -> None:
    # Inside the Aura app the app does its own per-chat handoff (--print); the terminal's PROGRESS.md stays untouched.
    if os.environ.get("AURA_APP") and "--print" not in sys.argv:
        return
    files = sorted(PROJ.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if "--hook" in sys.argv:  # Claude Code passes the exact transcript on stdin
        try:
            tp = json.loads(sys.stdin.read()).get("transcript_path")
            if tp and Path(tp).exists():
                files = [Path(tp)]
        except Exception:
            pass
    if not files:
        sys.exit("No Claude session found.")
    text = conversation(files[-1])
    if not text:
        sys.exit("Nothing to summarize.")
    parts = [text[i:i + CHUNK] for i in range(0, len(text), CHUNK)]
    notes = [ask("Summarize this part of a chat between a user and Claude in under 20 short bullets: "
                 f"what was done, decisions made, open problems, next steps.\n\n{p}") for p in parts]
    final = notes[0] if len(notes) == 1 else ask(
        "Merge these notes into ONE summary under 20 bullets (done, decisions, open problems, next steps):\n\n"
        + "\n\n".join(notes))
    if "--print" in sys.argv:  # the app wants the summary text, not a PROGRESS.md edit
        print(final)
        return
    pf = ROOT / "PROGRESS.md"
    old = pf.read_text(encoding="utf-8") if pf.exists() else ""
    old = re.sub(r"\n## Session handoff by Qwen.*?(?=\n## |\Z)", "", old, flags=re.S).rstrip() + "\n"  # keep only the newest handoff
    with open(pf, "w", encoding="utf-8") as f:
        f.write(old)
        f.write(f"\n## Session handoff by Qwen ({time.strftime('%Y-%m-%d %H:%M')})\n{final}\n")
    print("Done. Handoff added to PROGRESS.md. Now start a fresh chat (/clear).")


if __name__ == "__main__":
    main()
