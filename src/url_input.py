import re
import urllib.parse


def _is_ipv4(host: str) -> bool:
    parts = host.split(".")
    if len(parts) != 4:
        return False
    for p in parts:
        if not p.isdigit():
            return False
        if len(p) > 1 and p[0] == "0":
            return False
        if int(p) > 255:
            return False
    return True


def _is_domain(host: str) -> bool:
    if not host:
        return False
    labels = host.split(".")
    if len(labels) < 2:
        return False
    for lab in labels[:-1]:
        if not lab:
            return False
        if not re.fullmatch(r"[A-Za-z0-9-]+", lab):
            return False
    last = labels[-1]
    if len(last) < 2:
        return False
    if not re.fullmatch(r"[A-Za-z]+", last):
        return False
    return True


def _is_site(text: str) -> bool:
    if not text or any(c.isspace() for c in text):
        return False
    # split off path/query/fragment
    m = re.match(r"^([^/?#]+)([/?#].*)?$", text)
    if not m:
        return False
    host_part = m.group(1)
    # optional :PORT right after host
    if ":" in host_part:
        idx = host_part.rfind(":")
        host = host_part[:idx]
        port = host_part[idx + 1:]
        if not port.isdigit():
            return False
    else:
        host = host_part
    if host == "localhost":
        return True
    if _is_ipv4(host):
        return True
    if _is_domain(host):
        return True
    return False


def _is_other_scheme(text: str) -> bool:
    m = re.match(r"^[A-Za-z0-9+.-]+:", text)
    if not m:
        return False
    scheme = m.group(0)[:-1]
    if scheme.lower() in ("http", "https"):
        return False
    return True


def normalize_input(text) -> str:
    if text is None or not isinstance(text, str):
        return ""
    text = text.strip()
    if not text:
        return ""

    if (text.lower().startswith("http://") or text.lower().startswith("https://")) and not any(c.isspace() for c in text):
        return text

    if _is_site(text):
        m = re.match(r"^([^/?#]+)([/?#].*)?$", text)
        host_part = m.group(1)
        rest = m.group(2) or ""
        if ":" in host_part:
            idx = host_part.rfind(":")
            host = host_part[:idx]
            port = host_part[idx + 1:]
        else:
            host = host_part
            port = None
        if host == "localhost" or _is_ipv4(host):
            scheme = "http"
        else:
            scheme = "https"
        if port is not None:
            return f"{scheme}://{host}:{port}{rest}"
        return f"{scheme}://{host}{rest}"

    if _is_other_scheme(text):
        return "https://www.bing.com/search?q=" + urllib.parse.quote_plus(text)

    return "https://www.bing.com/search?q=" + urllib.parse.quote_plus(text)
