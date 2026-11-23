import re


def clean_prompt(text: str) -> str:
    if not text:
        return ""

    # Remove forbidden characters
    text = text.replace('{', '').replace('}', '').replace('|', '')

    # Collapse whitespace runs and strip
    text = re.sub(r'\s+', ' ', text).strip()

    if len(text) <= 300:
        return text

    # A word ending exactly at 300 is kept if the next char is a space
    if text[300] == ' ':
        return text[:300]

    truncated = text[:300]
    last_space = truncated.rfind(' ')
    if last_space == -1:
        return truncated
    return truncated[:last_space].rstrip()
