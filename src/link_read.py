"""Read a web page from a link: title, description, headings and the main text (menus, ads and scripts removed).
Read-only. Refuses non-web and local-network addresses."""
import ipaddress
import socket
import time
from html.parser import HTMLParser
from urllib.parse import urlparse

import requests

MAX_CHARS = 6000
VIDEO_HOSTS = ("tiktok.com", "youtube.com", "youtu.be", "instagram.com", "facebook.com", "fb.watch", "x.com", "twitter.com",
               "vimeo.com", "twitch.tv", "reddit.com", "dailymotion.com", "streamable.com")
_SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "aside", "header", "form", "iframe", "template", "button"}
_BLOCK = {"p", "div", "br", "li", "tr", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre"}


def is_video_link(url):
    host = (urlparse(url or "").hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in VIDEO_HOSTS)


def safe_url(url):
    """(ok, reason). Only http/https, and never an address on this PC or the local network."""
    try:
        u = urlparse((url or "").strip())
    except ValueError:
        return False, "That does not look like a web link."
    if u.scheme not in ("http", "https") or not u.hostname:
        return False, "That does not look like a web link."
    host = u.hostname
    try:
        ips = {ai[4][0] for ai in socket.getaddrinfo(host, None)}
    except OSError:
        return False, "I could not find that website."
    for ip in ips:
        try:
            a = ipaddress.ip_address(ip.split("%")[0])
        except ValueError:
            continue
        if a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast:
            return False, "I only read public web pages, not addresses on this PC or network."
    return True, ""


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.parts = []
        self.title = ""
        self.meta = {}
        self.heads = []
        self._in_title = False
        self._head = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            k = (a.get("property") or a.get("name") or "").lower()
            if k in ("description", "og:description", "og:title", "og:site_name") and a.get("content"):
                self.meta.setdefault(k, a["content"].strip())
        if tag in _SKIP:
            self.skip += 1
        if tag in _BLOCK:
            self.parts.append("\n")
        if tag in ("h1", "h2", "h3") and not self.skip:
            self._head = []

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in _SKIP and self.skip:
            self.skip -= 1
        if tag in ("h1", "h2", "h3") and self._head is not None:
            h = " ".join("".join(self._head).split())
            if h:
                self.heads.append(h)
            self._head = None
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self.skip:
            return
        self.parts.append(data)
        if self._head is not None:
            self._head.append(data)


def extract(html):
    """(title, description, headings, text) from page source. Pure function (tested)."""
    p = _Text()
    try:
        p.feed(html or "")
        p.close()
    except Exception:
        pass
    text = "".join(p.parts)
    lines = [" ".join(ln.split()) for ln in text.splitlines()]
    out, last = [], ""
    for ln in lines:
        if len(ln) < 2 or ln == last:
            continue
        out.append(ln)
        last = ln
    title = " ".join((p.meta.get("og:title") or p.title).split())
    desc = p.meta.get("og:description") or p.meta.get("description") or ""
    return title, desc, p.heads[:12], "\n".join(out)


def read(url, max_chars=MAX_CHARS):
    """Plain-text report about the page at url, or a plain error sentence."""
    ok, why = safe_url(url)
    if not ok:
        return why
    try:
        r = requests.get(url.strip(), timeout=(8, 20), allow_redirects=True, stream=True,
                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
                                  "Accept-Language": "en;q=0.8"})
        final_ok, final_why = safe_url(r.url)
        if not final_ok:
            return final_why
        if r.status_code in (429, 503):   # the site asks us to slow down: wait what it asks (max 65 s), try once more
            try:
                wait = min(65, max(5, int(r.headers.get("Retry-After", "30"))))
            except ValueError:
                wait = 30
            time.sleep(wait)
            r = requests.get(url.strip(), timeout=(8, 20), stream=True, headers=r.request.headers)
            if r.status_code in (429, 503):
                return ("That website is limiting how often it can be read right now (HTTP %d). Please ask again in a few "
                        "minutes, or let me search the web for the same information instead." % r.status_code)
        ctype = r.headers.get("content-type", "").lower()
        if r.status_code >= 400:
            return "The website answered with an error (%d), so I could not read it." % r.status_code
        if ctype and "html" not in ctype and "text" not in ctype:
            return "That link is not a web page (%s)." % ctype.split(";")[0]
        raw = r.raw.read(2_000_000, decode_content=True)
        html = raw.decode(r.encoding or "utf-8", errors="replace")
    except requests.RequestException as exc:
        return "I could not open that link (%s)." % type(exc).__name__
    title, desc, heads, text = extract(html)
    lines = ["Page: " + (title or url)]
    if desc:
        lines.append("Description: " + desc[:400])
    if heads:
        lines.append("Headings: " + " | ".join(heads))
    if len(text) < 200:
        lines.append("(Very little readable text: the page probably loads its content with scripts or blocks readers. "
                     "Open it in the Atlas browser and look at the screen instead.)")
    if text:
        lines.append("Text:\n" + text[:max_chars])
    return "\n".join(lines)
