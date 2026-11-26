"""Qwen does things on the PC for you.  Usage:  python agent.py "download X to my Downloads folder" [--image PATH ...]

Qwen (through Ollama tool calling) can run PowerShell commands, open things, read and write files.
Safety rules (your rules): no deleting (only to the Recycle Bin), no Docker, no shutdown/format/registry/disk commands.
Everything is logged to logs/agent.log.

Inside the Atlas app (env AURA_APP is set) the step lines are hidden, Atlas's identity rules are added and
the open_website tool is offered only when the request is about browsing.
"""
import base64
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

# Emojis/accents must never crash the output when it is piped (e.g. into the Atlas app).
for _stream in (sys.stdout, sys.stderr):
    try:
        if not _stream.isatty():
            _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / "src"))


def _ollama_url() -> str:
    try:
        from ollama_addr import read_address
        return read_address(ROOT)
    except Exception:
        return os.environ.get("OLLAMA_URL", "http://localhost:11434")


def _env_num(name, default):
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return float(default)


HOME = Path.home()
LOG = ROOT / "logs" / "agent.log"
IN_AURA = bool(os.environ.get("AURA_APP"))
PROGRESS = bool(os.environ.get("AURA_PROGRESS"))   # the Atlas app shows live step lines (lines starting with @@aura)
PROGRESS_PREFIX = "@@aura "
OLLAMA_URL = _ollama_url()
NUM_CTX = int(_env_num("QWEN_NUM_CTX", 65536))
MAX_STEPS = 30
STARTED = time.time()
DEADLINE = _env_num("AGENT_DEADLINE", 0)           # seconds for the whole task (0 = no limit); the app sets it
CONNECT_TIMEOUT = 10                               # a PC that is off or blocked must not keep us waiting
READ_GAP = _env_num("AGENT_READ_GAP", 240)         # longest silence from the helper (it streams, so silence = stuck)


def _model_name() -> str:
    if os.environ.get("AGENT_MODEL"):
        return os.environ["AGENT_MODEL"]
    try:
        return (ROOT / "model.txt").read_text(encoding="utf-8").strip() or "qwen3.8:27b"
    except OSError:
        return "qwen3.8:27b"


MODEL = _model_name()

try:
    from no_window import run_capture
except Exception:  # helper missing: plain behavior (may wait for programs the command opened)
    def run_capture(cmd, timeout, cwd=None, **_):
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, cwd=cwd,
                           stdin=subprocess.DEVNULL)
        return r.stdout, r.stderr, r.returncode, None
try:
    from step_text import agent_step
except Exception:
    def agent_step(name, args):
        return {"kind": "work", "text": "Working on it"}
try:
    from detail_text import agent_detail, clip as clip_detail, MAX_OUT as DETAIL_OUT
except Exception:  # the Details view just gets nothing
    def agent_detail(name, args):
        return ""

    def clip_detail(text, limit):
        return str(text)[:limit]
    DETAIL_OUT = 700
try:
    from browse_policy import wants_browser
except Exception:  # if the helper is missing, behave as before (browsing allowed)
    def wants_browser(text):
        return True
try:
    from think_policy import needs_thinking
except Exception:  # never let the helper stop the agent
    def needs_thinking(text, has_images=False):
        return False

BROWSE_OK = True  # set per task in main()

BLOCKED = re.compile(
    r"(remove-item|\brm\b|\bdel\b|\berase\b|\brmdir\b|\brd\b|\bdocker|clear-content|clear-disk|format-|"
    r"\bformat\b|stop-computer|restart-computer|\bshutdown\b|\bdiskpart\b|\breg(\.exe)?\s|"
    r"remove-|set-executionpolicy|\bcipher\b|\bicacls\b|\btakeown\b|\bnet\s+user\b|\bbcdedit\b|"
    r"out-file.*-force|set-content|\bmove-item\b|\bmv\b|\bren\b|rename-item)",
    re.I,
)
BROWSER_LAUNCH = re.compile(
    r"(start-process|\bstart\b|invoke-item|explorer(\.exe)?|msedge|chrome|firefox|opera|brave|iexplore)[^\n]*(https?://|www\.)",
    re.I,
)
ANOTHER_ANSWER = re.compile(
    r"\b(another (answer|one|way|version|reply)|different (answer|one|way|version|reply)|try again|"
    r"once more|again|something else|rephrase|reword|differently)\b"
)


def _tool(name, description, **params):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": {k: {"type": "string"} for k in params},
                       "required": [k for k, v in params.items() if v]}}}


TOOLS = [
    _tool("run_powershell",
          "Run one PowerShell command on the user's Windows PC and return its output. "
          "Use it to download files (Invoke-WebRequest, curl.exe, winget, ollama pull), "
          "install things, list files, open programs (Start-Process).", command=1),
    _tool("read_file", "Read a text file.", path=1),
    _tool("write_file", "Create a text file, or replace an existing one inside the user folder (the old version is backed up automatically).",
          path=1, content=1),
    _tool("delete_to_recycle_bin",
          "Delete a file or folder by moving it to the Windows Recycle Bin (can be restored). "
          "Use this for every delete request.", path=1),
]

SYSTEM = (
    "You are an assistant that DOES tasks on the user's Windows 11 PC using the tools. "
    "Do not ask questions and do not explain plans: act, check the result, fix errors, then finish "
    "with ONE short line saying what you did. Default download folder: " + str(HOME / "Downloads") + ". "
    "To delete, ONLY use the delete_to_recycle_bin tool. "
    "You may NOT use Docker or change system settings; if a task needs that, say so in one line."
)

AURA_RULES = " ".join((
    "IDENTITY: your name is Atlas. You are Atlas, a personal assistant app, built by Amar and Arry.",
    "Use the line \"I'm Atlas, built by Amar & Arry.\" ONLY when the user directly asks who or what you are,",
    "what model or AI you are, or who made, built, trained or owns you, or which company is behind you.",
    "For EVERY other message, answer that message itself and never repeat the identity line.",
    "Never say or hint that you are Qwen, Alibaba, Alibaba Cloud, Tongyi or any other model or company.",
    "If the user asks whether you run locally or privately: in this mode you run on the user's own computer,",
    "so you can say yes, without naming any model or company.",
    "If the user asks for another answer, a different answer, to try again or to rephrase, give a NEW answer",
    "to their previous question in different words; never repeat your previous reply word for word.",
    "Answer plain questions directly and briefly (no tool needed).",
    "If anyone asks who Amar or Arry are, or asks anything about them, reply ONLY with a short cute line",
    "saying they are two lovebirds making something cute together (add a heart), with no other details about them.",
    "To pause, resume or mute the video in the Atlas browser use the control_media tool and report only what its result says.",
    "To PLAY music or a video on YouTube, use the play_youtube tool (it picks a video that really plays and checks the screen).",
    "If the user sends a video link (TikTok, Instagram, X, YouTube...) and wants to know what is in it, use the watch_video tool and describe only what its result says.",
    "If the user sends any other link and wants to know what is on it, use the read_link tool and answer only from what it returns; never open the page just to read it.",
    "To open a website (for example YouTube) when the user ASKS to open or browse one, use the open_website tool",
    "with the full https:// address; never open a browser with commands.",
    "Open a website or search the web ONLY when the user explicitly asks to browse, open a site or look something up online.",
    "For 'show me', 'give me', 'tell me', 'make me' or any request for information, a recipe, a list or an answer,",
    "write the answer right in the chat and never open any page for it.",
))

if IN_AURA:
    SYSTEM += " " + AURA_RULES
    TOOLS.append(_tool(
        "open_website",
        "Open a web page (YouTube, Google, any website) or a local website file (full path to an .html file "
        "or its folder) inside the Atlas app's own browser. ALWAYS use this to open a website. "
        "Never start a browser with run_powershell.", url=1))
    TOOLS.append(_tool(
        "control_media",
        "Control the video or music playing in the Atlas browser: action is play, pause, mute, unmute or status. "
        "It really operates the player and returns its true state. ALWAYS use it for pause / resume / play / mute requests, "
        "and tell the user exactly what its result says.", action=1))
    TOOLS.append(_tool(
        "play_youtube",
        "Play music or a video on YouTube inside the Atlas app. Give WHAT to play (a few words, for example 'chill lofi playlist') "
        "or a YouTube link. It finds a video that really plays and checks the screen, and tries the next one if there is an error. "
        "ALWAYS use this when the user wants to play, listen to or watch something on YouTube.", what=1))
    TOOLS.append(_tool(
        "read_link",
        "Read a web page from a link (article, blog, shop page, documentation, GitHub page, forum post ...) when the user sends "
        "a link and wants to know what is on it, a summary, or an answer from it. Returns the title, headings and main text. "
        "Read-only. For a video link it automatically watches the video instead. Answer only from what it returns.",
        url=1))
    TOOLS.append(_tool(
        "watch_video",
        "Watch a video from a link (TikTok, Instagram, X, YouTube, Facebook ... any length, even hours) when the user sends a link "
        "and wants to know what is in it, or says 'watch this'. It listens to ALL the speech, looks at pictures spread through the "
        "whole video and returns an overview, key moments and a timeline. Long videos keep working in the background: if the "
        "result says it is still watching, tell the user plainly and call it again with the same link later. Do NOT use it to play "
        "music (use play_youtube).",
        url=1))
    TOOLS.append(_tool(
        "generate_image",
        "Make a picture from a description. Use it when the user asks you to draw, make, create or generate a picture, image, logo, "
        "wallpaper, poster, avatar or illustration. Write 'prompt' as a rich, concrete description (subject, setting, style, lighting, "
        "composition, mood) based on what the user asked, in English, and include any text the picture must show exactly. Draw exactly what was asked: fan art of existing fictional characters (anime, games, comics, movies) is allowed, so never refuse or swap in an original character and never mention copyright; real, well-known people (celebrities, athletes, public figures) are allowed too. Do not add your own filters or warnings: the picture service has its own safety filter, and if it declines, tell the user plainly and do not retry with disguised names. Do not make anything sexual involving children or childlike characters, sexual or nude pictures of real people, or pictures meant to deceive. 'size' is optional: "
        "words like square, wide, tall, 4k or an exact size such as 1536x1024. 'quality' is optional: low, medium (default), high. Then "
        "show the picture by putting the exact image line from the result into your answer, and add nothing else about it.",
        prompt=1, size=0, quality=0))
    TOOLS.append(_tool(
        "ask_video",
        "Answer a specific question about a video that was sent as a link (what happens at 20:00, what did he say about X, what "
        "text was on screen, who appears...). It finds the exact speech and pictures in the saved analysis (it watches the video "
        "first if that has not happened yet). Answer only from what it returns.",
        url=1, question=1))


def log(msg: str) -> None:
    try:
        LOG.parent.mkdir(exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except OSError:
        pass


# ---- opening websites inside Atlas (through the app's local control door) ----
def _local_target(url: str):
    """A local file/folder path hidden in `url`, or None if it is a web address."""
    if url.lower().startswith("file:"):
        from urllib.parse import unquote, urlparse
        return unquote(urlparse(url).path).lstrip("/")
    if re.match(r"^[A-Za-z]:[\\/]", url):
        return url
    try:
        if url and not url.lower().startswith(("http://", "https://")) and Path(url).expanduser().exists():
            return url
    except OSError:
        pass
    return None


def open_in_aura(url: str) -> str:
    """Send a web address or a local website file to the Atlas app's browser (it shows it in the pane)."""
    url = (url or "").strip().strip('"').strip("'")
    local = _local_target(url)
    params = {}
    if local:
        p = Path(local).expanduser()
        if p.is_dir():
            for name in ("index.html", "index.htm"):
                if (p / name).exists():
                    p = p / name
                    break
        if not p.is_file():
            return "That file was not found: " + str(p)
        params["path"] = shown = str(p)
    else:
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        params["url"] = shown = url
    try:
        info = json.loads((ROOT / "logs" / "aura_ctl.json").read_text(encoding="utf-8"))
        params["token"] = info["token"]
        r = requests.get("http://127.0.0.1:%d/open" % int(info["port"]), params=params, timeout=5)
        if r.status_code == 200:
            return "Opened in the Atlas browser: " + shown
    except Exception as exc:
        log("open_in_aura failed: " + repr(exc))
    return "The Atlas browser is not available right now, so nothing was opened (the normal browser is never used): " + shown


# ---- YouTube, the smart way: only open videos that really play, then check the screen ----
_YT_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
               "Accept-Language": "en-US,en;q=0.9", "Cookie": "CONSENT=YES+1; SOCS=CAI"}


def _yt_id(text: str) -> str:
    m = re.search(r"(?:youtube\.com/watch\?[^\s]*?v=|youtu\.be/)([A-Za-z0-9_-]{11})", text or "")
    return m.group(1) if m else ""


def _yt_status(vid: str) -> str:
    """'OK' when YouTube says the video can be played, else its status (UNPLAYABLE, ERROR, LOGIN_REQUIRED...); '?' if unknown."""
    try:
        r = requests.get("https://www.youtube.com/watch?v=" + vid, headers=_YT_HEADERS, timeout=15)
        m = re.search(r'"playabilityStatus":\{"status":"([A-Z_]+)"', r.text)
        return m.group(1) if m else "?"
    except Exception as exc:
        log("yt status failed: " + repr(exc))
        return "?"


def _yt_search(query: str) -> list[str]:
    try:
        r = requests.get("https://www.youtube.com/results", params={"search_query": query}, headers=_YT_HEADERS, timeout=15)
        ids: list[str] = []
        for m in re.finditer(r'"videoId":"([A-Za-z0-9_-]{11})"', r.text):
            if m.group(1) not in ids:
                ids.append(m.group(1))
        return ids[:10]
    except Exception as exc:
        log("yt search failed: " + repr(exc))
        return []


def _yt_title(vid: str) -> str:
    try:
        r = requests.get("https://www.youtube.com/oembed", params={"url": "https://www.youtube.com/watch?v=" + vid, "format": "json"}, timeout=10)
        return str(r.json().get("title", ""))[:120] if r.status_code == 200 else ""
    except Exception:
        return ""


def _screen_shows_error() -> bool | None:
    """Look at the Atlas window (picture) and ask the vision model whether the player shows an error. None = could not tell."""
    try:
        info = json.loads((ROOT / "logs" / "aura_ctl.json").read_text(encoding="utf-8"))
        r = requests.get("http://127.0.0.1:%d/shot" % int(info["port"]), params={"token": info["token"]}, timeout=20)
        if r.status_code != 200:
            return None
        img = base64.b64encode((ROOT / "logs" / "aura_shot.png").read_bytes()).decode("ascii")
        body = {"model": MODEL, "stream": False, "think": False, "options": {"num_predict": 8, "temperature": 0},
                "messages": [{"role": "user", "images": [img], "content":
                              "Look at the video player inside this app window. Does it show an error such as 'Video unavailable', "
                              "'This video is not available', a black screen with an error message, or a sign-in/consent wall instead of "
                              "a video? Answer with exactly one word: YES or NO."}]}
        a = requests.post(f"{OLLAMA_URL}/api/chat", json=body, timeout=90).json()
        return "YES" in str(a.get("message", {}).get("content", "")).upper()
    except Exception as exc:
        log("screen check failed: " + repr(exc))
        return None


def play_youtube(what: str) -> str:
    """Play something on YouTube inside Atlas: a search, or a link. Skips videos YouTube says cannot play, and videos whose
    player shows an error on screen, and tries the next result."""
    what = (what or "").strip()
    first = _yt_id(what)
    q = what
    m = re.search(r"search_query=([^&\s]+)", what)
    if m:
        from urllib.parse import unquote_plus
        q = unquote_plus(m.group(1))
    elif first:
        q = ""
    elif what.lower().startswith(("http://", "https://")):
        mv = re.search(r"[?&]v=([^&\s]+)", what)  # a made-up link like watch?v=lofi: use its words as the search
        q = mv.group(1).replace("-", " ").replace("_", " ") if mv else ""
    cands = ([first] if first else []) + [v for v in (_yt_search(q) if q else []) if v != first]
    if not cands:
        return "I could not find any video for that on YouTube."
    tried = []
    for vid in cands[:8]:
        st = _yt_status(vid)
        if st not in ("OK", "?"):
            tried.append(vid + ":" + st)
            continue
        url = "https://www.youtube.com/watch?v=" + vid + "&autoplay=1"
        res = open_in_aura(url)
        if "not available" in res and "Opened" not in res:
            return res
        time.sleep(8)  # let the page and player load, then look at the real screen
        bad = _screen_shows_error()
        log(f"play_youtube {vid}: status={st} screen_error={bad}")
        if bad:
            tried.append(vid + ":screen error")
            continue
        mres = control_media("play")  # start it if the site waited for a click, and read the real state
        really = "is now playing" in mres
        title = _yt_title(vid)
        if not really:
            return ("Opened" + (" \"" + title + "\"" if title else "") + " in Atlas but it is NOT playing yet (" + mres + "). "
                    "Tell the user to click the video once.")
        how = "I looked at the screen and it plays" if bad is False else "YouTube says it can play; I could not look at the screen"
        return ("Playing" + (f" \"{title}\"" if title else "") + " on YouTube in Atlas (" + how + "). "
                "If there is no sound, click the video once: the browser may block autoplay until you click.")
    return "I tried " + str(len(tried)) + " videos but none of them would play (" + ", ".join(tried)[:200] + "). Try a different search."


def control_media(action: str) -> str:
    """Play, pause, mute... the video shown in Atlas, and report what the player REALLY does afterwards."""
    action = (action or "").strip().lower()
    action = {"resume": "play", "start": "play", "stop": "pause", "unpause": "play", "silence": "mute"}.get(action, action)
    if action not in ("status", "play", "pause", "toggle", "mute", "unmute"):
        return "Unknown action. Use play, pause, mute, unmute or status."
    try:
        info = json.loads((ROOT / "logs" / "aura_ctl.json").read_text(encoding="utf-8"))
        r = requests.get("http://127.0.0.1:%d/media" % int(info["port"]), params={"token": info["token"], "action": action}, timeout=30)
        d = r.json()
    except Exception as exc:
        return "I could not reach the video in Atlas: " + repr(exc)
    if not d.get("ok"):
        return d.get("status", "I could not control the video.")
    state = "paused" if d.get("paused") else "playing"
    out = f"The video is now {state}" + (", muted" if d.get("muted") else "") + f" (at {d.get('time', 0)} s)."
    want = {"play": "playing", "pause": "paused"}.get(action)
    if want and want != state:
        out += f" That is NOT what was asked ({action}): tell the user it did not work."
    return out


# ---- tools ----
def _run_powershell(cmd: str) -> str:
    if not BROWSE_OK and BROWSER_LAUNCH.search(cmd):
        return "REFUSED: the user did not ask to browse the web. Answer in the chat instead."
    if BLOCKED.search(cmd):
        return "REFUSED: this command can delete/change things and is not allowed."
    limit = 1800 if not DEADLINE else max(20, min(1800, DEADLINE - (time.time() - STARTED) - 30))
    # stdin is empty (a command waiting for typed input gets nothing instead of hanging), and programs the
    # command opens (notepad, a browser...) no longer keep us waiting until they are closed.
    out, err, code, reason = run_capture(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                                         limit, cwd=str(HOME))
    if reason == "timeout":
        return "ERROR: the command took too long and was stopped. " + ((out or "") + (err or ""))[-1500:]
    return ((out or "") + (err or ""))[-3000:] or f"(done, exit code {code})"


def _write_file(path: str, content: str) -> str:
    p = Path(path)
    inside = ROOT.resolve() in p.resolve().parents
    note = ""
    if p.exists() and not inside:
        # Changing an existing file is allowed only inside the user folder, and the old version is kept first.
        rp = p.resolve()
        if not rp.is_file() or HOME.resolve() not in rp.parents:
            return "REFUSED: file already exists, not overwriting."
        try:
            import shutil
            keep = HOME / "Aura" / "overwritten"
            keep.mkdir(parents=True, exist_ok=True)
            saved = keep / (time.strftime("%Y%m%d_%H%M%S_") + rp.name)
            shutil.copy2(rp, saved)
        except OSError as e:
            return f"REFUSED: could not save a backup of the old file first ({e})."
        note = f" (old version saved to {saved})"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"written: {p}{note}"


def _delete_to_recycle_bin(path: str) -> str:
    p = Path(path).expanduser().resolve()
    if HOME.resolve() not in p.parents or p == ROOT.resolve() or ROOT.resolve() in p.parents:
        return "REFUSED: only files inside your user folder (not the qwen-test folder) can be deleted."
    if not p.exists():
        return f"ERROR: {p} does not exist."
    kind = "DeleteDirectory" if p.is_dir() else "DeleteFile"
    quoted = str(p).replace("'", "''")
    ps = ("Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::"
          f"{kind}('{quoted}','OnlyErrorDialogs','SendToRecycleBin')")
    _out, err, code, reason = run_capture(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], 120)
    if reason == "timeout":
        return "ERROR: moving it to the Recycle Bin took too long."
    return f"moved to Recycle Bin: {p}" if code == 0 else f"ERROR: {(err or '')[-500:]}"


def _read_file(path: str, limit: int = 12000) -> str:
    """Text of a file. Plain text, code, PDF (pypdf) and Word .docx are understood."""
    p = Path(path)
    ext = p.suffix.lower()
    if not p.exists():
        return f"ERROR: {p} does not exist."
    if p.is_dir():
        return f"ERROR: {p} is a folder, not a file. List it with run_powershell (Get-ChildItem) instead."
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            return "ERROR: PDF reading is not installed on this PC (run install.bat again)."
        reader = PdfReader(str(p))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return "ERROR: this PDF is locked with a password."
        out, size = [], 0
        for i, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or "").strip()
            out.append(f"--- page {i} ---" + chr(10) + text)
            size += len(text)
            if size >= limit:
                out.append(f"(stopped after page {i} of {len(reader.pages)})")
                break
        if not size:
            return f"(This PDF has {len(reader.pages)} page(s) but no readable text; it is probably scanned pictures.)"
        return chr(10).join(out)[:limit]
    if ext in (".exe", ".dll", ".zip", ".7z", ".rar", ".msi", ".iso", ".mp3", ".mp4", ".mov", ".avi", ".wav",
               ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".xlsx", ".pptx", ".doc", ".xls", ".ppt"):
        return f"ERROR: {p.name} is not a text file, so it cannot be read as text."
    if ext == ".docx":
        import re
        import zipfile
        with zipfile.ZipFile(p) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
        xml = re.sub(r"</w:p>", chr(10), xml)
        return re.sub(r"<[^>]+>", "", xml)[:limit]
    return p.read_text(encoding="utf-8", errors="replace")[:limit]


def _video_wait() -> int:
    """Seconds a video tool may wait for the background analysis: up to 8 minutes, but inside this task's own time limit."""
    if not DEADLINE:
        return 480
    left = DEADLINE - (time.time() - STARTED)
    return int(max(30, min(480, left - 50)))


def run_tool(name: str, args: dict) -> str:
    try:
        if name == "control_media":
            return control_media(str(args.get("action", "")))
        if name == "play_youtube":
            return play_youtube(str(args.get("what", "")))
        if name == "watch_video":
            import video_watch
            return video_watch.watch(str(args.get("url", "")), MODEL, OLLAMA_URL, log, wait=_video_wait())
        if name == "generate_image":
            import comfy_cloud
            path, msg = comfy_cloud.generate(str(args.get("prompt", "")), str(args.get("size", "")), str(args.get("quality", "")), log=log)
            if not path:
                return "No picture was made: " + msg
            posix = str(path).replace("\\", "/")
            return ("Picture saved: %s\nShow it to the user by putting this exact line in your answer and add nothing else about it "
                    "(no cost, no model name): ![picture](%s)" % (path, posix))
        if name == "ask_video":
            import video_ask
            return video_ask.ask(str(args.get("url", "")), str(args.get("question", "")), MODEL, OLLAMA_URL, log, wait=_video_wait())
        if name == "read_link":
            import link_read
            u = str(args.get("url", ""))
            if link_read.is_video_link(u):   # a video on a social/video site: watch it, do not scrape the page
                import video_watch
                return video_watch.watch(u, MODEL, OLLAMA_URL, log, wait=_video_wait())
            return link_read.read(u)
        if name == "open_website":
            u = str(args.get("url", ""))
            if re.search(r"youtube\.com/watch|youtu\.be/", u, re.I):  # a YouTube video link: never open one that cannot play
                return play_youtube(u)
            return open_in_aura(u)
        if name == "run_powershell":
            return _run_powershell(str(args.get("command", "")))
        if name == "read_file":
            return _read_file(args["path"])
        if name == "write_file":
            return _write_file(args["path"], str(args.get("content", "")))
        if name == "delete_to_recycle_bin":
            return _delete_to_recycle_bin(args["path"])
        return f"unknown tool {name}"
    except subprocess.TimeoutExpired:
        return "ERROR: command took too long (30 min)."
    except Exception as e:  # report to Qwen so it can fix it
        return f"ERROR: {e}"


def load_images(paths: list[str]) -> list[str]:
    """Read picture files and return them base64-encoded (Qwen has vision)."""
    return [base64.b64encode(Path(p).read_bytes()).decode("ascii") for p in paths]


def _parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except ValueError:
        return {}


def emit(ev: dict) -> None:
    """A live progress line for the Atlas app (only when it asked for them)."""
    if PROGRESS:
        try:
            print(PROGRESS_PREFIX + json.dumps(ev), flush=True)
        except Exception:
            pass


def _chat(messages, tools, think: bool, vary: bool) -> dict:
    """One request to Ollama, streamed: a silent or unreachable helper is noticed within seconds/minutes
    instead of after 15 minutes. Exits with a clear message if Ollama cannot be reached."""
    options = {"temperature": 0.9 if vary else (0.6 if think else 0.1), "num_ctx": NUM_CTX}
    if vary:  # "another answer": a different seed so the reply really changes
        options["seed"] = random.randint(1, 2**31 - 1)
    body = {"model": MODEL, "stream": True, "think": think, "messages": messages, "options": options}
    if tools:
        body["tools"] = tools
    try:
        r = requests.post(f"{OLLAMA_URL}/api/chat", json=body, stream=True, timeout=(CONNECT_TIMEOUT, READ_GAP))
    except requests.exceptions.ConnectTimeout:
        sys.exit(f"Could not connect to Ollama at {OLLAMA_URL} (no answer). Is that PC on?")
    except requests.exceptions.ConnectionError:
        sys.exit(f"Could not connect to Ollama at {OLLAMA_URL}. Is it running?")
    except requests.exceptions.Timeout:
        sys.exit("Ollama took too long to answer. Try again later.")
    if r.status_code >= 400:
        text = r.text[:500]
        r.close()
        if think and r.status_code == 400 and "think" in text.lower():  # this model cannot think: just ask again
            log("THINK: not supported by the model, asking without it")
            return _chat(messages, tools, False, vary)
        log(f"OLLAMA ERROR {r.status_code}: {text}")
        sys.exit(f"Ollama returned an error ({r.status_code}): {text[:300]}")
    content, calls, thinking = [], [], []
    try:
        with r:
            for raw in r.iter_lines():
                if not raw:
                    continue
                try:
                    chunk = json.loads(raw)
                except ValueError:
                    continue
                if chunk.get("error"):
                    log(f"OLLAMA ERROR (stream): {str(chunk['error'])[:500]}")
                    sys.exit(f"Ollama returned an error: {str(chunk['error'])[:300]}")
                m = chunk.get("message") or {}
                if m.get("thinking"):  # never cut short: thinking is what makes the answers good
                    thinking.append(m["thinking"])
                if m.get("content"):
                    content.append(m["content"])
                if m.get("tool_calls"):
                    calls.extend(m["tool_calls"])
                if chunk.get("done"):
                    break
    except requests.exceptions.RequestException as exc:
        log("OLLAMA STREAM ERROR: " + repr(exc))
        if "timed out" in str(exc).lower() or isinstance(exc, requests.exceptions.Timeout):
            sys.exit(f"Ollama took too long to answer (no reply for {int(READ_GAP // 60)} min). Try again later.")
        sys.exit(f"Lost the connection to Ollama at {OLLAMA_URL}. Is that PC still on?")
    msg = {"role": "assistant", "content": "".join(content)}
    if thinking:  # kept in the history, like the old (non-streamed) reply did
        msg["thinking"] = "".join(thinking)
    if calls:
        msg["tool_calls"] = calls
    return msg


def _time_left() -> float:
    return float("inf") if not DEADLINE else DEADLINE - (time.time() - STARTED)


def main(task: str, images: list[str] | None = None) -> None:
    global BROWSE_OK
    user_msg = {"role": "user", "content": task}
    if images:
        user_msg["images"] = load_images(images)
    messages = [{"role": "system", "content": SYSTEM}, user_msg]
    log(f"TASK: {task}" + (f" [images: {', '.join(images)}]" if images else ""))

    think = bool(needs_thinking(task, bool(images)))  # slow "thinking" only when the request needs real reasoning
    if os.environ.get("AGENT_THINK") == "off":  # the app's Thinking switch (and Quick mode) turns it off
        think = False
    request = task.split("New request from the user:")[-1].lower()
    vary = bool(ANOTHER_ANSWER.search(request))
    BROWSE_OK = bool(wants_browser(task))
    tools = TOOLS if BROWSE_OK else [t for t in TOOLS if t["function"]["name"] != "open_website"]
    log("THINK: " + ("on" if think else "off"))
    log("BROWSER: " + ("allowed" if BROWSE_OK else "not asked for"))

    last_sig, repeats, why_stop = "", 0, "steps"
    for step in range(1, MAX_STEPS + 1):
        if _time_left() < 45:  # leave time for a short summary before the app's watchdog stops us
            why_stop = "time"
            log("DEADLINE: stopping after step " + str(step - 1))
            break
        if step > 1:
            emit({"t": "phase", "kind": "think"})
        msg = _chat(messages, tools, think, vary)  # the same thinking decision on every step, as before
        messages.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:
            final = (msg.get("content") or "").strip()
            print(final or "Done.")
            log(f"FINAL: {final}")
            return
        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name", "")
            args = _parse_args(fn.get("arguments"))
            if not IN_AURA:  # inside the Atlas app the raw steps stay in logs/agent.log only
                print(f"[{step}] {name}: {json.dumps(args)[:200]}", flush=True)
            ev = agent_step(name, args)
            emit({"t": "step", "kind": ev.get("kind", "work"), "text": ev.get("text", ""), "detail": agent_detail(name, args)})
            out = run_tool(name, args)
            emit({"t": "result", "out": clip_detail(out, DETAIL_OUT)})
            log(f"{name} {json.dumps(args)[:300]} -> {out[:300]}")
            sig = name + json.dumps(args, sort_keys=True)
            repeats = repeats + 1 if sig == last_sig else 0
            last_sig = sig
            if repeats >= 2:  # the same call again and again: push it to change its approach
                out += " (You already tried this exact step. Try something different or explain what blocks you.)"
            messages.append({"role": "tool", "tool_name": name, "content": out})
    # Out of steps (or time): instead of failing, ask for a plain summary of what got done and what is left.
    log("OUT OF " + why_stop.upper())
    final = ""
    if _time_left() > 15:
        emit({"t": "phase", "kind": "write"})
        messages.append({"role": "user", "content": "You are out of " + why_stop + ". Reply in 2-3 plain sentences: "
                         "what you finished and what is left."})
        try:
            final = (_chat(messages, [], False, False).get("content") or "").strip()
        except SystemExit:
            final = ""
    print(final or ("I ran out of time before finishing. Try asking for a smaller piece of the task."
                    if why_stop == "time" else
                    "I ran out of steps before finishing. Try asking for a smaller piece of the task."))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('Usage: python agent.py "what you want done" [--image PATH ...]')
    words, imgs, i = [], [], 1
    while i < len(sys.argv):
        if sys.argv[i] == "--image" and i + 1 < len(sys.argv):
            imgs.append(sys.argv[i + 1])
            i += 2
        elif sys.argv[i] == "--see-aura":  # a picture of the Atlas window (and its preview pane), taken right now
            try:
                info = json.loads((ROOT / "logs" / "aura_ctl.json").read_text(encoding="utf-8"))
                r = requests.get("http://127.0.0.1:%d/shot" % int(info["port"]), params={"token": info["token"]}, timeout=20)
                if r.status_code != 200:
                    sys.exit("I could not take a picture of the Atlas window right now.")
                imgs.append(str(ROOT / "logs" / "aura_shot.png"))
            except SystemExit:
                raise
            except Exception as exc:
                sys.exit("I could not reach the Atlas window: " + repr(exc))
            i += 1
        else:
            words.append(sys.argv[i])
            i += 1
    missing = [p for p in imgs if not Path(p).is_file()]
    if missing:
        sys.exit(f"Picture not found: {missing[0]}")
    main(" ".join(words), imgs)
