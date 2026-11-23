"""Where the local helper (Ollama) runs: this PC, or another PC on the home network (ollama_url.txt).

normalize() makes a typed address usable: "192.168.1.6" -> "http://192.168.1.6:11434",
"http://pc:11434/" -> "http://pc:11434". Empty or junk -> "".
"""
import os
import re
from pathlib import Path

DEFAULT = "http://localhost:11434"
_HOST = re.compile(r"^[A-Za-z0-9.\-\[\]:]+$")


def normalize(text) -> str:
    if not isinstance(text, str):
        return ""
    t = text.strip().strip('"').strip("'").strip()
    if not t:
        return ""
    low = t.lower()
    if low.startswith("http://"):
        scheme, rest = "http://", t[7:]
    elif low.startswith("https://"):
        scheme, rest = "https://", t[8:]
    elif "://" in t:
        return ""
    else:
        scheme, rest = "http://", t
    rest = rest.split("/", 1)[0].strip()
    if not rest or not _HOST.match(rest):
        return ""
    if rest.startswith("["):              # IPv6 like [::1] or [::1]:11434
        if "]" not in rest:
            return ""
        has_port = not rest.endswith("]")
    else:
        has_port = rest.count(":") == 1
        if rest.count(":") > 1:
            return ""
    if has_port:
        port = rest.rsplit(":", 1)[1]
        if not port.isdigit() or not 0 < int(port) < 65536:
            return ""
    else:
        rest += ":11434"
    return scheme + rest


def read_address(root) -> str:
    """ollama_url.txt in `root` wins; then the OLLAMA_URL setting; then this PC."""
    try:
        f = Path(root) / "ollama_url.txt"
        if f.exists():
            url = normalize(f.read_text(encoding="utf-8-sig"))
            if url:
                return url
    except OSError:
        pass
    return normalize(os.environ.get("OLLAMA_URL", "")) or DEFAULT


def is_this_pc(url) -> bool:
    n = normalize(url)
    host = n.split("://", 1)[-1].rsplit(":", 1)[0].lower()
    return host in ("localhost", "127.0.0.1", "[::1]", "0.0.0.0", "")
