import re

_FILE_EXTS = (".md", ".py", ".bat", ".json", ".jsonl", ".txt", ".exe", ".log")

_PATTERN = re.compile(
    r"\b(?:claude[ ]code|claude|qwen|opus|sonnet|haiku|anthropic|ollama)"
    r"(?![a-z])"
    r"(?:\d[a-z0-9]*)?"
    r"(?:[-._:][a-z0-9]+)*",
    re.IGNORECASE,
)


def _replace(match):
    token = match.group(0)
    if token.lower().endswith(_FILE_EXTS):
        return token
    return "Aura"


def mask_names(text: str) -> str:
    if not isinstance(text, str) or text == "":
        return ""
    return _PATTERN.sub(_replace, text)
