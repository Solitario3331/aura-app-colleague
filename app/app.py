"""Aura desktop app: the Python side (window, chats, helpers, browser pane).

The window shows app/web/index.html. The page calls the public methods of `Api`
through window.pywebview.api, so their names must never change. Every public
method catches its own errors and returns a safe value, so the page never hangs.
Small tested helpers live in src/ (tests in tasks/).
"""
import base64
import ctypes
import functools
import http.server
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import uuid
import webbrowser
import zipfile
from pathlib import Path

if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).parent
else:
    ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "src"))

import chat_store
import live_state
import no_window
import ollama_addr
import updater
from aura_events import Translator
from aura_mask import mask_names
from chat_context import build_context, strip_tag
from frame_policy import can_embed
from site_finder import newest_html
from step_text import claude_step, housekeeping_calls
from detail_text import clip as clip_detail, claude_detail, claude_result, result_tool_ids, MAX_DETAIL, MAX_OUT
from tool_kind import tool_kind_from_line
from url_input import normalize_input

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
CHATS_DIR = ROOT / "chats"
DROP_DIR = CHATS_DIR / "_dropped"       # copies of dropped files when the page cannot tell their real location
PICS_DIR = CHATS_DIR / "_pics"          # the app's own copies of sent pictures (kept two days)
LOG_FILE = ROOT / "logs" / "aura_app.log"
CTL_FILE = ROOT / "logs" / "aura_ctl.json"
RESTART_READY = ROOT / "logs" / "aura_restart_ready.txt"   # the new Aura writes this once it has started fine
RESTART_WAIT_SECONDS = 45               # how long the old Aura waits for the new one before giving up

AURA_MODEL = "claude-sonnet-5-5"        # Aura mode: Sonnet + the local helper (streaming chat)
QUICK_MODEL = "claude-haiku-5-5"        # Quick mode: Haiku + the local helper (streaming chat, never thinks)
HELPER_MODEL = "claude-sonnet-5-5"      # old one-shot Quick fallback (kept for the job path)
POLISH_MODEL = "claude-opus-5-5"        # Polish mode
THINK_LEVELS = ("off", "low", "medium", "high")
_THINK = {"level": "medium"}            # the Thinking switch in the page (saved by the page, sent on start)

MODES = ("claude", "qwen", "auto", "polish")
IDLE_STOP_SECONDS = 20 * 60             # stop a chat's background process after 20 idle minutes
FILLER_STEPS = ("working on it", "working on this", "completed a task", "on it")  # never shown as a step line
LOCAL_TIMEOUT = 15 * 60                # Local/Quick: no answer after 15 minutes -> stop and say so
LOCAL_DEADLINE = LOCAL_TIMEOUT - 60     # the local helper itself wraps up (short summary) a minute before that
CLAUDE_TIMEOUTS = {"auto": 10 * 60, "big": 30 * 60, "polish": 20 * 60, "claude": 10 * 60}
AURA_SILENT_SECONDS = 15 * 60           # Aura mode: a turn with no sign of life for 15 minutes is stopped
PROGRESS_PREFIX = "@@aura "             # live step lines from agent.py (never shown as text)
WORKING_ELSEWHERE = "This chat is still working. Wait for it to finish, or press Stop."
CHIP_TEXT = {"read": "Read files", "search": "Searched", "edit": "Edited files", "run": "Ran a command",
             "work": "Completed a task", "build": "Built and tested", "web": "Looked something up",
             "plan": "Planned the steps"}
PICS_KEEP_SECONDS = 2 * 86400
MAX_ARG_CHARS = 30000                   # Windows command lines are limited to ~32k characters
SITE_SKIP = ("node_modules", "vendor", "__pycache__", "venv", "site-packages")
IMAGE_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
STEP_LINE = re.compile(r"^\[\d+\] [A-Za-z_]+: ")   # agent.py step lines, never shown in the chat
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"

NOT_SET_UP = ("Aura's main helper is not installed on this PC yet. Double-click install.bat in the Aura folder "
              "to finish the setup, then restart Aura.")
NOT_SIGNED_IN = ("Aura is not signed in on this PC yet. Use the sign-in / terminal button in Aura (or run install.bat "
                 "again), sign in with your account, then try again.")
TRY_AGAIN = "Aura could not finish this one. Please try again."
CANT_START = "Aura could not start. Please try again in a moment."
INTERNAL_ERROR = "Something went wrong inside Aura. Please try again."

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

AURA_PERSONA = " ".join((
    "You are Aura, a friendly personal assistant app. Your name is Aura.",
    "Never mention or hint at Claude, Anthropic, Qwen, Ollama, Opus, Sonnet, Haiku, model names,",
    "other AIs, local models, tools, hooks, or how you work inside.",
    "If asked who or what you are, say you are Aura, built by Amar & Arry",
    "(if asked who made or built you, answer: \"I'm Aura, built by Amar & Arry.\").",
    "If anyone asks who Amar or Arry are, or asks anything about them, reply ONLY with a short cute line",
    "saying they are two lovebirds making something cute together (add a heart), with no other details about them.",
    "You have FULL permission on this PC, like a terminal with everything approved: read, edit and create files,",
    "run any command, install things. Never ask the user to approve a command or to run something themselves;",
    "just do it. Deletes go to the Recycle Bin (restorable). When you did something on the PC, say what you did",
    "in plain simple words. Follow the CLAUDE.md rules for who does the work.",
    "Answer fully and clearly when the question needs it (explanations, plans, code, steps, comparisons) with",
    "markdown headings, lists and code blocks; answer briefly only when a short answer is enough.",
    "When you need the user to choose between options, do NOT use any question tool. Write the question, then",
    "a block exactly like:\n```choices\nOption one\nOption two\nOption three\n```\nOne option per line, 2 to 5 options;",
    "the user taps one and it comes back as their next message.",
    "You CAN look at your own window, including the preview pane and whether a video is playing: run do.bat with the question and the extra option --see-aura, "
    "for example do.bat \"What is shown in the Aura window? Is a video playing?\" --see-aura. Do this to check what you opened instead of saying you cannot see it.",
    "To play, pause, resume or mute the video in the Aura browser, run do.bat \"pause the video\" (or play / resume / mute). It really controls the player and reports the true state. "
    "Never tell the user you paused or played something unless that result says so.",
    "To PLAY something on YouTube run do.bat \"play <what> on YouTube\": it picks a video that really plays, opens it in Aura and checks the screen itself, "
    "and tries another result if one shows an error. Never open a YouTube link yourself without that. If you open anything else and it matters that it worked, check with --see-aura.",
    "To open a website, use do.bat (it opens the site inside Aura); never start a browser yourself. A website you built is ALREADY shown in the Aura preview pane: never open it in the normal browser, never run start/Start-Process/explorer on it, and do not say it was opened in the browser unless the user asked.",
    "Open a website ONLY when the user asks to browse, open a site or look something up online;",
    "for 'show me', 'give me' or any request for information, a recipe or an answer, write the answer in the chat.",
    "For facts that may be recent, specific or easy to get wrong (news, shows, films, books, people, prices, versions,",
    "sports results, anything you are not sure about), look them up on the web first and answer from what you found;",
    "say in plain words that you checked online. Never answer such facts from memory when you could check.",
    "If the user wants no spoilers, still check, but only report what they asked for.",
    "Never explain why you did or did not look something up, never say you answered from memory or training,",
    "and never describe your tools or how you work; just give the answer.",
    "NEVER end an answer with a list of sources, links, references or citations, and do not put links in the answer",
    "unless the user asks for a link.",
))

AURA_ALLOWED = [
    "Read", "Glob", "Grep", "WebSearch", "WebFetch",
    "Bash(do.bat:*)", "Bash(./do.bat:*)", "Bash(cmd.exe /c do.bat:*)",
    "PowerShell(.\\do.bat:*)", "PowerShell(do.bat:*)", "PowerShell(cmd /c do.bat:*)",
    "Bash(python runner.py:*)", "Bash(.venv/Scripts/python.exe runner.py:*)",
    "PowerShell(.venv\\Scripts\\python.exe runner.py:*)",
]
ONE_SHOT_ALLOWED = ["Bash(do.bat:*)", "Bash(python runner.py:*)", "Bash(.venv/Scripts/python.exe runner.py:*)"]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _log(text: str) -> None:
    """Technical details go to a log file, never to the screen."""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(text).rstrip() + "\n")
    except OSError:
        pass


def _needs_login(text: str) -> bool:
    """A short error saying the Claude login is missing/expired (long texts are real answers, never matched)."""
    low = (text or "").lower()
    return len(low) < 600 and any(k in low for k in (
        "run /login", "not logged in", "invalid api key", "please log in", "oauth token has expired"))


def _friendly(text: str) -> str:
    """Turn raw output into something safe to show: names masked, known failures explained."""
    text = text or ""
    low = text.lower()
    short = len(low) < 600   # error messages are short; long texts are real answers
    if _needs_login(text):
        _log("claude not signed in: " + text[-500:])
        return NOT_SIGNED_IN
    if short and "ollama" in low and "not found" in low and "model" in low:
        _log("local model missing: " + text[-500:])
        return ("Aura's local helper is reachable, but its brain (model) is not installed there yet. "
                "Run install.bat again, or ask the person who set up Aura.")
    if short and "ollama took too long" in low:
        _log("local helper slow: " + text[-500:])
        return "Aura's local helper is very busy right now. Please try again in a few minutes."
    if short and "ollama returned an error" in low:
        _log("local helper error: " + text[-500:])
        return "Aura's local helper had a problem with this request. Please try again in a moment."
    if "11434" in low or ("ollama" in low and ("connect" in low or "refused" in low or "not found" in low)):
        _log("local helper unreachable: " + text[-500:])
        if ollama_addr.is_this_pc(ollama_addr.read_address(ROOT)):
            return "Aura's local helper is not running on this PC. Start Ollama (or restart the PC), then try again."
        return "Can't reach Amar's PC (the one with the big graphics card). Is it on? Then try again."
    if "traceback (most recent call last)" in low:
        _log("traceback: " + text[-3000:])
        return INTERNAL_ERROR
    return mask_names(text)


def _clean_env(level=None) -> dict:
    """Environment for helper programs: no settings leaking in from a parent Claude session."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE_CODE", "CLAUDECODE", "QWEN_MODEL"))}
    env.update(AURA_APP="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    env.setdefault("AGENT_DEADLINE", "540")   # a local task started by Aura mode (do.bat) wraps up within 9 minutes
    env.pop("AGENT_THINK", None)
    env.pop("MAX_THINKING_TOKENS", None)
    if (level or _THINK["level"]) == "off":   # Thinking switched off: Claude and the local helper both skip it
        env.update(MAX_THINKING_TOKENS="0", AGENT_THINK="off")
    for k in ("AURA_PROGRESS",):               # live step lines only where the app asks for them
        env.pop(k, None)
    return env


def _hidden() -> dict:
    """subprocess options: no console window and no 'working in background' busy cursor (src/no_window.py)."""
    return no_window.hidden()


CHILD_PY = no_window.child_python()     # python.exe, never pythonw.exe: pythonw's own children would flash windows


def _kill_tree(proc) -> None:
    """Stop a helper process and everything it started (Windows keeps children alive otherwise)."""
    no_window.kill_tree(proc)


# Chat files are read and written from several threads (a chat answering in the background while the page
# loads another one): one lock keeps every read-modify-write whole.
_STORE = threading.RLock()


def _store_add(sid, role, text) -> None:
    with _STORE:
        chat_store.add_message(CHATS_DIR, sid, role, text)


def _store_load(sid) -> dict:
    with _STORE:
        return chat_store.load_session(CHATS_DIR, sid)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""
    except OSError:
        return ""


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _side_file(sid, ext: str) -> Path:
    """Per-chat side files: .claude (conversation id), .summary (handoff), .site (its website)."""
    return CHATS_DIR / (str(sid) + ext)


def _valid_pictures(images) -> list:
    return [im for im in (images or []) if isinstance(im, dict) and im.get("data")]


def _user_line(text: str, images: list) -> str:
    """How a user message is stored in the chat history."""
    return (text or "") + (" [" + str(len(images)) + " picture(s)]" if images else "")


def _save_pictures(images: list, folder: Path) -> list:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for im in images:
        path = folder / (uuid.uuid4().hex + IMAGE_EXT.get(im.get("media_type"), ".png"))
        path.write_bytes(base64.b64decode(im["data"]))
        paths.append(str(path))
    return paths


def _prune_old_pictures() -> None:
    try:
        now = time.time()
        for old in PICS_DIR.glob("*"):
            try:
                if now - old.stat().st_mtime > PICS_KEEP_SECONDS:
                    old.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _history(sid, since_tag=None) -> str:
    """What was already said in this chat (maybe in another mode), so the current mode knows."""
    try:
        messages = [m for m in _store_load(sid)["messages"]   # Aura's own notes/errors are not conversation
                    if not (m.get("role") == "assistant" and strip_tag(m.get("text", ""))[0] in ("App", "Note"))]
        return build_context(messages, since_tag=since_tag) if since_tag else build_context(messages)
    except Exception:
        return ""


def _local_version() -> str:
    return _read_text(ROOT / "VERSION") or "0.1.0"


LOCAL_ONLY = ("update.json", "theme.txt", "ollama_url.txt")   # per-PC files an update must never overwrite
DEFAULT_UPDATE = {"repo":"Solitario3331/aura-app", "branch": "main", "token": ""}


def _update_config() -> dict:
    """Where updates come from: built in, so a copy without update.json still updates."""
    cfg = dict(DEFAULT_UPDATE)
    try:
        with open(ROOT / "update.json", "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in ("repo", "branch", "token"):
                v = data.get(k)
                if isinstance(v, str) and v.strip() and "OWNER" not in v:
                    cfg[k] = v.strip()
    except (OSError, ValueError):
        pass
    return cfg


def _is_offline_error(exc) -> bool:
    """True when the internet (or GitHub) could not be reached at all, as opposed to a server error."""
    try:
        import requests
        return isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout))
    except Exception:
        return False


def _wait_for_old_aura(argv) -> bool:
    """After an update restart: tell the old Aura we started fine, then wait until it has closed, so the
    window titles, control door file and taskbar entry are ours alone. Returns True if this is such a restart."""
    pid = 0
    for a in argv:
        if a.startswith("--wait-pid="):
            try:
                pid = int(a.split("=", 1)[1])
            except ValueError:
                pid = 0
    if "--updated" not in argv:
        return False
    try:
        RESTART_READY.parent.mkdir(parents=True, exist_ok=True)
        RESTART_READY.write_text(str(os.getpid()), encoding="utf-8")
    except OSError as exc:
        _log("restart: could not write ready file: " + repr(exc))
    if pid and os.name == "nt":
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = ctypes.c_void_p
        h = k32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if h:
            try:
                if k32.WaitForSingleObject(ctypes.c_void_p(h), 20000) != 0:
                    _log("restart: old Aura still running after 20 s, starting anyway")
            finally:
                k32.CloseHandle(ctypes.c_void_p(h))
    _unlink(RESTART_READY)
    return True


def _latest_commit(repo: str, branch: str) -> str:
    """Newest commit id of a public repo, or "". GitHub keeps branch files for about 5 minutes, but this answer
    is never cached, and files read at an exact commit are always the newest."""
    try:
        import requests
        r = requests.get("https://github.com/{}.git/info/refs?service=git-upload-pack".format(repo), timeout=10)
        m = re.search(rb"([0-9a-f]{40}) refs/heads/" + re.escape(branch.encode()) + rb"(?=[\n\x00])", r.content)
        return m.group(1).decode() if m else ""
    except Exception as exc:
        _log("latest commit lookup failed: " + repr(exc))
        return ""


def _github_headers(token: str) -> dict:
    headers = {"Accept": "application/vnd.github.raw"}
    if token:
        headers["Authorization"] = "Bearer " + token
    return headers


# ---------------------------------------------------------------------------
# Claude conversations
# ---------------------------------------------------------------------------
def _claude_session(sid) -> list:
    """Keep the same conversation for the whole chat. Returns the command line arguments."""
    f = _side_file(sid, ".claude")
    try:
        saved = _read_text(f)
        if saved.startswith("new:"):  # the previous resume failed: start this id fresh
            saved = saved[4:]
            f.write_text(saved, encoding="utf-8")
            return ["--session-id", saved]
        if saved:
            return ["--resume", saved]
        new = str(uuid.uuid4())
        f.write_text(new, encoding="utf-8")
        return ["--session-id", new]
    except OSError:
        return []


def _context_tokens(transcript: Path) -> int:
    """Size of the conversation: input tokens of the latest reply (same measure as the terminal's auto-clear).
    Only the end of the file is read, where the latest reply is."""
    last = 0
    try:
        with open(transcript, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 2_000_000))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return 0
    for line in tail.splitlines():
        if '"usage"' not in line:
            continue
        try:
            usage = json.loads(line).get("message", {}).get("usage")
        except (ValueError, AttributeError):
            continue
        if usage:
            last = (usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0))
    return last


def _claude_project_dir() -> Path:
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(ROOT))


def _effort_args(level=None) -> list:
    """Claude's effort flag from the Thinking switch ('off' is handled by the environment instead)."""
    level = level or _THINK["level"]
    return ["--effort", level] if level in ("low", "medium", "high") else []


def run_claude(prompt, model, extra=None, timeout=1800, should_stop=None, effort=None):
    """One-shot answer (Big project, Polish, Quick fallback). Returns None when Claude is not installed.
    Never hangs: the whole process tree is stopped on timeout or when should_stop() says so."""
    exe = shutil.which("claude")
    if exe is None:
        return None
    try:
        out, err, code, reason = no_window.run_capture(
            [exe, "-p", "--model", model, "--permission-mode", "bypassPermissions",
             "--append-system-prompt", AURA_PERSONA,
             "--disallowedTools", "AskUserQuestion", "EnterPlanMode", "ExitPlanMode"]
            + _effort_args(effort) + (extra or []),
            timeout, cwd=str(ROOT), env=_clean_env(effort), input_text=prompt, should_stop=should_stop)
        if reason == "stopped":
            return "Stopped."
        if reason == "timeout":
            _log("claude -p ({}) stopped after {} s".format(model, timeout))
            return "Aura took too long ({} minutes) and stopped. Try a smaller request.".format(int(timeout // 60))
        out = (out or "").strip()
        if out:
            return NOT_SIGNED_IN if _needs_login(out) else _friendly(out)
        err = (err or "").strip()
        if err:
            _log("claude -p stderr (exit {}): {}".format(code, err[-3000:]))
            return NOT_SIGNED_IN if _needs_login(err) else TRY_AGAIN
        return "No answer."
    except Exception as exc:
        _log("run_claude failed: " + repr(exc))
        return TRY_AGAIN


# ---------------------------------------------------------------------------
# Windows window helpers (own copy of user32 with proper types, so 64-bit handles are safe)
# ---------------------------------------------------------------------------
SW_HIDE, SW_SHOW, SW_MINIMIZE, SW_MAXIMIZE, SW_RESTORE = 0, 5, 6, 3, 9
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x20
GWL_STYLE, GWL_EXSTYLE = -16, -20
WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = 0x00000080, 0x00040000
WS_POPUP, WS_CHILD, WS_CLIPCHILDREN = 0x80000000, 0x40000000, 0x02000000
WS_CAPTION, WS_THICKFRAME, WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00C00000, 0x00040000, 0x00020000, 0x00010000
VK_LBUTTON = 0x01
_U32 = None


def _user32():
    global _U32
    if _U32 is None:
        from ctypes import wintypes as w
        u = ctypes.WinDLL("user32")
        sigs = {
            "FindWindowExW": (w.HWND, [w.HWND, w.HWND, w.LPCWSTR, w.LPCWSTR]),
            "GetWindowThreadProcessId": (w.DWORD, [w.HWND, ctypes.POINTER(w.DWORD)]),
            "IsWindow": (w.BOOL, [w.HWND]),
            "IsWindowVisible": (w.BOOL, [w.HWND]),
            "IsZoomed": (w.BOOL, [w.HWND]),
            "ShowWindow": (w.BOOL, [w.HWND, ctypes.c_int]),
            "MoveWindow": (w.BOOL, [w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.BOOL]),
            "SetWindowPos": (w.BOOL, [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT]),
            "GetWindowRect": (w.BOOL, [w.HWND, ctypes.POINTER(w.RECT)]),
            "GetCursorPos": (w.BOOL, [ctypes.POINTER(w.POINT)]),
            "GetAsyncKeyState": (w.SHORT, [ctypes.c_int]),
            "GetWindowLongW": (w.LONG, [w.HWND, ctypes.c_int]),
            "SetWindowLongW": (w.LONG, [w.HWND, ctypes.c_int, w.LONG]),
            "SetParent": (w.HWND, [w.HWND, w.HWND]),
            "LoadImageW": (w.HANDLE, [w.HINSTANCE, w.LPCWSTR, w.UINT, ctypes.c_int, ctypes.c_int, w.UINT]),
            "SendMessageW": (w.LPARAM, [w.HWND, w.UINT, w.WPARAM, w.LPARAM]),
            "GetSystemMetrics": (ctypes.c_int, [ctypes.c_int]),
            "GetDpiForSystem": (w.UINT, []),
        }
        for name, (res, args) in sigs.items():
            fn = getattr(u, name)
            fn.restype, fn.argtypes = res, args
        _U32 = u
    return _U32


def _as_long(value: int) -> int:
    """Window styles are 32-bit: keep the value inside the signed range SetWindowLongW accepts."""
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def _find_window(title: str):
    """Top-level window of THIS app with exactly this title (ignores other programs' windows)."""
    from ctypes import wintypes as w
    u = _user32()
    pid, hwnd = os.getpid(), None
    for _ in range(64):
        hwnd = u.FindWindowExW(None, hwnd, None, title)
        if not hwnd:
            return None
        owner = w.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            return hwnd
    return None


def _wait_for_window(title: str, tries: int, pause: float):
    for _ in range(tries):
        hwnd = _find_window(title)
        if hwnd:
            return hwnd
        time.sleep(pause)
    return None


# ---------------------------------------------------------------------------
# Local web servers: website preview + control door for helper programs
# ---------------------------------------------------------------------------
class _PreviewHandler(http.server.SimpleHTTPRequestHandler):
    """Serves one website folder on 127.0.0.1 only, never cached, no console noise."""

    def log_message(self, *args):
        pass

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


_MEDIA_FILES: dict = {}
_MEDIA_SRV: dict = {}


class _MediaHandler(http.server.SimpleHTTPRequestHandler):
    """Serves files that Aura registered (by a random token) on 127.0.0.1 only. Supports Range requests, which
    video players need for seeking. Nothing else on the disk can be reached."""

    def log_message(self, *args):
        pass

    def translate_path(self, path):
        parts = urllib.parse.unquote(path.split("?", 1)[0]).split("/")
        token = parts[2] if len(parts) > 2 and parts[1] == "m" else ""
        return _MEDIA_FILES.get(token, os.devnull if os.name != "nt" else "NUL_NOT_A_FILE")

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        if not getattr(self, "_partial", False):
            self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def send_head(self):
        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            self.send_error(404)
            return None
        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d*)-(\d*)$", (rng or "").strip())
        if not m or not (m.group(1) or m.group(2)):
            return super().send_head()
        size = os.path.getsize(path)
        if m.group(1) == "":
            start, end = max(0, size - int(m.group(2))), size - 1
        else:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            self.send_error(416)
            return None
        f = open(path, "rb")
        f.seek(start)
        self._left, self._partial = end - start + 1, True
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        return f

    def copyfile(self, source, outputfile):
        try:
            left = getattr(self, "_left", None)
            if left is None:
                return super().copyfile(source, outputfile)
            while left > 0:
                buf = source.read(min(65536, left))
                if not buf:
                    break
                outputfile.write(buf)
                left -= len(buf)
        except (ConnectionError, OSError):
            pass   # the player closed the connection (it moved the slider): normal


def _media_url(path: Path) -> str:
    if "srv" not in _MEDIA_SRV:
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _MediaHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        _MEDIA_SRV["srv"] = srv
    token = None
    for t, f in _MEDIA_FILES.items():
        if f == str(path):
            token = t
            break
    if token is None:
        token = secrets.token_urlsafe(12)
        _MEDIA_FILES[token] = str(path)
    return "http://127.0.0.1:%d/m/%s/%s" % (_MEDIA_SRV["srv"].server_address[1], token, urllib.parse.quote(path.name))


def _capture_aura_window(dest: Path) -> bool:
    """Save a picture of this Aura window (everything in it, including the preview pane) as a PNG. Windows only."""
    import struct
    import zlib
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    try:
        u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # real pixels, not scaled ones (else the picture is cropped)
    except Exception:
        pass
    found = []
    pid = os.getpid()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def each(hwnd, _):
        owner = ctypes.c_ulong(0)
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        buf = ctypes.create_unicode_buffer(64)
        u.GetWindowTextW(hwnd, buf, 64)
        if owner.value == pid and buf.value == "Aura" and u.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    u.EnumWindows(each, 0)
    if not found:
        return False
    hwnd = found[0]

    class RECT(ctypes.Structure):
        _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long), ("r", ctypes.c_long), ("b", ctypes.c_long)]

    rc = RECT()
    u.GetWindowRect(hwnd, ctypes.byref(rc))
    w, h = rc.r - rc.l, rc.b - rc.t
    if w < 50 or h < 50:
        return False
    sdc = u.GetDC(None)
    mdc = g.CreateCompatibleDC(sdc)
    bmp = g.CreateCompatibleBitmap(sdc, w, h)
    old = g.SelectObject(mdc, bmp)
    try:
        u.PrintWindow(hwnd, mdc, 2)  # 2 = also draw the web content

        class BIH(ctypes.Structure):
            _fields_ = [("size", ctypes.c_uint32), ("w", ctypes.c_int32), ("h", ctypes.c_int32), ("planes", ctypes.c_uint16),
                        ("bits", ctypes.c_uint16), ("comp", ctypes.c_uint32), ("img", ctypes.c_uint32), ("xp", ctypes.c_int32),
                        ("yp", ctypes.c_int32), ("used", ctypes.c_uint32), ("imp", ctypes.c_uint32)]

        bi = BIH(ctypes.sizeof(BIH), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)  # negative height = top-down rows
        raw = ctypes.create_string_buffer(w * h * 4)
        g.GetDIBits(mdc, bmp, 0, h, raw, ctypes.byref(bi), 0)
    finally:
        g.SelectObject(mdc, old)
        g.DeleteObject(bmp)
        g.DeleteDC(mdc)
        u.ReleaseDC(None, sdc)
    data = raw.raw
    rows = []
    for y in range(h):  # BGRA -> RGB, one PNG row at a time
        row = data[y * w * 4:(y + 1) * w * 4]
        rgb = bytearray(w * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = row[2::4], row[1::4], row[0::4]
        rows.append(b"\x00" + bytes(rgb))

    def chunk(tag, body):
        c = struct.pack(">I", len(body)) + tag + body
        return c + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"".join(rows), 6)) + chunk(b"IEND", b""))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(png)
    return True


def _start_control(api_obj):
    """Tiny local door so helper programs (like the local assistant) can ask the app to open a web page.
    Needs a secret token, which is written to logs/aura_ctl.json for them."""
    token = secrets.token_urlsafe(16)
    home = Path.home().resolve()

    class _Ctl(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            code = 403
            try:
                u = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(u.query)
                url = q.get("url", [""])[0].strip()
                local = q.get("path", [""])[0].strip()
                if u.path == "/media" and secrets.compare_digest(q.get("token", [""])[0], token):
                    act = q.get("action", ["status"])[0]
                    if act in ("status", "play", "pause", "toggle", "mute", "unmute"):
                        body = json.dumps(api_obj._media(act)).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    code = 404
                elif u.path == "/shot" and secrets.compare_digest(q.get("token", [""])[0], token):
                    shot = ROOT / "logs" / "aura_shot.png"
                    code = 200 if _capture_aura_window(shot) else 404
                elif u.path == "/open" and secrets.compare_digest(q.get("token", [""])[0], token):
                    if url.lower().startswith(("http://", "https://")):
                        api_obj._queue_open(url)
                        code = 200
                    elif local:
                        code = 404
                        lp = Path(local).resolve()
                        if lp.is_file() and lp.suffix.lower() in (".html", ".htm") and home in lp.parents:
                            api_obj._queue_open(api_obj._serve_site(str(lp))["url"])
                            code = 200
            except Exception as exc:
                _log("control door: " + repr(exc))
                code = 404
            self.send_response(code)
            self.end_headers()

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Ctl)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    CTL_FILE.parent.mkdir(parents=True, exist_ok=True)
    CTL_FILE.write_text(json.dumps({"port": srv.server_address[1], "token": token}), encoding="utf-8")
    return srv


# ---------------------------------------------------------------------------
# The API the page talks to
# ---------------------------------------------------------------------------
class Api:
    def __init__(self):
        # Everything private starts with "_" so the page never sees it.
        self._chats = {}                     # sid -> running chat process state (Aura mode)
        self._chats_lock = threading.Lock()
        self._jobs = {}                      # sid -> one-shot job state (Quick, Local, Big project, Polish)
        self._jobs_lock = threading.Lock()
        self._local_lock = threading.Lock()  # one local-helper task at a time (one graphics card)
        self._reaper_on = False
        self._handoff_busy = set()
        self._handoff_lock = threading.Lock()
        self._servers = {}                   # website folder -> local preview server
        self._srv_lock = threading.Lock()
        self._open_q = []                    # web addresses waiting to be shown in the browser pane
        self._open_lock = threading.Lock()
        self._ov_lock = threading.Lock()     # the real browser view over the pane
        self._ov_win = None
        self._ov_hwnd = None
        self._hwnd = None                    # main window handle (cached)
        self._pty = None                     # hidden terminal mode
        self._buf = ""
        self._term_lock = threading.Lock()
        self._ctl_srv = None                 # control door server (set in main)
        self._upd_lock = threading.Lock()    # one update step at a time
        self._upd_files = None               # downloaded update, waiting to be installed
        self._restarting = False
        self._updated_to = ""                # set after an update restart: shown once as a toast

    # ---- chats list ----
    def get_version(self) -> str:
        try:
            return _local_version()
        except Exception:
            return "0.1.0"

    def list_sessions(self) -> list:
        try:
            with _STORE:
                return chat_store.list_sessions(CHATS_DIR, include_empty=False, with_time=True)
        except Exception as exc:
            _log("list_sessions failed: " + repr(exc))
            return []

    def _purge_empty(self, keep="") -> None:
        """Chats with no messages are never kept: delete them (except `keep` and anything still working)."""
        try:
            with _STORE:
                empties = chat_store.empty_session_ids(CHATS_DIR)
            if not empties:
                return
            busy = set(self.busy_chats())
            for sid in empties:
                if sid != keep and sid not in busy:
                    self.delete_session(sid)
        except Exception as exc:
            _log("purge_empty failed: " + repr(exc))

    def new_session(self) -> str:
        """A new empty chat. Returns its id, or "" if it could not be created."""
        try:
            self._purge_empty()                  # the empty chat you leave behind is never kept
            with _STORE:
                sid = chat_store.new_session(CHATS_DIR)
            return sid
        except Exception as exc:
            _log("new_session failed: " + repr(exc))
            return ""

    def load_session(self, sid) -> dict:
        try:
            return _store_load(sid)
        except Exception as exc:
            _log("load_session failed: " + repr(exc))
            return {"messages": [], "error": True}

    def open_chat(self, sid) -> dict:
        """A chat's messages plus, if it is still working, its live state (text so far, step lines, phase).
        Taken in one go under the chat's lock, so nothing is shown twice or lost when the answer finishes
        right at this moment. Waiting events of that chat are dropped (the snapshot replaces them)."""
        try:
            sid = str(sid)
            with self._chats_lock:
                st = self._chats.get(sid)
            with self._jobs_lock:
                job = self._jobs.get(sid)
            for state, kind in ((st, "aura"), (job, "job")):
                if not state:
                    continue
                with state["lock"]:
                    if state.get("busy"):
                        data = _store_load(sid)
                        del state["events"][:]
                        data["run"] = {"kind": kind, "mode": state.get("mode", "claude"),
                                       "elapsed": max(0.0, time.time() - state.get("started", time.time())),
                                       "phase": state.get("phase") or "think",
                                       "items": live_state.snapshot(state["live"])}
                        return data
            data = _store_load(sid)
            data["run"] = None
            return data
        except Exception as exc:
            _log("open_chat failed: " + repr(exc))
            return {"messages": [], "error": True, "run": None}

    def busy_chats(self) -> list:
        """Ids of chats that are working right now (the chat list shows a small indicator on them)."""
        try:
            with self._chats_lock:
                out = [sid for sid, st in self._chats.items() if st.get("busy")]
            with self._jobs_lock:
                out += [sid for sid, j in self._jobs.items() if j.get("busy") and sid not in out]
            return out
        except Exception:
            return []

    def delete_session(self, sid) -> bool:
        try:
            sid = str(sid)
            self._stop_chat(sid)
            self._stop_job(sid, quiet=True)
            with self._chats_lock:
                self._chats.pop(sid, None)
            with self._jobs_lock:
                self._jobs.pop(sid, None)
            with _STORE:
                ok = chat_store.delete_session(CHATS_DIR, sid)
            if ok:
                for ext in (".claude", ".summary", ".site"):
                    _unlink(_side_file(sid, ext))
            return ok
        except Exception as exc:
            _log("delete_session failed: " + repr(exc))
            return False

    # ---- window buttons (the page draws its own top strip instead of the native title bar) ----
    # ---- settings helpers ----
    def delete_all_sessions(self) -> int:
        """Delete every chat. Returns how many were removed."""
        n = 0
        try:
            for s in self.list_sessions():
                if self.delete_session(s.get("id")):
                    n += 1
            self._purge_empty()
        except Exception as exc:
            _log("delete_all_sessions failed: " + repr(exc))
        return n

    _IMG_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
                 ".webp": "image/webp", ".bmp": "image/bmp"}
    _VID_EXT = {".mp4", ".webm", ".mov", ".m4v", ".mkv"}

    def _resolve_media(self, path):
        """The real picture/video file for a path the assistant wrote (or None). Only inside the user folder, the
        project folder or the temp folder. A slightly wrong folder is fixed by looking for the same file name."""
        raw = str(path or "").strip()
        if raw.lower().startswith("file:///"):
            raw = raw[8:].replace("/", "\\")
        p = Path(raw).expanduser().resolve()
        if p.suffix.lower() not in self._IMG_MIME and p.suffix.lower() not in self._VID_EXT:
            return None
        if not p.is_file():
            home = Path.home()
            found = []
            deadline = time.monotonic() + 1.5   # never block the app: a short, shallow look only
            for folder in (DROP_DIR, ROOT / "chats" / "_dropped", ROOT / "chats", ROOT / "logs", home / "Aura",
                           home / "Desktop", home / "Downloads", home / "Pictures", home / "Videos",
                           home / "Pictures" / "Screenshots", Path(tempfile.gettempdir())):
                if time.monotonic() > deadline:
                    break
                for depth in (0, 1, 2):
                    try:
                        pattern = "/".join(["*"] * depth + [p.name])
                        for cand in folder.glob(pattern):
                            if cand.is_file():
                                found.append(cand)
                    except Exception:
                        pass
                if found:
                    break   # the first folder (in this order) that has it wins
            if not found:
                return None
            p = max(found, key=lambda c: c.stat().st_mtime).resolve()
        allowed = [Path.home().resolve(), ROOT.resolve(), Path(tempfile.gettempdir()).resolve()]
        if not any(p == a or a in p.parents for a in allowed):
            return None
        return p

    def media_info(self, path) -> dict:
        """What the chat needs to show a picture or video card: url (data: for pictures, a local address for
        videos), kind, name, size."""
        try:
            p = self._resolve_media(path)
            if p is None:
                return {"url": ""}
            size = p.stat().st_size
            if p.suffix.lower() in self._VID_EXT:
                return {"url": _media_url(p), "kind": "video", "name": p.name, "size": size, "path": str(p)}
            if size > 20 * 1024 * 1024:
                return {"url": ""}
            return {"url": "data:" + self._IMG_MIME[p.suffix.lower()] + ";base64,"
                    + base64.b64encode(p.read_bytes()).decode("ascii"),
                    "kind": "image", "name": p.name, "size": size, "path": str(p)}
        except Exception as exc:
            _log("media_info failed: " + repr(exc))
            return {"url": ""}

    def read_image(self, path) -> dict:
        r = self.media_info(path)
        return {"url": r.get("url", "")} if r.get("kind") == "image" else {"url": ""}

    def copy_media(self, path) -> dict:
        """Put the picture on the clipboard (pastes as a picture) or the video file (pastes as a file)."""
        try:
            p = self._resolve_media(path)
            if p is None:
                return {"ok": False}
            if p.suffix.lower() in self._VID_EXT:
                script = "Set-Clipboard -Path $env:AURA_MEDIA"
            else:
                script = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
                          "$i=[System.Drawing.Image]::FromFile($env:AURA_MEDIA);"
                          "[System.Windows.Forms.Clipboard]::SetImage($i);$i.Dispose()")
            _o, err, rc, why = no_window.run_capture(
                ["powershell", "-STA", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], 25,
                env=dict(os.environ, AURA_MEDIA=str(p)))
            if rc != 0 or why:
                _log("copy_media failed: " + repr(err)[:200])
            return {"ok": rc == 0 and not why}
        except Exception as exc:
            _log("copy_media failed: " + repr(exc))
            return {"ok": False}

    def open_media(self, path) -> dict:
        """Open the picture/video in the normal Windows app."""
        try:
            p = self._resolve_media(path)
            if p is None:
                return {"ok": False}
            os.startfile(str(p))   # noqa: S606 (a picture/video file the user can see in the chat)
            return {"ok": True}
        except Exception as exc:
            _log("open_media failed: " + repr(exc))
            return {"ok": False}

    def save_dropped(self, name, data) -> dict:
        """Keep a copy of a dropped file so the assistant can read it. Returns its path."""
        try:
            safe = re.sub(r"[^\w.\- ]", "_", Path(str(name)).name)[:80] or "file"
            raw = base64.b64decode(data)
            if len(raw) > 25 * 1024 * 1024:
                return {"path": ""}
            DROP_DIR.mkdir(parents=True, exist_ok=True)
            out = DROP_DIR / (uuid.uuid4().hex[:8] + "_" + safe)
            out.write_bytes(raw)
            return {"path": str(out)}
        except Exception as exc:
            _log("save_dropped failed: " + repr(exc))
            return {"path": ""}

    def open_chats_folder(self) -> dict:
        try:
            CHATS_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(CHATS_DIR))
            return {"status": "Opened."}
        except Exception as exc:
            _log("open_chats_folder failed: " + repr(exc))
            return {"status": "Could not open the folder."}

    def export_chat(self, sid) -> dict:
        """Save one chat as a text file (the user picks where)."""
        try:
            import webview
            data = _store_load(sid)
            msgs = data.get("messages") or []
            if not msgs:
                return {"status": "This chat is empty."}
            lines = []
            for m in msgs:
                who = "You" if m.get("role") == "user" else "Aura"
                lines.append(who + ":\n" + mask_names(str(m.get("text", ""))) + "\n")
            title = re.sub(r'[\\/:*?"<>|]+', "", data.get("title") or "chat").strip()[:40] or "chat"
            dialog_type = getattr(getattr(webview, "FileDialog", None), "SAVE", None) or webview.SAVE_DIALOG
            res = webview.windows[0].create_file_dialog(dialog_type, save_filename="Aura - " + title + ".txt")
            if not res:
                return {"status": "Cancelled."}
            path = res if isinstance(res, str) else res[0]
            Path(path).write_text("\n".join(lines), encoding="utf-8")
            return {"status": "Chat saved."}
        except Exception as exc:
            _log("export_chat failed: " + repr(exc))
            return {"status": "Could not save the chat."}

    def _main_hwnd(self):
        u = _user32()
        if not (self._hwnd and u.IsWindow(self._hwnd)):
            self._hwnd = _find_window("Aura")
        return self._hwnd

    def win_theme(self, light: bool) -> None:
        """Paint the window's top strip + border in the page's color (light paper or dark)."""
        try:
            hwnd = self._main_hwnd()
            if not hwnd:
                return
            col = 0xF2F5F6 if light else 0x090706   # COLORREF 0x00BBGGRR (#F6F5F2 / #060709)
            dwm = ctypes.windll.dwmapi
            for attr in (35, 34):   # DWMWA_CAPTION_COLOR, DWMWA_BORDER_COLOR
                dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(ctypes.c_int(col)), 4)
        except Exception as exc:
            _log("win_theme failed: " + repr(exc))

    def win_drag(self) -> None:
        """Move the window with the mouse while the left button stays down (called when the top strip is pressed)."""
        try:
            from ctypes import wintypes as w
            u = _user32()
            h = self._main_hwnd()
            if not h:
                return
            flags = SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
            pt, rc = w.POINT(), w.RECT()
            u.GetCursorPos(ctypes.byref(pt))
            if u.IsZoomed(h):  # dragging a maximized window first restores it, under the cursor
                u.ShowWindow(h, SW_RESTORE)
                time.sleep(0.05)
                u.GetWindowRect(h, ctypes.byref(rc))
                ox, oy = (rc.right - rc.left) // 2, 16
                u.SetWindowPos(h, None, pt.x - ox, pt.y - oy, 0, 0, flags)
            else:
                u.GetWindowRect(h, ctypes.byref(rc))
                ox, oy = pt.x - rc.left, pt.y - rc.top
            started = time.time()
            last = (pt.x, pt.y)
            while (u.GetAsyncKeyState(VK_LBUTTON) & 0x8000) and time.time() - started < 60:
                u.GetCursorPos(ctypes.byref(pt))
                if (pt.x, pt.y) != last:  # only move when the mouse moved
                    last = (pt.x, pt.y)
                    u.SetWindowPos(h, None, pt.x - ox, pt.y - oy, 0, 0, flags)
                time.sleep(0.008)
        except Exception as exc:
            _log("win_drag failed: " + repr(exc))

    def win_min(self) -> None:
        try:
            h = self._main_hwnd()
            if h:
                _user32().ShowWindow(h, SW_MINIMIZE)
        except Exception as exc:
            _log("win_min failed: " + repr(exc))

    def win_max(self) -> dict:
        try:
            u = _user32()
            h = self._main_hwnd()
            if not h:
                return {"max": False}
            u.ShowWindow(h, SW_RESTORE if u.IsZoomed(h) else SW_MAXIMIZE)
            return {"max": bool(u.IsZoomed(h))}
        except Exception as exc:
            _log("win_max failed: " + repr(exc))
            return {"max": False}

    def win_is_max(self) -> dict:
        """Is the window maximized right now? (keeps the maximize button icon right after a drag)"""
        try:
            h = self._main_hwnd()
            return {"max": bool(h and _user32().IsZoomed(h))}
        except Exception:
            return {"max": False}

    def win_close(self) -> None:
        """Close Aura: extra windows first (one that is still being created cannot be closed and used to stop
        the loop before the main window), the main window last; if it still runs after 6 s, end the process."""
        try:
            import webview
            wins = list(webview.windows)
        except Exception as exc:
            _log("win_close failed: " + repr(exc))
            wins = []
        guard = threading.Timer(6.0, self._force_exit)
        guard.daemon = True
        guard.start()
        main_win = next((w for w in wins if getattr(w, "title", "") == "Aura"), wins[0] if wins else None)
        for win in [w for w in wins if w is not main_win] + ([main_win] if main_win is not None else []):
            try:
                win.destroy()
            except Exception as exc:
                _log("win_close: could not close '{}': {!r}".format(getattr(win, "title", "?"), exc))
        if main_win is None:
            self._force_exit()

    def _force_exit(self):
        _log("closing: window did not close by itself, ending Aura")
        try:
            self._shutdown()
        finally:
            os._exit(0)

    # ---- websites Aura builds (shown in the browser pane) ----
    def _site_roots(self):
        home = Path.home()
        return [ROOT / "sites", ROOT / "site", home / "Desktop", home / "Documents", home / "Downloads"]

    def _chat_site(self, sid):
        """The website this chat is about (the last one shown for it), if it still exists."""
        if not str(sid).isalnum():
            return None
        path = _read_text(_side_file(sid, ".site"))
        try:
            return path if path and Path(path).is_file() else None
        except OSError:
            return None

    def remember_site(self, sid, path) -> None:
        """The page tells us which website belongs to which chat, so Polish edits the right one."""
        try:
            if str(sid).isalnum() and Path(str(path)).is_file():
                _side_file(sid, ".site").write_text(str(path), encoding="utf-8")
        except Exception as exc:
            _log("remember_site failed: " + repr(exc))

    def site_check(self, since) -> dict:
        """Did a website file get created or changed since `since` (unix time)? If so, serve it and return its URL."""
        try:
            since = float(since)
            found = [newest_html(self._site_roots(), since, max_depth=3, skip=SITE_SKIP),
                     newest_html([ROOT], since, max_depth=0)]
            found = [f for f in found if f]
            if not found:
                return {"found": False}
            path = max(found, key=lambda f: Path(f).stat().st_mtime)
            return dict(self._serve_site(path), found=True)
        except Exception as exc:
            _log("site_check failed: " + repr(exc))
            return {"found": False}

    def _serve_site(self, path) -> dict:
        p = Path(path)
        folder = str(p.parent)
        with self._srv_lock:
            srv = self._servers.get(folder)
            if srv is None:
                handler = functools.partial(_PreviewHandler, directory=folder)
                srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
                threading.Thread(target=srv.serve_forever, daemon=True).start()
                self._servers[folder] = srv
        url = "http://127.0.0.1:%d/%s" % (srv.server_address[1], urllib.parse.quote(p.name))
        return {"url": url, "name": p.name, "path": str(p), "mtime": p.stat().st_mtime}

    def open_external(self, path) -> dict:
        """Open the website file in the normal browser."""
        try:
            webbrowser.open(Path(path).as_uri())
            return {"status": "Opened."}
        except Exception as exc:
            _log("open_external failed: " + repr(exc))
            return {"status": "Could not open the browser."}

    # ---- browser pane ----
    def normalize_url(self, text) -> str:
        """Typed text -> a safe web address (or a search)."""
        try:
            return normalize_input(text)
        except Exception as exc:
            _log("normalize_url failed: " + repr(exc))
            return ""

    def frame_check(self, url) -> dict:
        """Does this site allow itself to be shown inside the pane?"""
        try:
            import requests
            u = str(url)
            host = urllib.parse.urlparse(u).hostname or ""
            if host in ("127.0.0.1", "localhost"):
                return {"embeddable": True}
            r = requests.get(u, stream=True, timeout=6, allow_redirects=True, headers={"User-Agent": BROWSER_UA})
            try:
                headers = dict(r.headers)
            finally:
                r.close()
            return {"embeddable": can_embed(headers)}
        except Exception as exc:
            _log("frame_check failed: " + repr(exc))
            return {"embeddable": True}  # cannot tell: just try it

    def open_url(self, url) -> dict:
        """Open a web address in the normal browser."""
        try:
            u = str(url)
            if not u.lower().startswith(("http://", "https://")):
                return {"status": "Not a web address."}
            webbrowser.open(u)
            return {"status": "Opened."}
        except Exception as exc:
            _log("open_url failed: " + repr(exc))
            return {"status": "Could not open the browser."}

    def _media(self, action: str) -> dict:
        """Control the video in the pane: the page handles the embedded YouTube player, the app handles a real browser view."""
        win = getattr(self, "_main_win", None)
        if win is None:
            return self._page_media(action)
        try:
            win.evaluate_js("window.__mres=null;window.auraMediaStart&&window.auraMediaStart(%s)" % json.dumps(action))
            end = time.time() + 9
            d = None
            while time.time() < end:
                r = win.evaluate_js("window.__mres")
                if r:
                    d = json.loads(r)
                    break
                time.sleep(0.25)
            if d is None:
                return {"ok": False, "status": "The page did not answer."}
            if d.get("use") == "overlay":
                return self._page_media(action)
            return d
        except Exception as exc:
            _log("media failed: " + repr(exc))
            return {"ok": False, "status": "I could not reach the video."}

    def _page_media(self, action: str) -> dict:
        """Play / pause / mute the video of the page shown in the Aura browser view, then read back what it REALLY does now."""
        win = self._ov_win
        if win is None:
            return {"ok": False, "status": "No page is open in the Aura browser."}
        def js_for(a):
            return (
                "(function(action){var v=document.querySelector('video');"
                "if(!v)return JSON.stringify({ok:false,status:'There is no video on this page.'});"
                "if(action==='pause'){v.pause();}"
                "else if(action==='play'){var p=v.play();if(p&&p.catch)p.catch(function(){});}"
                "else if(action==='toggle'){if(v.paused){var q=v.play();if(q&&q.catch)q.catch(function(){});}else{v.pause();}}"
                "else if(action==='mute'){v.muted=true;}else if(action==='unmute'){v.muted=false;}"
                "else if(action==='click'){var b=document.querySelector('.ytp-large-play-button,.ytp-play-button');if(b)b.click();}"
                "return JSON.stringify({ok:true,paused:v.paused,muted:v.muted,time:Math.round(v.currentTime),"
                "duration:isFinite(v.duration)?Math.round(v.duration):null,title:document.title});})(%s)" % json.dumps(a)
            )

        def run(a):
            raw = win.evaluate_js(js_for(a))
            return json.loads(raw) if raw else {"ok": False, "status": "The page did not answer."}

        try:
            res = run(action)
            if action in ("play", "toggle", "pause", "mute", "unmute", "click"):
                time.sleep(0.9)  # let the player react, then look again
                res = run("status")
                if action == "play" and res.get("ok") and res.get("paused"):  # the site still blocks it: press its own play button
                    run("click")
                    time.sleep(1.2)
                    res = run("status")
            return res
        except Exception as exc:
            _log("page media failed: " + repr(exc))
            return {"ok": False, "status": "I could not reach the video."}

    def open_browser_window(self, url) -> dict:
        """Open a web address in its own Aura browser window (works with every site)."""
        try:
            import webview
            u = str(url)
            if not u.lower().startswith(("http://", "https://")):
                return {"status": "Not a web address."}
            webview.create_window("Aura Browser", url=u, width=1200, height=800, min_size=(600, 400),
                                  text_select=True, background_color="#060709")
            return {"status": "Opened."}
        except Exception as exc:
            _log("open_browser_window failed: " + repr(exc))
            return {"status": "Could not open the window."}

    def ui_log(self, msg) -> None:
        """The page reports its own errors here so they land in logs/aura_app.log."""
        _log("ui: " + str(msg)[:600])

    def poll_open(self) -> list:
        """Web addresses the assistant wants shown in the Aura browser (the page asks regularly)."""
        with self._open_lock:
            out, self._open_q = self._open_q, []
        return out

    def _queue_open(self, url) -> None:
        with self._open_lock:
            self._open_q.append(str(url))
            del self._open_q[:-10]

    # ---- real browser view fastened over the pane (for sites that refuse a normal frame) ----
    def overlay_open(self, url, x, y, w, h) -> dict:
        with self._ov_lock:
            try:
                return self._overlay_open(str(url), int(x), int(y), int(w), int(h))
            except Exception as exc:
                _log("overlay_open failed: " + repr(exc))
                return {"ok": False}

    def _overlay_open(self, url, x, y, w, h) -> dict:
        """Show a real browser view inside the Aura window at this spot (pixels inside the window)."""
        import webview
        if not url.lower().startswith(("http://", "https://")):
            return {"ok": False}
        u = _user32()
        if self._ov_win is not None and self._ov_hwnd and u.IsWindow(self._ov_hwnd):
            self._ov_win.load_url(url)
        else:
            win = webview.create_window("Aura Page", url=url, width=900, height=600, x=-3000, y=-3000,
                                        frameless=True, easy_drag=False, text_select=True,
                                        background_color="#060709", focus=False)
            hwnd = _wait_for_window("Aura Page", 80, 0.1)
            main = self._main_hwnd()
            if not hwnd or not main:
                _log("overlay: could not find windows (view {}, main {})".format(hwnd, main))
                try:
                    win.destroy()
                except Exception:
                    pass
                self._ov_win = self._ov_hwnd = None
                return {"ok": False}
            # Turn it into a borderless child of the main window.
            style = u.GetWindowLongW(hwnd, GWL_STYLE)
            style &= ~(WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
            style |= WS_CHILD | WS_CLIPCHILDREN
            u.SetWindowLongW(hwnd, GWL_STYLE, _as_long(style))
            u.SetParent(hwnd, main)
            self._ov_win, self._ov_hwnd = win, hwnd
        u.MoveWindow(self._ov_hwnd, x, y, max(w, 1), max(h, 1), True)
        u.ShowWindow(self._ov_hwnd, SW_SHOW)
        return {"ok": True}

    def overlay_move(self, x, y, w, h) -> dict:
        try:
            hwnd = self._ov_hwnd
            if hwnd:
                u = _user32()
                u.MoveWindow(hwnd, int(x), int(y), max(int(w), 1), max(int(h), 1), True)
                u.ShowWindow(hwnd, SW_SHOW)
            return {"ok": bool(hwnd)}
        except Exception as exc:
            _log("overlay_move failed: " + repr(exc))
            return {"ok": False}

    def overlay_hide(self, stop=False) -> dict:
        try:
            if self._ov_hwnd:
                _user32().ShowWindow(self._ov_hwnd, SW_HIDE)
                if stop and self._ov_win is not None:
                    self._ov_win.load_url("about:blank")  # silence videos and music
            return {"ok": True}
        except Exception as exc:
            _log("overlay_hide failed: " + repr(exc))
            return {"ok": False}

    # ---- one-shot modes: Quick, Local, Big project, Polish ----
    def send_message(self, sid, text, mode="auto", images=None) -> dict:
        """Old all-in-one call (waits for the answer). The page now uses job_start/job_poll instead, so it can
        show live steps and switch chats; this stays for older pages and quick tests."""
        try:
            sid = str(sid)
            if self._chat_working(sid):
                return {"reply": WORKING_ELSEWHERE, "who": "App", "error": True}
            text, images, mode = self._begin_turn(sid, text, mode, images)
            reply, who = self._answer(sid, text, mode, images, None)
            failed = who == "App"
            self._save_answer(sid, reply, who)
            return {"reply": reply, "who": who, "error": failed} if failed else {"reply": reply, "who": who}
        except Exception as exc:
            _log("send_message failed: " + repr(exc))
            return {"reply": INTERNAL_ERROR, "who": "App", "error": True}

    def _begin_turn(self, sid, text, mode, images):
        """Save the user's message; return (prompt with earlier context, pictures, mode)."""
        text = str(text or "")
        images = _valid_pictures(images)
        context = _history(sid)
        _store_add(sid, "user", _user_line(text, images))
        if context and text.strip() and not images:  # with a picture, earlier answers must not get in the way
            text = context + "New request from the user: " + text
        return text, images, (mode if mode in MODES else "claude")

    def _answer(self, sid, text, mode, images, job):
        """Do the work for one message. Returns (reply, who); who == "App" means a failure message."""
        stop = (lambda: job["stopped"]) if job else None
        if mode in ("qwen", "auto"):
            reply = self._ask_local(text, images, job, force_no_think=(mode == "auto"))  # Quick: never thinks
            if job and job["stopped"]:
                return "Stopped.", "Note"
            if mode == "auto" and self._local_failed(reply):
                self._job_event(job, {"t": "step", "kind": "work", "text": "Getting extra help"})
                reply, who = self._one_shot("Qwen could not do this task, do it yourself and follow CLAUDE.md: " + text,
                                            HELPER_MODEL, "Claude", None, CLAUDE_TIMEOUTS["auto"], stop)
            else:
                who = "Qwen"
            if reply.startswith("Error:"):
                return reply[6:].strip() or TRY_AGAIN, "App"
            return reply, who
        if mode == "polish":
            return self._polish(sid, text, stop)
        return self._one_shot(text, AURA_MODEL, "Claude", _claude_session(sid), CLAUDE_TIMEOUTS["claude"], stop)

    @staticmethod
    def _save_answer(sid, reply, who) -> None:
        try:
            _store_add(sid, "assistant", "[" + who + "] " + (reply or "No answer."))
        except KeyError:
            pass  # the chat was deleted meanwhile
        except Exception as exc:
            _log("could not save the answer: " + repr(exc))

    @staticmethod
    def _one_shot(prompt, model, who, extra=None, timeout=1800, stop=None, effort=None):
        reply = run_claude(prompt, model, extra, timeout, stop, effort)
        if reply is None:
            return NOT_SET_UP, "App"
        if reply in (NOT_SIGNED_IN, TRY_AGAIN, INTERNAL_ERROR) or reply.startswith("Aura took too long"):
            return reply, "App"
        if reply == "Stopped.":
            return reply, "Note"
        return reply, who

    @staticmethod
    def _local_failed(reply) -> bool:
        return (not reply or reply.startswith(("Stopped:", "Error", "No answer", "Aura's local helper is not reachable")))

    # ---- background jobs: the page starts one, polls its events, can leave the chat and come back ----
    def _chat_working(self, sid) -> bool:
        with self._chats_lock:
            st = self._chats.get(sid)
        with self._jobs_lock:
            job = self._jobs.get(sid)
        return bool((st and st.get("busy")) or (job and job.get("busy")))

    def job_start(self, sid, text, mode="auto", images=None) -> dict:
        """Start Quick / Local / Big project / Polish for this chat in the background. Returns {"ok": True}
        at once; the answer arrives through job_poll (and is saved into this chat, whichever chat is open)."""
        try:
            sid = str(sid)
            if not sid.isalnum():
                return {"ok": False, "error": INTERNAL_ERROR}
            with self._chats_lock:
                st = self._chats.get(sid)
                aura_busy = bool(st and st.get("busy"))
            with self._jobs_lock:
                old = self._jobs.get(sid)
                if aura_busy or (old and old.get("busy")):
                    return {"ok": False, "error": WORKING_ELSEWHERE}
                job = {"busy": True, "stopped": False, "proc": None, "mode": mode, "started": time.time(),
                       "phase": "think", "events": [], "live": [], "lock": threading.Lock()}
                self._jobs[sid] = job
            try:
                text, images, mode = self._begin_turn(sid, text, mode, images)
            except Exception as exc:
                _log("job_start could not save the message: " + repr(exc))
                with self._jobs_lock:
                    if self._jobs.get(sid) is job:
                        del self._jobs[sid]
                return {"ok": False, "error": "This chat could not be saved. Please start a new chat."}
            job["mode"] = mode
            job["phase"] = {"big": "big", "polish": "polish"}.get(mode, "look" if images else "think")
            threading.Thread(target=self._run_job, args=(sid, job, text, mode, images), daemon=True).start()
            return {"ok": True}
        except Exception as exc:
            _log("job_start failed: " + repr(exc))
            return {"ok": False, "error": CANT_START}

    def _run_job(self, sid, job, text, mode, images):
        reply, who = INTERNAL_ERROR, "App"
        try:
            reply, who = self._answer(sid, text, mode, images, job)
            reply = reply or "No answer."
        except Exception as exc:
            _log("job failed: " + repr(exc))
        if job["stopped"]:
            reply, who = "Stopped.", "Note"
        with job["lock"]:  # saving + "done" together, so open_chat never sees one without the other
            self._save_answer(sid, reply, who)
            job["busy"] = False
            ev = {"t": "done", "text": reply, "error": who == "App", "note": who == "Note"}
            live_state.apply(job["live"], ev)
            live_state.push(job["events"], ev)

    def _job_event(self, job, ev) -> None:
        if not job:
            return
        if isinstance(ev.get("text"), str):
            ev["text"] = " ".join(mask_names(ev["text"]).split())[:200]
        for key, lim in (("detail", MAX_DETAIL), ("out", MAX_OUT)):  # Details view: raw command / output
            if isinstance(ev.get(key), str):
                ev[key] = clip_detail(ev[key], lim)
            else:
                ev.pop(key, None)
        with job["lock"]:
            if ev.get("t") == "step" and not ev.get("detail"):  # one tidy line: no filler, no step twice in a row
                text, live = ev.get("text") or "", job["live"]
                if (not text or text.lower().rstrip(".") in FILLER_STEPS
                        or (live and live[-1].get("k") == "chip" and live[-1].get("text") == text)):
                    return
            if ev.get("t") == "phase":
                job["phase"] = ev.get("kind") or "think"
            elif ev.get("t") == "step":
                job["phase"] = ev.get("kind") or "work"
            live_state.apply(job["live"], ev)
            live_state.push(job["events"], ev)

    def job_poll(self, sid) -> list:
        """New events of this chat's job (JSON strings, same shape as chat_poll)."""
        try:
            with self._jobs_lock:
                job = self._jobs.get(str(sid))
            if not job:
                return []
            with job["lock"]:
                out, job["events"] = job["events"], []
            return [json.dumps(e) for e in out]
        except Exception:
            return []

    def job_stop(self, sid) -> bool:
        return self._stop_job(str(sid))

    def _stop_job(self, sid, quiet=False) -> bool:
        try:
            with self._jobs_lock:
                job = self._jobs.get(sid)
            if not job or not job.get("busy"):
                return False
            job["stopped"] = True
            proc = job.get("proc")
            if proc is not None:
                _kill_tree(proc)
            if not quiet:
                _log("job for chat {} stopped by the user".format(sid))
            return True
        except Exception as exc:
            _log("job_stop failed: " + repr(exc))
            return False

    def _ask_local(self, text, images, job=None, force_no_think=False) -> str:
        """Run the local assistant (agent.py). Failures come back as text starting with 'Error: '.
        Streams its step lines into the job, never runs longer than LOCAL_TIMEOUT, and only one local task
        runs at a time (others wait, and say so)."""
        img_dir, img_args, got_lock = None, [], False
        try:
            if images:
                img_dir = tempfile.mkdtemp(prefix="aura_img_")
                for path in _save_pictures(images, Path(img_dir)):
                    img_args += ["--image", path]
                text = ("Look ONLY at the attached picture(s) and answer about them. Do not reuse any earlier answer or "
                        "description; describe what this picture itself shows. " + (text.strip() or "Describe this picture."))
            if len(text) > MAX_ARG_CHARS:
                return "Error: That message is too long for this mode. Please shorten it, or use Aura mode."
            if not self._local_lock.acquire(blocking=False):
                self._job_event(job, {"t": "phase", "kind": "wait"})
                waited = time.time()
                while not self._local_lock.acquire(timeout=0.5):
                    if job and job["stopped"]:
                        return "Stopped."
                    if time.time() - waited > LOCAL_TIMEOUT:
                        return "Error: Aura's local helper is still busy with another chat. Please try again later."
                self._job_event(job, {"t": "phase", "kind": "look" if images else "think"})
            got_lock = True
            if job and job["stopped"]:
                return "Stopped."
            env = _clean_env("off" if force_no_think else None)
            env.update(AURA_PROGRESS="1", AGENT_DEADLINE=str(LOCAL_DEADLINE), PYTHONUNBUFFERED="1")

            def on_line(line):
                if line.startswith(PROGRESS_PREFIX):
                    try:
                        ev = json.loads(line[len(PROGRESS_PREFIX):])
                    except ValueError:
                        return
                    if isinstance(ev, dict) and ev.get("t") in ("step", "phase", "result"):
                        self._job_event(job, ev)

            def on_start(proc):
                if job:
                    job["proc"] = proc
                    if job["stopped"]:  # Stop was pressed while it was starting
                        _kill_tree(proc)

            out, err, code, reason = no_window.run_capture(
                [CHILD_PY, str(ROOT / "agent.py"), text] + img_args, LOCAL_TIMEOUT, cwd=str(ROOT), env=env,
                on_line=on_line if job else None, on_start=on_start,
                should_stop=(lambda: job["stopped"]) if job else None)
            if reason == "stopped":
                return "Stopped."
            if reason == "timeout":
                _log("agent.py stopped after {} s".format(LOCAL_TIMEOUT))
                return ("Error: Aura got no answer within {} minutes, so it stopped. The helper PC may be busy; "
                        "try again, or try a smaller request.".format(LOCAL_TIMEOUT // 60))
            out = "\n".join(ln for ln in (out or "").splitlines()
                            if not STEP_LINE.match(ln) and not ln.startswith(PROGRESS_PREFIX)).strip()
            if not out:
                err = (err or "").strip()
                if err:
                    _log("agent.py stderr (exit {}): {}".format(code, err[-3000:]))
                    return "Error: " + _friendly(err[-6000:])
                if code:
                    _log("agent.py exited with code {} and no output".format(code))
                    return "Error: " + TRY_AGAIN
                return "Done."
            return _friendly(out[-6000:])
        except Exception as exc:
            _log("agent.py failed: " + repr(exc))
            return "Error: Aura's local helper could not start."
        finally:
            if got_lock:
                self._local_lock.release()
            if job:
                job["proc"] = None
            if img_dir:
                shutil.rmtree(img_dir, ignore_errors=True)

    def _polish(self, sid, text, stop=None):
        """Polish edits the website of this chat (or else the newest one), even outside the project folder."""
        extra, site_note = [], ""
        try:
            site = self._chat_site(sid) or newest_html(self._site_roots(), time.time() - 7 * 86400,
                                                       max_depth=3, skip=SITE_SKIP)
            if site:
                site_dir = str(Path(site).parent)
                extra = ["--add-dir", site_dir]
                site_note = (
                    " The website to polish (the one from this chat, or else the newest) is in " + site_dir
                    + " (main file: " + site + "). Start your reply with: Polished <the website's folder name>."
                    " If the user does not name another project, polish THAT website. You may edit files inside that"
                    " website folder and nowhere else outside the project folder. Never delete files.")
        except Exception as exc:
            _log("polish: could not find the website folder: " + repr(exc))
        prompt = ("FINAL POLISH, one pass only: " + text + ". Make the project more modern, professional and solid"
                  " (design/UX, structure, error handling, speed, small bugs). Focused edits only inside this folder,"
                  " never delete files, confirm nothing broke, reply in 3-5 lines." + site_note)
        return self._one_shot(prompt, POLISH_MODEL, "Opus", extra, CLAUDE_TIMEOUTS["polish"], stop, "high")  # Polish always thinks

    # ---- updates ----
    def check_update(self) -> dict:
        try:
            cfg = _update_config()
            repo = cfg.get("repo", "")
            if not repo or "OWNER" in repo:
                return {"status": "Updates are not set up yet.", "newer": False}
            import requests
            branch = cfg.get("branch", "main")
            if cfg.get("token"):
                url = "https://api.github.com/repos/{}/contents/VERSION?ref={}".format(repo, branch)
            else:  # public repo: plain file links have no strict request limit (the API allows only 60 an hour per home)
                # a changing query keeps GitHub's 5-minute file cache from hiding a brand-new version
                ref = _latest_commit(repo, branch) or branch
                url = "https://raw.githubusercontent.com/{}/{}/VERSION?t={}".format(repo, ref, int(time.time()))
            hdrs = _github_headers(cfg.get("token", ""))
            hdrs["Cache-Control"] = "no-cache"
            resp = requests.get(url, headers=hdrs, timeout=15)
            resp.raise_for_status()
            remote = resp.text.strip()
            if updater.is_newer(remote, _local_version()):
                return {"status": "Update available: v" + remote, "newer": True, "state": "available", "version": remote}
            return {"status": "You are up to date.", "newer": False, "state": "uptodate", "version": _local_version()}
        except Exception as exc:
            _log("check_update failed: " + repr(exc))
            if _is_offline_error(exc):
                return {"status": "Could not check (no internet).", "newer": False, "state": "offline"}
            return {"status": "Could not check for updates right now. Please try again later.", "newer": False,
                    "state": "error"}

    def _seen_file(self) -> Path:
        return ROOT / "logs" / ("reminder_seen_" + ("hermes" if _theme() == "hermes" else "cyberpunk") + ".txt")

    def reminder_seen(self, rid) -> dict:
        """She has read this reminder: remember it on this PC (a file, because the page's own storage is wiped on every start)."""
        try:
            f = self._seen_file()
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(str(rid)[:40], encoding="utf-8")
            return {"ok": True}
        except Exception as exc:
            _log("reminder_seen failed: " + repr(exc))
            return {"ok": False}

    def get_reminder(self) -> dict:
        """The newest reminder meant for this look, unless it was already read on this PC."""
        res = self._get_reminder_raw()
        try:
            if res.get("id") and res["id"] == self._seen_file().read_text(encoding="utf-8").strip():
                return {"id": "", "text": ""}
        except OSError:
            pass
        return res

    def _get_reminder_raw(self) -> dict:
        """A short note from the person who looks after this Aura (reminder.json in the update repo). Never raises."""
        try:
            cfg = _update_config()
            repo = os.environ.get("AURA_REMINDER_REPO", "").strip() or cfg.get("repo", "")  # a launcher may read reminders from another repo (updates stay as they are)
            if not repo or "OWNER" in repo:
                return {"id": "", "text": ""}
            import requests
            branch = cfg.get("branch", "main")
            ref = _latest_commit(repo, branch) or branch  # an exact commit is never served from an old cache
            cached = getattr(self, "_rem_cache", None)
            if cached and cached[0] == (repo, ref) and ref != branch:
                return cached[1]  # nothing new on GitHub since the last look: no second request
            url = "https://raw.githubusercontent.com/{}/{}/reminder.json?t={}".format(repo, ref, int(time.time()))
            hdrs = {"Cache-Control": "no-cache"}
            if cfg.get("token"):
                hdrs["Authorization"] = "token " + cfg["token"]
            resp = requests.get(url, headers=hdrs, timeout=10)
            if resp.status_code != 200:
                return {"id": "", "text": ""}
            data = json.loads(resp.content.decode("utf-8-sig"))
            text = str(data.get("text", "")).strip()[:400]
            look = str(data.get("look", "")).strip().lower()
            mine = "hermes" if _theme() == "hermes" else "cyberpunk"
            if look in ("hermes", "cyberpunk") and look != mine:  # sent to the other app (e.g. the other look on a shared repo)
                return {"id": "", "text": ""}
            res = {"id": str(data.get("id", ""))[:40], "text": text}
            self._rem_cache = ((repo, ref), res)
            return res
        except Exception as exc:
            _log("get_reminder failed: " + repr(exc))
            return {"id": "", "text": ""}

    def update_download(self) -> dict:
        """Step 1 of an update: fetch the new version (kept in memory until update_install)."""
        if not self._upd_lock.acquire(blocking=False):
            return {"ok": False, "status": "An update is already running."}
        try:
            cfg = _update_config()
            repo = cfg.get("repo", "")
            if not repo or "OWNER" in repo:
                return {"ok": False, "status": "Updates are not set up on this PC."}
            import requests
            if cfg.get("token"):
                url = "https://api.github.com/repos/{}/zipball/{}".format(repo, cfg.get("branch", "main"))
            else:
                ref = _latest_commit(repo, cfg.get("branch", "main"))
                url = ("https://github.com/{}/archive/{}.zip".format(repo, ref) if ref
                       else "https://github.com/{}/archive/refs/heads/{}.zip".format(repo, cfg.get("branch", "main")))
            resp = requests.get(url, headers=_github_headers(cfg.get("token", "")), timeout=60)
            resp.raise_for_status()
            files = {}
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                for info in zf.infolist():
                    parts = info.filename.split("/", 1)
                    if info.is_dir() or len(parts) < 2 or parts[1] in LOCAL_ONLY \
                            or parts[1].split("/")[0].lower() in ("chats", "logs"):
                        continue  # this PC's own settings and data are never replaced
                    files[parts[1]] = zf.read(info)
            if "app/app.py" not in files and not getattr(sys, "frozen", False):
                _log("update: downloaded archive has no app/app.py ({} files)".format(len(files)))
                return {"ok": False, "status": "The downloaded update looks incomplete. Please try again later."}
            self._upd_files = files
            return {"ok": True}
        except Exception as exc:
            _log("update download failed: " + repr(exc))
            if _is_offline_error(exc):
                return {"ok": False, "status": "Could not download the update (no internet). Check your connection "
                                              "and press Retry."}
            return {"ok": False, "status": "Could not download the update. Please press Retry in a moment."}
        finally:
            self._upd_lock.release()

    def update_install(self) -> dict:
        """Step 2: write the downloaded files (chats, logs and this PC's settings are never touched)."""
        if not self._upd_lock.acquire(blocking=False):
            return {"ok": False, "status": "An update is already running."}
        try:
            files, self._upd_files = self._upd_files, None
            if not files:
                return {"ok": False, "status": "Nothing was downloaded yet. Please press Retry."}
            written = updater.apply_update(files, ROOT)
            if "requirements.txt" in written:
                try:
                    res = subprocess.run([CHILD_PY, "-m", "pip", "install", "-q", "--disable-pip-version-check",
                                          "-r", str(ROOT / "requirements.txt")], cwd=str(ROOT), timeout=300,
                                         capture_output=True, **_hidden())
                    if res.returncode != 0:
                        _log("pip after update failed: " + (res.stderr or b"").decode("utf-8", "replace")[-2000:])
                        return {"ok": False, "status": "The new files are in place, but some parts could not be "
                                                      "installed. Check your internet and press Retry."}
                except Exception as exc:
                    _log("pip after update failed: " + repr(exc))
                    return {"ok": False, "status": "The new files are in place, but some parts could not be "
                                                  "installed. Check your internet and press Retry."}
            _log("update installed: {} files, now v{}".format(len(written), _local_version()))
            return {"ok": True, "version": _local_version()}
        except Exception as exc:
            _log("update install failed: " + repr(exc))
            return {"ok": False, "status": "Could not install the update. Aura is still running; press Retry."}
        finally:
            self._upd_lock.release()

    def do_update(self) -> dict:
        """Old one-call update (download + install), kept for older pages."""
        r = self.update_download()
        if r.get("ok"):
            r = self.update_install()
        return {"status": "Updated. Aura will restart." if r.get("ok") else r.get("status", "Update failed.")}

    def update_restart(self) -> dict:
        """Step 3: start a fresh Aura and close this one. The new one first tells us it started fine
        (RESTART_READY), then waits until this process is gone before opening its window. If it does not
        come up, this Aura keeps running and the page shows the error (no restart loop)."""
        with self._upd_lock:
            if self._restarting:
                return {"ok": True}
            proc = None
            try:
                _unlink(RESTART_READY)
                if getattr(sys, "frozen", False):
                    cmd = [sys.executable]
                else:
                    cmd = [no_window.gui_python(), str(ROOT / "app" / "app.py")]
                cmd += ["--updated", "--wait-pid={}".format(os.getpid())]
                proc = no_window.start_detached(cmd, str(ROOT))
                deadline = time.time() + RESTART_WAIT_SECONDS
                while time.time() < deadline:
                    if _read_text(RESTART_READY):  # (a venv pythonw is a launcher, so its pid is not the app's)
                        break
                    if proc.poll() is not None:
                        raise RuntimeError("new Aura exited early with code {}".format(proc.returncode))
                    time.sleep(0.2)
                else:
                    raise RuntimeError("new Aura did not report back in time")
            except Exception as exc:
                _log("restart after update failed: " + repr(exc))
                if proc is not None and proc.poll() is None:
                    _kill_tree(proc)
                _unlink(RESTART_READY)
                return {"ok": False, "status": "Aura could not restart by itself. It is still running: press Retry, "
                                              "or close Aura and open it again to finish the update."}
            self._restarting = True
        _log("restarting for the update (new pid {})".format(proc.pid))
        threading.Thread(target=self._exit_for_restart, daemon=True).start()
        return {"ok": True}

    def _exit_for_restart(self):
        """Close everything cleanly so the new Aura can take over (it waits for this process to end)."""
        time.sleep(0.4)  # let the page receive the answer first
        guard = threading.Timer(8.0, lambda: os._exit(0))  # never hang around if something blocks below
        guard.daemon = True
        guard.start()
        try:
            self._shutdown()
        except Exception as exc:
            _log("shutdown before restart: " + repr(exc))
        try:
            import webview
            for w in list(webview.windows)[::-1]:  # the main window last
                try:
                    w.destroy()
                except Exception:
                    pass
        except Exception as exc:
            _log("closing windows before restart: " + repr(exc))

    def btw(self, sid, question) -> dict:
        """/btw: a quick side question. The fast model answers it with the chat as background; nothing is saved
        into the chat, no tools that change things are allowed, and work in progress is not disturbed."""
        q = str(question or "").strip()[:2000]
        if not q:
            return {"ok": False, "error": "Type your question after /btw."}
        try:
            context = _history(str(sid)) if str(sid).isalnum() else ""
            prompt = ("SIDE QUESTION. Answer in a few short, plain sentences. Do not run any command, do not change "
                      "any file and do not start any work.\n\n"
                      + (("What was said so far in this chat (for background only):\n" + context + "\n") if context else "")
                      + "Side question: " + q)
            reply = run_claude(prompt, QUICK_MODEL, ["--disallowedTools", "Bash", "PowerShell", "Write", "Edit", "MultiEdit", "NotebookEdit"], 120, None, "off")
            if reply is None:
                return {"ok": False, "error": NOT_SET_UP}
            if reply in (TRY_AGAIN, NOT_SIGNED_IN) or reply.startswith("Aura took too long"):
                return {"ok": False, "error": reply}
            return {"ok": True, "text": reply}
        except Exception as exc:
            _log("btw failed: " + repr(exc))
            return {"ok": False, "error": TRY_AGAIN}

    def set_think(self, level) -> dict:
        """The page's Thinking switch: off / low / medium / high (applies from the next message)."""
        level = str(level or "").lower()
        if level in THINK_LEVELS:
            _THINK["level"] = level
        return {"ok": True, "level": _THINK["level"]}

    def update_notice(self) -> str:
        """The version Aura was just updated to (once, right after the restart), else ''."""
        v, self._updated_to = self._updated_to, ""
        return v

    # ---- Aura mode: one streaming background process per chat ----
    def _claude_proc(self, sid, mode="claude"):
        """The chat's running process, started if needed. Returns its state dict, or None.
        mode "claude" = Aura (Sonnet, the Thinking switch applies); "auto" = Quick (Haiku, never thinks).
        Switching between them restarts the process; the same conversation is resumed."""
        model = QUICK_MODEL if mode == "auto" else AURA_MODEL
        level = "off" if mode == "auto" else _THINK["level"]
        with self._chats_lock:
            st = self._chats.get(sid)
            if st and st["proc"].poll() is None:
                if (st.get("model") == model and st.get("think") == level) or st.get("busy"):
                    return st
                _kill_tree(st["proc"])  # the mode or Thinking level changed: restart it (the same conversation is resumed)
                self._chats.pop(sid, None)
            exe = shutil.which("claude")
            if not exe:
                _log("claude executable not found on PATH")
                return None
            cmd = ([exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                    "--verbose", "--include-partial-messages", "--permission-mode", "bypassPermissions",
                    "--model", model, "--append-system-prompt", AURA_PERSONA,
                    "--disallowedTools", "AskUserQuestion", "EnterPlanMode", "ExitPlanMode"]
                   + _effort_args(level) + _claude_session(sid))
            try:
                proc = subprocess.Popen(
                    cmd, cwd=str(ROOT), env=_clean_env(level), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", **_hidden())
            except OSError as exc:
                _log("could not start chat process: " + repr(exc))
                return None
            st = {"proc": proc, "events": [], "live": [], "lock": threading.Lock(), "stopped": False,
                  "busy": False, "last": time.time(), "started": time.time(), "phase": "think", "mode": mode,
                  "tr": Translator(), "noise": [], "think": level, "model": model}
            self._chats[sid] = st
            if not self._reaper_on:
                self._reaper_on = True
                threading.Thread(target=self._reap_idle_chats, daemon=True).start()
        threading.Thread(target=self._read_chat, args=(sid, st), daemon=True).start()
        return st

    @staticmethod
    def _publish(st, events) -> None:
        """Add events to the chat's live picture and waiting queue (caller holds st["lock"])."""
        for ev in events:
            t = ev.get("t")
            if t in ("tool", "step"):
                st["phase"] = ev.get("kind") or "work"
            elif t == "text":
                st["phase"] = "write"
            live_state.apply(st["live"], ev)
            live_state.push(st["events"], ev)
        st["last"] = time.time()

    def _read_chat(self, sid, st):
        """Turn the process output into small events for the page (picked up by chat_poll). The reply is saved
        into THIS chat when the turn ends, whichever chat the page shows."""
        proc, tr, noise = st["proc"], st["tr"], st["noise"]

        def translate(line):
            try:
                events = tr.feed(line) or []
            except Exception as exc:
                _log("chat event error: " + repr(exc))
                return []
            for ev in events:
                if ev.get("t") == "done" and ev.get("error"):
                    raw_text = ev.get("text", "")
                    ev["text"] = NOT_SIGNED_IN if _needs_login(raw_text) else (_friendly(raw_text) or TRY_AGAIN)
                elif ev.get("t") == "tool":
                    try:
                        kind = tool_kind_from_line(line)
                        label = claude_step(line)
                    except Exception:
                        kind, label = None, None
                    ev["kind"] = (label or {}).get("kind") or kind or "work"
                    ev["text"] = (label or {}).get("text") or CHIP_TEXT.get(ev["kind"], "Completed a task")
                    ev["detail"] = claude_detail(line)
            if '"tool_use"' in line:  # internal bookkeeping calls (like loading a tool) are not steps the user cares about
                only_hk, hk_ids = housekeeping_calls(line)
                if hk_ids:
                    st.setdefault("skip_ids", set()).update(hk_ids)
                if only_hk:
                    events = [e for e in events if e.get("t") != "tool"]
            if not events and '"tool_result"' in line:  # what a tool printed (Details view)
                ids = result_tool_ids(line)
                if not (ids and all(i in st.get("skip_ids", ()) for i in ids)):
                    out = claude_result(line)
                    if out:
                        events.append({"t": "result", "out": out})
            return events

        try:
            for raw in proc.stdout:
                line = raw.strip()
                if not line:
                    continue
                obj = None
                if line.startswith("{"):
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        obj = None
                is_result = isinstance(obj, dict) and obj.get("type") == "result"
                reply = tr.saved_text() if is_result else ""   # read before the result line resets it
                events = translate(line)
                with st["lock"]:
                    if is_result and not st["stopped"]:
                        if reply:
                            self._save_reply(sid, reply)
                        for ev in events:
                            if ev.get("t") == "done" and ev.get("error"):
                                self._save_answer(sid, ev.get("text") or TRY_AGAIN, "App")
                        st["busy"] = False
                    if events and not st["stopped"]:
                        self._publish(st, events)
                if is_result:
                    threading.Thread(target=self._maybe_handoff, args=(sid,), daemon=True).start()
                if obj is None and not events:
                    noise.append(line)
                    del noise[:-40]
        except Exception as exc:
            noise.append("reader error: " + repr(exc))
        no_conversation = any("no conversation found" in n.lower() for n in noise)
        if no_conversation:
            try:  # the saved conversation is gone; the next message starts a fresh one
                _side_file(sid, ".claude").write_text("new:" + str(uuid.uuid4()), encoding="utf-8")
            except OSError:
                pass
        if not st["stopped"]:
            _log("chat process for {} ended (exit {}). Last output:\n{}".format(sid, proc.poll(), "\n".join(noise)))
        retry = None
        with st["lock"]:
            if st["busy"] and not st["stopped"] and no_conversation and st.get("line") and not st.get("retried"):
                st["busy"] = False   # the message goes once more to a fresh conversation (below)
                retry = st["line"]
            elif st["busy"] and not st["stopped"]:  # it died in the middle of an answer: keep what came, say so
                partial = tr.saved_text()
                if partial:
                    self._save_reply(sid, partial)
                login = any(_needs_login(n) for n in noise)
                msg = NOT_SIGNED_IN if login else "Aura stopped unexpectedly. Please send your message again."
                self._save_answer(sid, msg, "App")
                st["busy"] = False
                self._publish(st, [{"t": "done", "error": True, "text": msg}])
        if retry:
            self._resend(sid, st, retry)

    def _resend(self, sid, old, line):
        """Send the same message again to a fresh process (once). The page keeps polling this chat and
        simply sees the new process's events."""
        _log("chat {}: saved conversation was gone, sending the message to a fresh one".format(sid))
        st = self._claude_proc(sid, old.get("mode", "claude"))
        err = NOT_SET_UP if st is None else None
        if st is not None:
            try:
                with st["lock"]:
                    del st["events"][:]
                    del st["live"][:]
                    st["busy"], st["stopped"], st["retried"], st["line"] = True, False, True, line
                    st["last"] = time.time()
                    st["started"] = old.get("started", time.time())
                    st["phase"] = "think"
                st["proc"].stdin.write(line)
                st["proc"].stdin.flush()
                return
            except (OSError, ValueError) as exc:
                _log("resend failed: " + repr(exc))
                st["busy"] = False
                err = CANT_START
        target = st if st is not None else old
        with target["lock"]:
            self._save_answer(sid, err, "App")
            self._publish(target, [{"t": "done", "error": True, "text": err}])

    @staticmethod
    def _save_reply(sid, text):
        if not text:
            return
        try:
            _store_add(sid, "assistant", "[Claude] " + text)
        except KeyError:
            pass  # the chat was deleted meanwhile
        except Exception as exc:
            _log("could not save reply: " + repr(exc))

    def _reap_idle_chats(self):
        """Stop background processes of chats nobody used for 20 minutes (they resume on the next message),
        and end an answer that shows no sign of life for AURA_SILENT_SECONDS (so 'busy' never sticks)."""
        while True:
            time.sleep(30)
            try:
                with self._chats_lock:
                    items = list(self._chats.items())
                for sid, st in items:
                    idle = time.time() - st.get("last", time.time())
                    alive = st["proc"].poll() is None
                    if st.get("busy") and idle > AURA_SILENT_SECONDS:
                        _log("chat {} silent for {} minutes while answering: stopping it".format(
                            sid, AURA_SILENT_SECONDS // 60))
                        msg = ("Aura got no answer for {} minutes, so it stopped. Please try again."
                               .format(AURA_SILENT_SECONDS // 60))
                        with st["lock"]:
                            if st.get("busy"):
                                partial = st["tr"].saved_text()
                                if partial:
                                    self._save_reply(sid, partial)
                                self._save_answer(sid, msg, "App")
                                st["busy"] = False
                                self._publish(st, [{"t": "done", "error": True, "text": msg}])
                            st["stopped"] = True
                        self._stop_chat(sid)
                    elif idle > IDLE_STOP_SECONDS and not st.get("busy") and alive:
                        _log("chat {} idle for 20 minutes: stopping its background process".format(sid))
                        self._stop_chat(sid)
            except Exception as exc:
                _log("idle reaper: " + repr(exc))

    def _maybe_handoff(self, sid):
        """Like the terminal's auto-clear: when this chat gets big, the local assistant writes a summary
        and the chat continues in a fresh conversation that starts from that summary."""
        with self._handoff_lock:
            if sid in self._handoff_busy:
                return
            self._handoff_busy.add(sid)
        try:
            limit = int(os.environ.get("AUTOCLEAR_TOKENS", "100000"))
            f = _side_file(sid, ".claude")
            saved = _read_text(f)
            sess = saved[4:] if saved.startswith("new:") else saved
            if not sess:
                return
            transcript = _claude_project_dir() / (sess + ".jsonl")
            if not transcript.exists():
                return
            tokens = _context_tokens(transcript)
            if tokens < limit:
                return
            _log("chat {} is {} tokens: writing the handoff summary".format(sid, tokens))
            before = transcript.stat().st_mtime
            if not (ROOT / "handoff.py").exists():
                return
            out, err, _code, _reason = no_window.run_capture(
                [CHILD_PY, str(ROOT / "handoff.py"), "--hook", "--print"], 900, cwd=str(ROOT), env=_clean_env(),
                input_text=json.dumps({"transcript_path": str(transcript)}))
            summary = (out or "").strip()
            if not summary:
                _log("handoff gave no summary: " + (err or "")[-500:])
                return
            if transcript.stat().st_mtime != before:  # the chat moved on meanwhile; try again next time
                return
            with self._chats_lock:
                st = self._chats.get(sid)
            if st and st.get("busy"):  # a new turn started meanwhile; do not cut it off
                return
            _side_file(sid, ".summary").write_text(summary, encoding="utf-8")
            self._stop_chat(sid)
            f.write_text("new:" + str(uuid.uuid4()), encoding="utf-8")
            _log("chat {} continues in a fresh conversation from the summary".format(sid))
        except Exception as exc:
            _log("handoff failed: " + repr(exc))
        finally:
            with self._handoff_lock:
                self._handoff_busy.discard(sid)

    def _compose_chat_message(self, sid, text, images):
        """Build the message for the chat process. Returns (content, used_summary)."""
        context = _history(sid, since_tag="Claude")  # only what happened in the other modes since
        if context and text.strip() and not images:
            text = context + "New message from the user: " + text
        carried = _read_text(_side_file(sid, ".summary"))
        if images:
            # Pictures cost many tokens, so the free local assistant can read them too (do.bat --image).
            _prune_old_pictures()
            paths = _save_pictures(images, PICS_DIR)
            text = ("The user attached " + str(len(paths)) + " picture(s); you can see them directly. Qwen can also read"
                    " them for free if you want a second look or a detailed description: run do.bat"
                    " \"<your question about it>\" --image <path> (saved at: " + "; ".join(paths) + ").\n\n"
                    + (text or "What is in the picture?"))
        if carried:
            text = ("Summary of this chat so far (Qwen wrote it to save tokens; treat it as your memory):\n" + carried
                    + "\n\nNow the user says:\n" + text)
        if not images:
            return text, bool(carried)
        blocks = [{"type": "image", "source": {"type": "base64", "media_type": im.get("media_type", "image/png"),
                                               "data": im["data"]}} for im in images]
        return blocks + [{"type": "text", "text": text or "What is in this picture?"}], bool(carried)

    def chat_send(self, sid, text, images=None, mode="claude") -> dict:
        try:
            sid = str(sid)
            if self._chat_working(sid):
                return {"ok": False, "error": WORKING_ELSEWHERE}
            text = text or ""
            images = _valid_pictures(images)
            content, used_summary = self._compose_chat_message(sid, text, images)
            line = json.dumps({"type": "user", "message": {"role": "user", "content": content}}) + "\n"
            try:
                _store_add(sid, "user", _user_line(text, images))   # first, so the reply always comes after it
            except Exception as exc:
                _log("could not save user message: " + repr(exc))
            st = None
            for attempt in (1, 2):  # one automatic retry: if the process died, start a fresh one
                st = self._claude_proc(sid, "auto" if mode == "auto" else "claude")
                if st is None:
                    self._save_answer(sid, NOT_SET_UP, "App")
                    return {"ok": False, "error": NOT_SET_UP}
                try:
                    with st["lock"]:  # a new turn: nothing left over from the previous one
                        del st["events"][:]
                        del st["live"][:]
                        st["busy"], st["stopped"] = True, False
                        st["last"] = st["started"] = time.time()
                        st["phase"] = "look" if images else "think"
                        st["line"], st["retried"] = line, False
                    st["proc"].stdin.write(line)
                    st["proc"].stdin.flush()
                    break
                except (OSError, ValueError) as exc:
                    _log("chat_send write failed (attempt {}): {}".format(attempt, repr(exc)))
                    st["busy"] = False
                    time.sleep(0.5)  # let the reader collect what the process said before it died
                    self._stop_chat(sid)
            else:
                err = NOT_SIGNED_IN if st and any(_needs_login(n) for n in st.get("noise", [])) else CANT_START
                self._save_answer(sid, err, "App")
                return {"ok": False, "error": err}
            if used_summary:
                _unlink(_side_file(sid, ".summary"))
            return {"ok": True}
        except Exception as exc:
            _log("chat_send failed: " + repr(exc))
            return {"ok": False, "error": CANT_START}

    def chat_poll(self, sid) -> list:
        try:
            with self._chats_lock:
                st = self._chats.get(str(sid))
            if not st:
                return []
            with st["lock"]:
                out, st["events"] = st["events"], []
            return [json.dumps(e) for e in out]
        except Exception:
            return []

    def chat_stop(self, sid) -> bool:
        """The Stop button: keep what was already written, note that it was stopped, end the process.
        Also stops a Quick/Local/Big/Polish job of this chat."""
        try:
            sid = str(sid)
            with self._chats_lock:
                st = self._chats.get(sid)
            if st:
                with st["lock"]:
                    if st.get("busy"):
                        partial = st["tr"].saved_text()
                        if partial:
                            self._save_reply(sid, partial)
                        self._save_answer(sid, "Stopped.", "Note")
                        st["busy"] = False
                        self._publish(st, [{"t": "done", "note": True, "text": "Stopped."}])
                    st["stopped"] = True
            job = self._stop_job(sid)
            return self._stop_chat(sid) or job
        except Exception as exc:
            _log("chat_stop failed: " + repr(exc))
            return False

    def _stop_chat(self, sid) -> bool:
        """End the chat's background process (nothing is saved). Its state stays until the next message, so
        the page can still collect the last events."""
        try:
            with self._chats_lock:
                st = self._chats.get(str(sid))
            if not st:
                return False
            st["stopped"] = True
            _kill_tree(st["proc"])
            return True
        except Exception as exc:
            _log("stopping chat process failed: " + repr(exc))
            return False

    def chat_save(self, sid, text) -> None:
        """Older pages saved the part shown before Stop themselves; chat_stop does that now."""
        try:
            with self._chats_lock:
                st = self._chats.get(str(sid))
            if st is None:
                self._save_reply(str(sid), str(text or ""))
        except Exception as exc:
            _log("chat_save failed: " + repr(exc))

    def _shutdown(self):
        """The window closed: stop every helper this app started."""
        with self._chats_lock:
            sids = list(self._chats)
        for sid in sids:
            self._stop_chat(sid)
        with self._jobs_lock:
            jids = list(self._jobs)
        for sid in jids:
            self._stop_job(sid, quiet=True)
        self.term_restart()
        with self._srv_lock:  # website previews: free their ports
            servers, self._servers = list(self._servers.values()), {}
        srv, self._ctl_srv = self._ctl_srv, None
        if srv is not None:  # control door: close it and remove its file so no helper knocks on a dead door
            servers.append(srv)
            try:
                if json.loads(_read_text(CTL_FILE) or "{}").get("port") == srv.server_address[1]:
                    _unlink(CTL_FILE)
            except Exception:
                pass
        for s in servers:
            try:
                s.shutdown()
                s.server_close()
            except Exception:
                pass

    # ---- hidden terminal mode (ConPTY via pywinpty) ----
    def term_start(self, cols: int = 100, rows: int = 30) -> dict:
        try:
            if self._pty is not None and self._pty.isalive():
                return {"status": "running"}
            exe = shutil.which("claude")
            if not exe:
                return {"status": NOT_SET_UP}
            from winpty import PtyProcess
            pty = PtyProcess.spawn([exe], cwd=str(ROOT), dimensions=(int(rows), int(cols)), env=_clean_env())
            self._pty = pty
        except Exception as exc:
            _log("terminal start failed: " + repr(exc))
            return {"status": "Could not start the terminal."}

        def reader(p):
            while True:
                try:
                    data = p.read(4096)
                except Exception:
                    break
                if not data:
                    if not p.isalive():
                        break
                    time.sleep(0.02)
                    continue
                with self._term_lock:
                    self._buf = (self._buf + data)[-200000:]
            with self._term_lock:
                self._buf += "\r\n[Closed. Click Restart.]\r\n"

        threading.Thread(target=reader, args=(pty,), daemon=True).start()
        return {"status": "started"}

    def term_read(self) -> str:
        with self._term_lock:
            out, self._buf = self._buf, ""
        return out

    def term_write(self, data: str) -> None:
        try:
            if self._pty is not None and self._pty.isalive():
                self._pty.write(data)
        except Exception as exc:
            _log("term_write failed: " + repr(exc))

    def term_resize(self, cols: int, rows: int) -> None:
        try:
            if self._pty is not None and self._pty.isalive():
                self._pty.setwinsize(int(rows), int(cols))
        except Exception:
            pass

    def term_restart(self) -> dict:
        if self._pty is not None:
            try:
                self._pty.terminate(force=True)
            except Exception:
                pass
        self._pty = None
        return {"status": "ok"}

    def open_claude(self) -> dict:
        try:
            subprocess.Popen(["claude"], cwd=str(ROOT), creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            return {"status": "Opened."}
        except Exception as exc:
            _log("open_claude failed: " + repr(exc))
            return {"status": NOT_SET_UP}


# ---------------------------------------------------------------------------
# Start-up
# ---------------------------------------------------------------------------
def _app_id() -> str:
    """Taskbar identity. Each look has its own, so the professional app never borrows the cyberpunk app's icon.
    The same ids are used by make_shortcut.ps1, so a shortcut and its running window group under one icon."""
    return "Aura.App.Pro" if _theme() == "hermes" else "Aura.App"


def _set_window_app_id(hwnd, icon: Path) -> None:
    """Tell the taskbar directly which app this window is and which icon to show (window property store).
    Needed because the venv's pythonw.exe is only a launcher: without this the taskbar may show the Python
    icon, a blank icon, or group the window under a different (missing) entry."""
    from ctypes import wintypes as w

    class GUID(ctypes.Structure):
        _fields_ = [("d1", ctypes.c_uint32), ("d2", ctypes.c_uint16), ("d3", ctypes.c_uint16), ("d4", ctypes.c_ubyte * 8)]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", w.DWORD)]

    class PROPVARIANT(ctypes.Structure):   # only the VT_LPWSTR case; padded to the full 24 bytes
        _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort), ("r3", ctypes.c_ushort),
                    ("val", ctypes.c_void_p), ("pad", ctypes.c_void_p)]

    def guid(d1, d2, d3, d4):
        return GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*d4))

    iid_store = guid(0x886D8EEB, 0x8CF2, 0x4446, (0x8D, 0x02, 0xCD, 0xBA, 0x1D, 0xBD, 0xCF, 0x99))
    fmt = guid(0x9F4C2855, 0x9F79, 0x4B39, (0xA8, 0xD0, 0xE1, 0xD4, 0x2D, 0xE1, 0xD5, 0xF3))
    py = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    values = (  # relaunch details first, the id last (setting the id makes the taskbar read the rest)
        (2, '"{}" "{}"'.format(py if py.exists() else sys.executable, ROOT / "app" / "app.py")),  # RelaunchCommand
        (4, "Aura"),                                                                  # RelaunchDisplayNameResource
        (3, str(icon) + ",0"),                                                        # RelaunchIconResource
        (5, _app_id()),                                                             # AppUserModel.ID
    )
    shell32 = ctypes.WinDLL("shell32")
    shell32.SHGetPropertyStoreForWindow.argtypes = [w.HWND, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    shell32.SHGetPropertyStoreForWindow.restype = ctypes.c_long
    store = ctypes.c_void_p()
    hr = shell32.SHGetPropertyStoreForWindow(hwnd, ctypes.byref(iid_store), ctypes.byref(store))
    if hr != 0 or not store.value:
        _log("icon: no window property store (hr={})".format(hr))
        return
    vtbl = ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    set_value = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(PROPERTYKEY),
                                   ctypes.POINTER(PROPVARIANT))(vtbl[6])
    commit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtbl[7])
    release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])
    try:
        for pid, text in values:
            buf = ctypes.create_unicode_buffer(text)
            pv = PROPVARIANT(31, 0, 0, 0, ctypes.cast(buf, ctypes.c_void_p), None)  # VT_LPWSTR
            hr = set_value(store, ctypes.byref(PROPERTYKEY(fmt, pid)), ctypes.byref(pv))
            if hr != 0:
                _log("icon: window property {} failed (hr={})".format(pid, hr))
        commit(store)
    finally:
        release(store)


def _force_taskbar_button(u, hwnd) -> None:
    """Make sure the window owns a normal taskbar button (app window, not a tool window)."""
    ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
    want = (ex | WS_EX_APPWINDOW) & ~WS_EX_TOOLWINDOW
    if want != ex:
        _log("icon: fixing window style for the taskbar ({:#x} -> {:#x})".format(ex & 0xFFFFFFFF, want & 0xFFFFFFFF))
        u.SetWindowLongW(hwnd, GWL_EXSTYLE, _as_long(want))
        if u.IsWindowVisible(hwnd):   # the taskbar only notices a style change when the window is shown again
            u.ShowWindow(hwnd, SW_HIDE)
            u.ShowWindow(hwnd, SW_SHOW)


def _start_size_guard(hwnd) -> None:
    """Without a title bar Windows makes the window about one title-bar taller on every minimize/restore.
    This watcher remembers the normal size and puts it back for one second after a restore (no window hook)."""
    def loop() -> None:
        u = _user32()
        from ctypes import wintypes as w
        last = None
        prev = "norm"
        guard_until = 0.0
        seen, still = None, 0     # the size counts as "the normal size" only after it stayed the same for 0.3 s
        while True:
            try:
                if not u.IsWindow(hwnd):
                    return
                state = "min" if u.IsIconic(hwnd) else ("max" if u.IsZoomed(hwnd) else "norm")
                now = time.monotonic()
                if state == "norm":
                    rc = w.RECT()
                    if u.GetWindowRect(hwnd, ctypes.byref(rc)):
                        cur = (rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)
                        if prev != "norm":
                            guard_until = now + 1.0
                        if now < guard_until and last and cur != last:
                            u.SetWindowPos(hwnd, None, last[0], last[1], last[2], last[3], SWP_NOZORDER | SWP_NOACTIVATE)
                        elif now >= guard_until:
                            still = still + 1 if cur == seen else 0
                            seen = cur
                            if still >= 10:
                                last = cur
                prev = state
            except Exception:
                pass
            time.sleep(0.03)
    threading.Thread(target=loop, daemon=True).start()


# ---------------------------------------------------------------------------
# Minimize animation: Aura flies into ITS OWN taskbar button and grows back out of it.
# In plain words:
#  - A hook only WATCHES the messages of Aura's window thread (it never changes or swallows one). The moment
#    Windows starts to minimize Aura (taskbar click, the app's own button, Win+D, ...) the window is still on
#    screen: the hook asks the helper thread to lay a "ghost" (a live picture of Aura drawn by Windows/DWM)
#    exactly over it, waits until that is on screen (at most 0.1 s), then lets the minimize go on. Windows' own
#    effect is off for Aura, so the window vanishes at once under the ghost and the ghost flies into the button.
#  - On restore the hook hides the window (DWM "cloak": it still gets its size and place) and the ghost grows
#    out of the button; at the end the window is shown under the ghost and then the ghost goes (no gap, no jump).
#  - The ghost is a click-through tool window: never a taskbar button, never focus, never clicks.
#  - Every frame is timed by the screen refresh (DwmFlush) and by the clock (time-based easing), in one thread.
#  - If anything is missing or fails, Windows' normal minimize/restore runs and the window is never left hidden.
# ---------------------------------------------------------------------------
_ANIM = {"ok": False, "button": None, "ask": None, "min": False, "back": None, "job": None, "ready": None,
         "tid": 0, "cloaked": False, "cloaked_at": 0.0, "keep": []}
ANIM_SECONDS = 0.25


def _anim_enabled() -> bool:
    """Windows' "Animation effects" setting (off = no animation at all)."""
    try:
        on = ctypes.c_int(0)
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0)   # SPI_GETCLIENTAREAANIMATION
        return bool(on.value)
    except Exception:
        return False


def _button_finder() -> None:
    """Keeps a hidden PowerShell open that finds Aura's own taskbar button by its app id (UI Automation,
    about 15 ms per question). Asked at every minimize and every few seconds; the answer is the button's middle."""
    app_id = _app_id().replace("'", "")
    script = (
        "Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes;"
        "$A=[Windows.Automation.AutomationElement];$P=[Windows.Automation.PropertyCondition];"
        "$id=New-Object $P($A::AutomationIdProperty,'Appid: " + app_id + "');"
        "$t1=New-Object $P($A::ClassNameProperty,'Shell_TrayWnd');"
        "$t2=New-Object $P($A::ClassNameProperty,'Shell_SecondaryTrayWnd');"
        "$tc=New-Object Windows.Automation.OrCondition($t1,$t2);"
        "while(($l=[Console]::In.ReadLine()) -ne $null){$o=@();"
        "try{foreach($t in $A::RootElement.FindAll('Children',$tc)){$e=$t.FindFirst('Descendants',$id);"
        "if($e){$r=$e.Current.BoundingRectangle;"
        "$o+=('{0} {1} {2} {3}' -f [int]$r.X,[int]$r.Y,[int]$r.Width,[int]$r.Height)}}}catch{}"
        "if($o.Count){$o=$o -join ';'}else{$o='none'};[Console]::Out.WriteLine($o);[Console]::Out.Flush()}"
    )
    ask = _ANIM["ask"]
    for _start in range(5):   # a helper that dies is started again a few times, then we give up (no animation)
        try:
            proc = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                    text=True, bufsize=1, **no_window.hidden())
        except Exception as exc:
            _log("anim: button finder failed to start: " + repr(exc))
            return
        try:
            while True:
                proc.stdin.write("?\n")
                proc.stdin.flush()
                line = proc.stdout.readline()
                if not line:
                    break
                found = []   # one button per taskbar (each screen can have its own taskbar)
                for item in line.strip().split(";"):
                    parts = item.split()
                    if len(parts) == 4:
                        x, y, bw, bh = (int(v) for v in parts)
                        if 8 <= bw <= 400 and 8 <= bh <= 400:
                            found.append((x, y, bw, bh))
                _ANIM["button"] = found or None   # None = not visible (auto-hidden taskbar...): Windows' own effect
                ask.wait(4.0)
                ask.clear()
        except Exception as exc:
            _log("anim: button finder stopped: " + repr(exc))
        finally:
            try:
                proc.kill()
            except Exception:
                pass
        _ANIM["button"] = None
        time.sleep(2)


def _start_minimize_anim(hwnd) -> None:
    """Switch the fly-to-taskbar minimize on (does nothing if Windows' animation effects are off)."""
    if not _anim_enabled():
        return
    _ANIM["ask"] = threading.Event()
    _ANIM["ready"] = threading.Event()
    threading.Thread(target=_button_finder, daemon=True).start()
    threading.Thread(target=_anim_thread, args=(hwnd,), daemon=True).start()


def _anim_thread(hwnd) -> None:
    from ctypes import wintypes as w
    try:
        u = ctypes.WinDLL("user32")   # own copies: these signatures do not touch _user32()
        dwm = ctypes.WinDLL("dwmapi")
        k32 = ctypes.WinDLL("kernel32")

        class WNDCLASSEXW(ctypes.Structure):
            _fields_ = [("cbSize", w.UINT), ("style", w.UINT), ("lpfnWndProc", ctypes.c_void_p),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int), ("hInstance", w.HINSTANCE),
                        ("hIcon", w.HICON), ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH),
                        ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR), ("hIconSm", w.HICON)]

        class THUMB(ctypes.Structure):   # DWM_THUMBNAIL_PROPERTIES
            _fields_ = [("dwFlags", w.DWORD), ("rcDestination", w.RECT), ("rcSource", w.RECT),
                        ("opacity", ctypes.c_ubyte), ("fVisible", w.BOOL), ("fSourceClientAreaOnly", w.BOOL)]

        class CWP(ctypes.Structure):     # what the hook sees: one message sent to a window
            _fields_ = [("lParam", w.LPARAM), ("wParam", w.WPARAM), ("message", w.UINT), ("hwnd", w.HWND)]

        class WPOS(ctypes.Structure):    # WINDOWPOS: where Windows is about to put the window
            _fields_ = [("hwnd", w.HWND), ("after", w.HWND), ("x", ctypes.c_int), ("y", ctypes.c_int),
                        ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", w.UINT)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", w.DWORD), ("rcMonitor", w.RECT), ("rcWork", w.RECT), ("dwFlags", w.DWORD)]

        u.MonitorFromRect.restype, u.MonitorFromRect.argtypes = w.HANDLE, [ctypes.POINTER(w.RECT), w.DWORD]
        u.GetMonitorInfoW.argtypes = [w.HANDLE, ctypes.POINTER(MONITORINFO)]
        HOOKPROC = ctypes.WINFUNCTYPE(w.LPARAM, ctypes.c_int, w.WPARAM, w.LPARAM)
        k32.GetModuleHandleW.restype, k32.GetModuleHandleW.argtypes = w.HMODULE, [w.LPCWSTR]
        u.RegisterClassExW.restype, u.RegisterClassExW.argtypes = w.ATOM, [ctypes.POINTER(WNDCLASSEXW)]
        u.CreateWindowExW.restype = w.HWND
        u.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
        u.SetLayeredWindowAttributes.argtypes = [w.HWND, w.DWORD, ctypes.c_ubyte, w.DWORD]
        u.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT]
        u.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
        u.IsWindow.argtypes = [w.HWND]
        u.IsIconic.argtypes = [w.HWND]
        u.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
        u.FindWindowW.restype, u.FindWindowW.argtypes = w.HWND, [w.LPCWSTR, w.LPCWSTR]
        u.GetWindowThreadProcessId.restype = w.DWORD
        u.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.c_void_p]
        u.SetWindowsHookExW.restype = w.HANDLE
        u.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, w.HINSTANCE, w.DWORD]
        u.CallNextHookEx.restype = w.LPARAM
        u.CallNextHookEx.argtypes = [w.HANDLE, ctypes.c_int, w.WPARAM, w.LPARAM]
        u.PostThreadMessageW.argtypes = [w.DWORD, w.UINT, w.WPARAM, w.LPARAM]
        u.PeekMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
        u.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
        u.DispatchMessageW.argtypes = [ctypes.POINTER(w.MSG)]
        dwm.DwmSetWindowAttribute.argtypes = [w.HWND, w.DWORD, ctypes.c_void_p, w.DWORD]
        dwm.DwmRegisterThumbnail.argtypes = [w.HWND, w.HWND, ctypes.POINTER(w.HANDLE)]
        dwm.DwmUpdateThumbnailProperties.argtypes = [w.HANDLE, ctypes.POINTER(THUMB)]
        dwm.DwmUnregisterThumbnail.argtypes = [w.HANDLE]
        dwm.DwmFlush.argtypes = []
        WM_ANIM = 0x8000 + 0x41   # WM_APP + n: "there is a new job"

        def transitions_off(h, off: bool) -> None:   # DWMWA_TRANSITIONS_FORCEDISABLED: Windows' own min/restore effect
            dwm.DwmSetWindowAttribute(h, 3, ctypes.byref(ctypes.c_int(1 if off else 0)), 4)

        def cloak(on: bool) -> None:   # DWMWA_CLOAK: the window keeps its place and taskbar button but is not drawn
            dwm.DwmSetWindowAttribute(hwnd, 13, ctypes.byref(ctypes.c_int(1 if on else 0)), 4)
            _ANIM["cloaked"] = on
            _ANIM["cloaked_at"] = time.monotonic()

        def rect_of(h):
            rc = w.RECT()
            if not u.GetWindowRect(h, ctypes.byref(rc)):
                return None
            return (rc.left, rc.top, rc.right, rc.bottom)

        # The ghost: borderless, always on top, click-through, no focus, no taskbar button, and no surface of its
        # own (only the DWM picture of Aura is drawn on it).
        inst = k32.GetModuleHandleW(None)
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = ctypes.cast(u.DefWindowProcW, ctypes.c_void_p).value
        wc.hInstance = inst
        wc.lpszClassName = "AuraMinimizeGhost"
        if not u.RegisterClassExW(ctypes.byref(wc)):
            _log("anim: class failed")
            return
        ex = 0x80 | 0x8 | 0x08000000 | 0x20 | 0x80000 | 0x00200000
        # TOOLWINDOW | TOPMOST | NOACTIVATE | TRANSPARENT | LAYERED | NOREDIRECTIONBITMAP
        ghost = u.CreateWindowExW(ex, "AuraMinimizeGhost", "", 0x80000000, 0, 0, 1, 1, None, None, inst, None)
        if not ghost:
            _log("anim: ghost window failed")
            return
        u.SetLayeredWindowAttributes(ghost, 0, 255, 0x2)   # LWA_ALPHA, fully visible (layered = click-through)
        transitions_off(ghost, True)
        state = {"thumb": None, "box": (0, 0, 1, 1), "cur": None, "op": 255}
        props = THUMB()
        props.fVisible = True

        def show_ghost(box) -> bool:
            """Lay the (still empty) ghost over the area the flight needs and connect the live picture."""
            if state["thumb"] is None:
                th = w.HANDLE()
                if dwm.DwmRegisterThumbnail(ghost, hwnd, ctypes.byref(th)) != 0:
                    return False
                state["thumb"] = th
            state["box"] = box
            u.SetWindowPos(ghost, w.HWND(-1), box[0], box[1], box[2] - box[0], box[3] - box[1], 0x10 | 0x40)
            return True

        def put(r, op: int) -> None:
            """Draw the picture of Aura at screen rect r with opacity op (0-255)."""
            bx = state["box"]
            props.dwFlags = 0x1 | 0x4 | 0x8   # destination, opacity, visible
            props.rcDestination = w.RECT(r[0] - bx[0], r[1] - bx[1], r[2] - bx[0], r[3] - bx[1])
            props.opacity = max(0, min(255, int(op)))
            dwm.DwmUpdateThumbnailProperties(state["thumb"], ctypes.byref(props))
            state["cur"], state["op"] = r, op

        def hide_ghost() -> None:
            u.ShowWindow(ghost, 0)   # SW_HIDE
            if state["thumb"] is not None:
                dwm.DwmUnregisterThumbnail(state["thumb"])
                state["thumb"] = None
            state["cur"] = None

        def monitor_of(r):
            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            mon = u.MonitorFromRect(ctypes.byref(w.RECT(*(int(v) for v in r))), 2)   # MONITOR_DEFAULTTONEAREST
            if mon and u.GetMonitorInfoW(mon, ctypes.byref(mi)):
                m = mi.rcMonitor
                return (m.left, m.top, m.right, m.bottom)
            return None

        def button_for(full):
            """Aura's button on the taskbar of the screen the window is on (each screen may show one)."""
            found = _ANIM["button"]
            if not found or not full:
                return None
            m = monitor_of(full)
            for b in found:
                cx, cy = b[0] + b[2] // 2, b[1] + b[3] // 2
                if m and m[0] <= cx < m[2] and m[1] <= cy < m[3]:
                    return b
            return None   # not on this screen's taskbar: Windows' own effect

        def icon_rect(full):
            """A small copy of the window's shape sitting in the middle of Aura's taskbar button."""
            b = button_for(full)
            if not b:
                return None
            cx, cy = b[0] + b[2] / 2, b[1] + b[3] / 2
            fw, fh = max(1, full[2] - full[0]), max(1, full[3] - full[1])
            ew = b[2] * 0.62
            eh = ew * fh / fw
            return (cx - ew / 2, cy - eh / 2, cx + ew / 2, cy + eh / 2)

        def flight_box(full):
            """The ghost covers the window and its whole screen (so the end point may move a little in flight)."""
            parts = [tuple(int(v) for v in full)]
            m = monitor_of(full)
            if m:
                parts.append(m)
            return (min(p[0] for p in parts), min(p[1] for p in parts),
                    max(p[2] for p in parts), max(p[3] for p in parts))

        def ease_in_out(p):
            return 4 * p * p * p if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2

        def ease_out(p):
            return 1 - (1 - p) ** 3

        def new_job_waiting() -> bool:
            msg = w.MSG()
            return bool(u.PeekMessageW(ctypes.byref(msg), None, WM_ANIM, WM_ANIM, 0))   # PM_NOREMOVE

        def fly(start, op0, target_fn, op_fn, ease, label) -> bool:
            """Move the picture from start to target_fn() (asked again every frame) on the screen's own clock.
            Returns False if a newer job arrived (it then continues from wherever the picture is now)."""
            t0 = time.perf_counter()
            stamps = [t0]
            done = True
            while True:
                p = min(1.0, (time.perf_counter() - t0) / ANIM_SECONDS)
                e = ease(p)
                end = target_fn()
                r = tuple(start[k] + (end[k] - start[k]) * e for k in range(4))
                put(tuple(int(round(v)) for v in r), op_fn(p, op0))
                dwm.DwmFlush()   # wait for the screen's next frame (vsync)
                stamps.append(time.perf_counter())
                if p >= 1.0:
                    break
                if new_job_waiting():
                    done = False
                    break
            gaps = [(b - a) * 1000 for a, b in zip(stamps, stamps[1:])]
            _log("anim: {} {} frames in {:.0f} ms, longest frame {:.1f} ms{}".format(
                label, len(gaps), (stamps[-1] - t0) * 1000, max(gaps) if gaps else 0, "" if done else " (interrupted)"))
            return done

        def do_minimize(full) -> None:
            start = state["cur"] or full     # a restore still in flight: carry on from where the picture is
            op0 = state["op"] if state["cur"] else 255
            if icon_rect(full) is None or not show_ghost(flight_box(full)):
                _ANIM["ready"].set()
                return
            put(start, op0)
            dwm.DwmFlush()                   # the ghost is now on screen, exactly over the window
            _ANIM["ready"].set()             # the hook lets the minimize go on

            def fade(p, o):                  # stays solid, then melts into the icon in the last third of the flight
                q = max(0.0, (p - 0.65) / 0.35)
                return o * (1 - q * q * (3 - 2 * q))
            last = [icon_rect(full)]

            def target():                    # follows a fresh button answer; keeps the last one if it goes missing
                last[0] = icon_rect(full) or last[0]
                return last[0]
            if fly(start, op0, target, fade, ease_in_out, "minimize"):
                hide_ghost()
            if _ANIM["cloaked"] and u.IsIconic(hwnd):
                cloak(False)                 # minimized again during a restore: nothing is drawn anyway

        def do_restore(full) -> None:
            if u.IsIconic(hwnd):             # minimized again before this job started: nothing to show
                if _ANIM["cloaked"]:
                    cloak(False)
                return
            small = state["cur"] or icon_rect(full)
            op0 = state["op"] if state["cur"] else 0
            if small is None or not show_ghost(flight_box(full)):
                cloak(False)
                return

            def target():
                return full

            def appear(p, o):                # comes out of the icon and is solid after the first 40 %
                q = min(1.0, p / 0.4)
                return o + (255 - o) * (1 - (1 - q) ** 2)
            if not fly(small, op0, target, appear, ease_out, "restore"):
                return                       # minimized again in flight: the next job takes over
            real = rect_of(hwnd)             # the window's true place (the size guard may have fixed a stretch)
            if real and real != tuple(int(round(v)) for v in full):
                put(real, 255)
                dwm.DwmFlush()
            cloak(False)                     # the window is drawn under the identical picture...
            dwm.DwmFlush()
            dwm.DwmFlush()
            hide_ghost()                     # ...and only then the picture goes: no gap, no jump

        # ---- the hook: runs on Aura's window thread, only watches ----
        def on_pos_changing(lparam) -> None:
            p = WPOS.from_address(lparam)
            if not _ANIM["min"]:
                if p.flags & 0x8000 and not p.flags & 0x2 and p.x <= -32000 and u.IsIconic(hwnd):
                    _ANIM["min"] = True      # Windows is minimizing Aura; the window is still on screen
                    full = rect_of(hwnd)
                    _ANIM["ask"].set()       # look up the button again (the answer may retarget the flight)
                    if not (full and full[0] > -30000 and icon_rect(full) and _anim_enabled()):
                        transitions_off(hwnd, False)   # no button known: Windows' own effect
                        _ANIM["back"] = None
                        return
                    transitions_off(hwnd, True)
                    _ANIM["back"] = full
                    _ANIM["ready"].clear()
                    _ANIM["job"] = ("min", full)
                    u.PostThreadMessageW(_ANIM["tid"], WM_ANIM, 0, 0)
                    if not _ANIM["ready"].wait(0.1):
                        _log("anim: ghost was late")
            elif not u.IsIconic(hwnd):
                _ANIM["min"] = False         # Windows is restoring Aura
                full = _ANIM["back"]
                if not (full and icon_rect(full) and _anim_enabled()):
                    transitions_off(hwnd, False)
                    return
                transitions_off(hwnd, True)
                cloak(True)                  # hidden until the picture has grown out of the icon
                _ANIM["job"] = ("res", full)
                u.PostThreadMessageW(_ANIM["tid"], WM_ANIM, 0, 0)

        def hook(code, wparam, lparam):
            try:
                if code >= 0:
                    c = CWP.from_address(lparam)
                    if c.message == 0x46 and c.hwnd == hwnd:   # WM_WINDOWPOSCHANGING
                        on_pos_changing(c.lParam)
            except Exception as exc:
                _log("anim hook: " + repr(exc))
            return u.CallNextHookEx(None, code, wparam, lparam)

        proc = HOOKPROC(hook)
        _ANIM["keep"].append(proc)   # the callback must stay alive as long as the hook
        msg = w.MSG()
        u.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)   # give this thread a message queue first
        _ANIM["tid"] = k32.GetCurrentThreadId()
        if not u.SetWindowsHookExW(4, proc, None, u.GetWindowThreadProcessId(hwnd, None)):   # WH_CALLWNDPROC
            _log("anim: hook failed")
            return
        _ANIM["ok"] = True
        threading.Thread(target=_anim_watchdog, args=(hwnd,), daemon=True).start()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_ANIM:
                job, _ANIM["job"] = _ANIM["job"], None
                if not job:
                    continue
                try:
                    (do_minimize if job[0] == "min" else do_restore)(job[1])
                except Exception as exc:
                    _log("anim job failed: " + repr(exc))
                    _ANIM["ready"].set()
                    try:
                        hide_ghost()
                        if _ANIM["cloaked"]:
                            cloak(False)
                    except Exception:
                        pass
            else:
                u.DispatchMessageW(ctypes.byref(msg))
    except Exception as exc:
        _log("anim failed: " + repr(exc))
    _ANIM["ok"] = False
    try:   # never leave the window hidden or without Windows' own effect
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 13, ctypes.byref(ctypes.c_int(0)), 4)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 3, ctypes.byref(ctypes.c_int(0)), 4)
        _ANIM["cloaked"] = False
    except Exception:
        pass


def _anim_watchdog(hwnd) -> None:
    """Safety net: a window that is restored but still hidden after 1.5 s is shown again."""
    u = _user32()
    while True:
        time.sleep(0.25)
        try:
            if not u.IsWindow(hwnd):
                return
            if _ANIM["cloaked"] and time.monotonic() - _ANIM["cloaked_at"] > 1.5 \
                    and not ctypes.windll.user32.IsIconic(hwnd):
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 13, ctypes.byref(ctypes.c_int(0)), 4)
                _ANIM["cloaked"] = False
                _log("anim: window was still hidden after a restore, shown again")
        except Exception:
            pass


def _set_window_icon() -> None:
    """Give the window and taskbar the Aura icon and drop the native title bar (Windows only)."""
    try:
        u = _user32()
        icon = ROOT / "app" / ("aura_pro3.ico" if _theme() == "hermes" else "aura_icon4.ico")  # each look has its own icon
        custom = os.environ.get("AURA_ICON", "")  # a launcher can name its own icon file (inside the app folder)
        if custom and (ROOT / "app" / Path(custom).name).exists():
            icon = ROOT / "app" / Path(custom).name
        if not icon.exists():
            icon = ROOT / "app" / "icon.ico"
        hwnd = _wait_for_window("Aura", 240, 0.25)  # a slow first start can take a while
        if not hwnd:
            _log("icon: window not found")
            return
        try:
            _set_window_app_id(hwnd, icon)
        except Exception as exc:
            _log("icon: app id failed: " + repr(exc))
        try:
            _force_taskbar_button(u, hwnd)
        except Exception as exc:
            _log("icon: taskbar button check failed: " + repr(exc))

        def put_icon() -> None:
            big = u.LoadImageW(None, str(icon), 1, 256, 256, 0x10)    # IMAGE_ICON, LR_LOADFROMFILE
            small = u.LoadImageW(None, str(icon), 1, 32, 32, 0x10)
            if not big or not small:
                _log("icon: could not load {}".format(icon))
            u.SendMessageW(hwnd, 0x80, 1, big or 0)    # WM_SETICON, ICON_BIG
            u.SendMessageW(hwnd, 0x80, 0, small or 0)  # WM_SETICON, ICON_SMALL
            try:  # also tell the window frame itself (the taskbar button reads this one)
                import webview
                from System import Action
                from System.Drawing import Icon
                form = webview.windows[0].native
                ico = Icon(str(icon))
                form.Invoke(Action(lambda: setattr(form, "Icon", ico)))
            except Exception as exc:
                _log("icon (form) failed: " + repr(exc))

        put_icon()

        def again() -> None:  # the window library may paint its own icon a moment later: set ours again
            for pause in (2, 4, 9):
                time.sleep(pause)
                try:
                    if u.IsWindow(hwnd):
                        put_icon()
                except Exception:
                    return
        threading.Thread(target=again, daemon=True).start()
        # Drop the caption but keep the resize frame (the page draws its own top strip).
        style = u.GetWindowLongW(hwnd, GWL_STYLE)
        u.SetWindowLongW(hwnd, GWL_STYLE, _as_long(style & ~WS_CAPTION))
        u.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED)
        _start_size_guard(hwnd)
        _start_minimize_anim(hwnd)   # fly into the taskbar button (no-op if animations are off)
        # Windows 11 paints a lighter, see-through strip at the top (the hidden caption bar) and a colored
        # border: paint both in the app's own dark color (COLORREF 0x00BBGGRR for #060709) and use no backdrop.
        try:
            dwm = ctypes.windll.dwmapi
            for attr, val in ((35, 0x090706),   # DWMWA_CAPTION_COLOR
                              (34, 0x090706),   # DWMWA_BORDER_COLOR
                              (38, 1)):         # DWMWA_SYSTEMBACKDROP_TYPE = none
                dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(ctypes.c_int(val)), 4)
        except Exception as exc:
            _log("window color failed: " + repr(exc))
    except Exception as exc:
        _log("icon failed: " + repr(exc))


def _window_geometry():
    """Window size and position, centered on the screen. Windows is asked directly because the window
    library reports a wrong screen size on this setup; values are converted to scaled units."""
    try:
        u = _user32()
        scale = max(1.0, (u.GetDpiForSystem() or 96) / 96)
        lw = u.GetSystemMetrics(0) / scale
        lh = u.GetSystemMetrics(1) / scale
        width = max(980, int(lw * 0.68))
        height = max(640, int(min(lh * 0.74, lh - 80)))
        x = max(0, int((lw - width) / 2))
        y = max(0, int((lh - height) / 2) - 20)  # a little higher, leaving room for the taskbar
        return width, height, x, y
    except Exception:
        return 1440, 900, None, None  # None = let the system place it


def _keep_links_inside(api_obj) -> None:
    """Links that ask for a new window (target=_blank, YouTube results...) used to jump to the normal
    browser. Now: inside the page view they load in that view; in the main window they open in the pane."""
    try:
        from webview.platforms import edgechromium

        def on_new_window(self, sender, args):
            args.set_Handled(True)
            uri = str(args.get_Uri())
            if not uri.lower().startswith(("http://", "https://")):
                return
            try:
                if not args.get_IsUserInitiated():
                    return  # a page opening windows by itself (Bing does) is ignored; only real clicks count
            except Exception:
                pass
            title = getattr(self.pywebview_window, "title", "")
            if title == "Aura":
                api_obj._queue_open(uri)  # the page shows it in the browser pane
            else:
                self.load_url(uri)         # stay in the same view

        edgechromium.EdgeChrome.on_new_window_request = on_new_window
    except Exception as exc:
        _log("keep_links_inside failed: " + repr(exc))


def _read_ollama_url() -> None:
    """A PC that uses another PC's graphics card keeps the address in ollama_url.txt."""
    try:
        url = ollama_addr.read_address(ROOT)   # also fixes a typed "192.168.1.6" (no http://, no port)
        os.environ["OLLAMA_URL"] = url
        if (ROOT / "ollama_url.txt").exists():
            _log("local helper address: " + url)
    except Exception as exc:
        _log("ollama_url.txt failed: " + repr(exc))


def _theme() -> str:
    """'hermes' = professional look; anything else = cyberpunk. theme.txt is optional."""
    t = os.environ.get("AURA_THEME", "")
    try:
        f = ROOT / "theme.txt"
        if not t and f.exists():
            t = f.read_text(encoding="utf-8-sig").strip()
    except OSError:
        pass
    return t.lower()


def _ensure_shortcut() -> None:
    """Put an Aura icon on the desktop if there is none, and fix an old one that lacks the Aura app id/icon
    (installed copies have make_shortcut.ps1)."""
    try:
        script = ROOT / "make_shortcut.ps1"
        if script.exists():
            subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-IfMissing"],
                             **_hidden())
    except Exception as exc:
        _log("shortcut failed: " + repr(exc))


_instance_lock = None


def _already_running() -> bool:
    """One Aura per install folder: if it is open already, bring that window to the front and say so."""
    global _instance_lock
    try:
        import hashlib
        k = ctypes.windll.kernel32
        k.CreateMutexW.restype = ctypes.c_void_p
        name = "Local\\AuraApp_" +hashlib.md5((str(ROOT).lower() + "|" + _theme()).encode("utf-8")).hexdigest()[:16]
        _instance_lock = k.CreateMutexW(None, False, name)
        if ctypes.GetLastError() != 183:  # 183 = the mutex existed already
            return False
        u = ctypes.windll.user32
        hwnd = u.FindWindowW(None, "Aura")
        if hwnd:
            if u.IsIconic(hwnd):
                u.ShowWindow(hwnd, 9)  # restore a minimized window
            u.SetForegroundWindow(hwnd)
        return True
    except Exception as exc:
        _log("single-instance check failed: " + repr(exc))
        return False


def main() -> None:
    import webview

    updated = _wait_for_old_aura(sys.argv[1:])  # after an update: let the old window close first
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--autoplay-policy=no-user-gesture-required")  # videos may start without a click
    if _already_running():
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_app_id())  # before any window exists
    except Exception:
        pass
    for folder in (CHATS_DIR, LOG_FILE.parent):  # a fresh install has neither
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    _read_ollama_url()
    _ensure_shortcut()

    width, height, x, y = _window_geometry()
    api_obj = Api()
    if updated:
        api_obj._updated_to = _local_version()
        _log("started after update, v" + api_obj._updated_to)
    _keep_links_inside(api_obj)
    try:
        api_obj._ctl_srv = _start_control(api_obj)
    except Exception as exc:
        _log("control server failed: " + repr(exc))

    api_obj._main_win = webview.create_window(
        "Aura",
        url=str(ROOT / "app" / ("web_hermes" if _theme() == "hermes" else "web") / "index.html"),
        js_api=api_obj,
        width=width,
        height=height,
        x=x,
        y=y,
        min_size=(760, 520),
        background_color="#060709",
        text_select=True,
    )
    try:
        webview.start(_set_window_icon)
    finally:
        api_obj._shutdown()
        if api_obj._restarting:  # the new Aura is waiting for this process to end: do not linger
            os._exit(0)


if __name__ == "__main__":
    main()
