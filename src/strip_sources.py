"""Remove a trailing "Sources" / "References" list from an answer (the user never wants those)."""
import re

_HEAD = re.compile(
    r"^\s{0,3}(?:#{1,6}\s*)?(?:\*\*|__)?\s*(?:sources?|references?|citations?|further reading|learn more)"
    r"\s*(?:\*\*|__)?\s*:?\s*(?:\*\*|__)?\s*(.*)$", re.I)
_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+\S")
_LINKISH = re.compile(r"https?://|\]\(")
MAX_TAIL = 25


def strip_sources(text):
    """The answer without a closing Sources section. Only a heading-like line followed by nothing but links or
    list items to the very end is removed; a sentence like "Sources of income are..." is never touched."""
    if not isinstance(text, str) or not text:
        return text or ""
    lines = text.split("\n")
    for i in range(len(lines) - 1, max(-1, len(lines) - 2 - MAX_TAIL), -1):
        m = _HEAD.match(lines[i])
        if not m:
            continue
        rest = m.group(1).strip()
        if rest and not _LINKISH.search(rest):
            continue  # a normal sentence that happens to start with the word
        tail = [ln for ln in lines[i + 1:] if ln.strip()]
        if all(_ITEM.match(ln) or _LINKISH.search(ln) for ln in tail):
            kept = "\n".join(lines[:i]).rstrip()
            return kept if kept else text  # never wipe the whole answer
    return text
