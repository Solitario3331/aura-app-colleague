"""Image generation through ComfyUI Cloud (GPT Image 2.6 'Sunburst' partner node).
The Comfy API key is read from comfy_cloud_key.txt in the project folder (never printed, never logged, never published).
Flow: submit a small workflow -> poll the job -> read the finished file name -> download it (the cloud answers with a redirect to a
signed address; the key is NOT sent there) -> save it under ~/Atlas/images."""
import json
import math
import os
import re
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://cloud.comfy.org"
MODEL = "gpt-image-2.6-sunburst"
SIZES = ("auto", "1024x1024", "1024x1536", "1536x1024", "2048x2048", "2048x1152", "1152x2048", "3840x2160", "2160x3840")
QUALITIES = ("low", "medium", "high", "xhigh", "max")
TERMINAL = ("success", "error", "non_retryable_error", "lost", "cancelled")
# approximate US$ per picture at the usual sizes (from the node's own price list, gpt-image-2.6)
PRICE_1024 = {"low": 0.035, "medium": 0.062, "high": 0.075, "xhigh": 0.134, "max": 0.301}


# typical seconds a picture takes to render once the job runs (low is measured, the others are estimates)
EXPECTED = {"low": 22, "medium": 35, "high": 50, "xhigh": 80, "max": 130}
PROGRESS_FILE = ROOT / "logs" / "image_progress.json"


def estimate_pct(stage, elapsed, quality="medium"):
    """A believable percentage for the preview card. The cloud reports no real progress for this model, so this follows the stages
    and the typical time: waiting in line 0-9 % (slowly), rendering 6-92 % (fast at first, slowing down), saving 95 %, done 100 %.
    It never reaches 100 before the picture really is ready."""
    elapsed = max(0.0, float(elapsed))
    if stage == "done":
        return 100.0
    if stage == "downloading":
        return 95.0
    if stage == "queued":
        return min(6.0, elapsed * 0.6) + min(3.0, max(0.0, elapsed - 10.0) * 0.05)   # keeps creeping while waiting in line
    tau = EXPECTED.get(quality, 35) / 2.2
    return min(92.0, 6.0 + 86.0 * (1.0 - math.exp(-elapsed / tau)))


def write_progress(stage, pct, size="", prompt="", quality="", root=ROOT):
    """The page reads this small file (through the app) to draw the preview card. Atomic; failures are ignored."""
    try:
        f = Path(root) / "logs" / "image_progress.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps({"stage": stage, "pct": round(float(pct), 1), "size": size, "prompt": (prompt or "")[:160],
                                   "quality": quality, "t": time.time()}), encoding="utf-8")
        os.replace(tmp, f)
    except OSError:
        pass


def read_progress(root=ROOT, max_age=25):
    """The current preview state, or {} when nothing is being made (or the file is old)."""
    try:
        d = json.loads((Path(root) / "logs" / "image_progress.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if time.time() - float(d.get("t", 0)) > max_age:
        return {}
    return d


def read_key(root=ROOT):
    try:
        k = (Path(root) / "comfy_cloud_key.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return k if re.fullmatch(r"[A-Za-z0-9_\-]{20,200}", k) else ""


def pick_size(text):
    """A cloud size from words: 'wide'/'landscape', 'tall'/'portrait'/'phone', '4k', 'square', or an explicit WxH."""
    t = (text or "").lower().strip()
    for s in SIZES:
        if s != "auto" and s in t:
            return s
    if "4k" in t or "ultra" in t:
        return "3840x2160" if ("wide" in t or "landscape" in t or "16:9" in t or "desktop" in t) else "2160x3840" if ("tall" in t or "portrait" in t or "phone" in t) else "2048x2048"
    if "wide" in t or "landscape" in t or "16:9" in t or "desktop" in t or "banner" in t:
        return "1536x1024"
    if "tall" in t or "portrait" in t or "phone" in t or "9:16" in t or "poster" in t:
        return "1024x1536"
    if "square" in t or "1:1" in t or "icon" in t or "avatar" in t:
        return "1024x1024"
    return "1024x1024" if not t or t == "auto" else "1024x1024"


def pick_quality(text):
    t = (text or "").lower().strip()
    return t if t in QUALITIES else "medium"


def build_workflow(prompt, size="1024x1024", quality="medium", seed=42, prefix="atlas"):
    """The API-format workflow: one GPT Image 2.6 Sunburst node saved by one SaveImage node."""
    size = size if size in SIZES else "1024x1024"
    quality = quality if quality in QUALITIES else "medium"
    return {
        "1": {"class_type": "OpenAIGPTImageNodeV3", "inputs": {
            "model": MODEL, "model.prompt": str(prompt), "model.size": size, "model.custom_width": 1024, "model.custom_height": 1024,
            "model.background": "auto", "model.quality": quality, "model.complexity": "high", "model.seed": int(seed)}},
        "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": prefix}},
    }


def parse_outputs(history, prompt_id):
    """[(filename, subfolder, type)] for every picture in a /api/history_v2 answer."""
    out = []
    entry = (history or {}).get(prompt_id) or {}
    for node in (entry.get("outputs") or {}).values():
        for im in node.get("images") or []:
            if im.get("filename"):
                out.append((im["filename"], im.get("subfolder", ""), im.get("type", "output")))
    return out


def asset_ids(history, prompt_id):
    """Ids of the pictures THIS job made (so only they are removed from the account afterwards)."""
    entry = (history or {}).get(prompt_id) or {}
    ids = []
    for node in (entry.get("outputs") or {}).values():
        for im in node.get("images") or []:
            if im.get("id"):
                ids.append(str(im["id"]))
    return ids


def cleanup(headers, prompt_id, ids, log=lambda m: None):
    """The picture is saved on this PC, so remove it (and the job entry) from the ComfyUI account: nothing from Atlas stays there.
    Only the ids this job produced are deleted. Failures are ignored (the picture is already safe)."""
    for i in ids:
        try:
            requests.delete("%s/api/assets/%s" % (BASE, i), headers=headers, timeout=30)
        except requests.RequestException as exc:
            log("cleanup asset failed: " + type(exc).__name__)
    try:
        requests.delete("%s/api/jobs/%s" % (BASE, prompt_id), headers=headers, timeout=30)
    except requests.RequestException as exc:
        log("cleanup job failed: " + type(exc).__name__)


def safe_name(prompt):
    words = re.findall(r"[A-Za-z0-9]+", (prompt or "image").lower())[:6]
    return "-".join(words) or "image"


def estimate_cost(size, quality):
    base = PRICE_1024.get(quality, 0.062)
    return round(base * (2.0 if size in ("2048x2048",) else 1.8 if size in ("3840x2160", "2160x3840") else 0.8 if size in ("1024x1536", "1536x1024") else 1.0), 3)


def is_moderation(text):
    """True when an error text says the picture service refused the request on content grounds."""
    t = (text or "").lower()
    return any(w in t for w in ("moderation", "safety system", "content policy", "content_policy", "safety filter", "safety_violation",
                                "violates", "blocked by", "not allowed by"))


def friendly_error(status, body=""):
    b = (body or "").lower()
    if status in (401, 403):
        return "The Comfy key was refused. Check comfy_cloud_key.txt (a new key may be needed)."
    if status == 402 or "credit" in b or "insufficient" in b or "balance" in b:
        return "Your Comfy credits are used up. Top up at platform.comfy.org (Settings, Billing)."
    if status == 429:
        return "ComfyUI Cloud says too many requests right now. Try again in a minute."
    if is_moderation(b):
        return "The picture service declined this request (its own content rules)."
    return "ComfyUI Cloud could not make the picture (error %s)." % status


def generate(prompt, size="1024x1024", quality="medium", out_dir=None, log=lambda m: None, timeout=300, root=ROOT,
             keep_in_comfy=False):
    """Make one picture. Returns (path or None, message). The message is plain text for the user."""
    key = read_key(root)
    if not key:
        return None, "Image making is not set up: comfy_cloud_key.txt is missing or does not look like a Comfy key."
    prompt = (prompt or "").strip()
    if not prompt:
        return None, "I need a description of the picture."
    size, quality = pick_size(size), pick_quality(quality)
    H = {"X-API-Key": key}
    write_progress("queued", 0, size, prompt, quality)
    try:
        r = requests.post(BASE + "/api/prompt", headers=H, timeout=60,
                          json={"prompt": build_workflow(prompt, size, quality, seed=int(time.time()) % 2147483647),
                                "extra_data": {"api_key_comfy_org": key}})
        if r.status_code != 200:
            write_progress("error", 0, size, prompt, quality)
            return None, friendly_error(r.status_code, r.text)
        j = r.json()
        pid = j.get("prompt_id")
        if not pid or j.get("node_errors"):
            return None, "ComfyUI Cloud did not accept the job (%s)." % (str(j.get("node_errors"))[:160])
        t0, st, t_exec = time.time(), "", None
        while time.time() - t0 < timeout:
            s = requests.get("%s/api/job/%s/status" % (BASE, pid), headers=H, timeout=30)
            st = s.json().get("status", "") if s.status_code == 200 else ""
            if st in TERMINAL:
                break
            if st == "executing" and t_exec is None:
                t_exec = time.time()
            stage = "creating" if t_exec else "queued"
            write_progress(stage, estimate_pct(stage, (time.time() - (t_exec or t0)), quality), size, prompt, quality)
            time.sleep(1.2)
        if st != "success":
            write_progress("error", 0, size, prompt, quality)
            try:   # a refusal by the picture service's own filter: say so plainly (the reason is in the job record)
                why = requests.get("%s/api/history_v2/%s" % (BASE, pid), headers=H, timeout=30).text
                if is_moderation(why):
                    return None, friendly_error(400, why)
            except requests.RequestException:
                pass
            return None, ("The picture took too long (over %d seconds); it may still finish in your Comfy account." % timeout
                          if st not in TERMINAL else "The picture could not be made (%s)." % st)
        write_progress("downloading", estimate_pct("downloading", 0), size, prompt, quality)
        h = requests.get("%s/api/history_v2/%s" % (BASE, pid), headers=H, timeout=30)
        hist = h.json() if h.status_code == 200 else {}
        files = parse_outputs(hist, pid)
        ids = asset_ids(hist, pid)
        if not files:
            return None, "The job finished but no picture came back."
        name, sub, typ = files[0]
        v = requests.get(BASE + "/api/view", headers=H, params={"filename": name, "subfolder": sub, "type": typ},
                         allow_redirects=False, timeout=60)
        if v.status_code in (301, 302, 303, 307, 308) and v.headers.get("location"):
            data = requests.get(v.headers["location"], timeout=120).content   # signed address: no key is sent
        elif v.status_code == 200:
            data = v.content
        else:
            return None, friendly_error(v.status_code, v.text)
        folder = Path(out_dir) if out_dir else Path.home() / "Atlas" / "images"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ("%s-%s.png" % (time.strftime("%Y%m%d-%H%M%S"), safe_name(prompt)))
        path.write_bytes(data)
        if keep_in_comfy is False:
            cleanup(H, pid, ids, log)
        write_progress("done", 100, size, prompt, quality)
        return path, "Picture made."
    except requests.RequestException as exc:
        log("comfy cloud request failed: " + type(exc).__name__)
        write_progress("error", 0, size, prompt, quality)
        return None, "I could not reach ComfyUI Cloud (%s). Check the internet connection." % type(exc).__name__
