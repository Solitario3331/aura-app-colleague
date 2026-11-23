import re

def strip_tag(text: str) -> tuple:
    if not text:
        return (None, text)
    m = re.match(r'^\[([A-Za-z]+)\] (.*)$', text, re.DOTALL)
    if m:
        return (m.group(1), m.group(2))
    return (None, text)


def build_context(messages, since_tag=None, max_messages=12, per_msg=500, max_chars=3000) -> str:
    if not messages:
        return ""

    kept = list(messages)

    if since_tag is not None:
        last_idx = -1
        for i, msg in enumerate(kept):
            if msg.get("role") == "assistant":
                tag, _ = strip_tag(msg.get("text", ""))
                if tag == since_tag:
                    last_idx = i
        if last_idx != -1:
            kept = kept[last_idx + 1:]
        # Check if any assistant message remains
        has_assistant = any(m.get("role") == "assistant" for m in kept)
        if not has_assistant:
            return ""

    # Take last max_messages
    if len(kept) > max_messages:
        kept = kept[-max_messages:]

    lines = []
    for msg in kept:
        role = msg.get("role")
        text = msg.get("text", "")
        if role == "user":
            body = text
        else:
            _, body = strip_tag(text)
        # Collapse newlines to single spaces
        body = body.replace("\n", " ").replace("\r", " ")
        body = body.strip()
        if not body:
            continue
        if len(body) > per_msg:
            body = body[:per_msg] + "..."
        label = "You" if role == "user" else "Aura"
        lines.append(f"{label}: {body}")

    if not lines:
        return ""

    # Drop oldest lines until fits max_chars
    while len(lines) > 1 and sum(len(l) for l in lines) + (len(lines) - 1) > max_chars:
        lines.pop(0)

    head = "Earlier in this chat (background only, do not repeat or redo it):" + "\n"
    return head + "\n".join(lines) + "\n\n"
