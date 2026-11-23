import re

_MARKER = "New request from the user:"

_BROWSING_WORDS = [
    "browse", "browser", "browsing", "website", "websites", "web site", "webpage",
    "web page", "web", "internet", "online", "url", "link", "links", "http",
    "https", "www",
]

_SITE_NAMES = [
    "google", "bing", "youtube", "youtu.be", "wikipedia", "reddit", "twitter",
    "instagram", "tiktok", "netflix", "spotify", "amazon", "facebook", "github",
    "stackoverflow", "twitch",
]

_PHRASES = [
    "look up", "look it up", "look this up", "watch", "stream",
]

_TLDS = ["com", "org", "net", "io", "gov", "edu", "co", "tv", "app", "dev", "ai", "uk", "de"]


def _word_boundary_match(text: str, word: str) -> bool:
    pattern = r"(?<!\w)" + re.escape(word) + r"(?!\w)"
    return re.search(pattern, text) is not None


def _domain_like_match(text: str) -> bool:
    # letters/digits/hyphens, a dot, then one of the TLDs
    tld_alt = "|".join(re.escape(t) for t in _TLDS)
    pattern = r"(?<!\w)[A-Za-z0-9-]+\.(?:%s)(?!\w)" % tld_alt
    return re.search(pattern, text) is not None


def wants_browser(text) -> bool:
    try:
        if not isinstance(text, str):
            return False
        if not text.strip():
            return False

        # Step 0: keep only what comes after the LAST occurrence of the marker
        idx = text.rfind(_MARKER)
        if idx != -1:
            text = text[idx + len(_MARKER):]

        text = text.lower().strip()
        if not text:
            return False

        # Browsing words
        for w in _BROWSING_WORDS:
            if _word_boundary_match(text, w):
                return True

        # Site names
        for s in _SITE_NAMES:
            if _word_boundary_match(text, s):
                return True

        # Phrases
        for p in _PHRASES:
            if _word_boundary_match(text, p):
                return True

        # Domain-like token
        if _domain_like_match(text):
            return True

        return False
    except Exception:
        return False
