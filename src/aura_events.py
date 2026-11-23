import json
from aura_mask import mask_names
from strip_sources import strip_sources


class Translator:
    def __init__(self):
        self._blocks = []
        self._open = False

    def feed(self, line):
        try:
            obj = json.loads(line)
        except Exception:
            return []
        if not isinstance(obj, dict):
            return []
        try:
            t = obj.get("type")
            if t == "stream_event":
                return self._handle_stream_event(obj)
            if t == "assistant":
                return self._handle_assistant(obj)
            if t == "result":
                return self._handle_result(obj)
            if t == "closed":
                return [{"t": "closed"}]
        except Exception:
            return []
        return []

    def saved_text(self):
        parts = []
        for b in self._blocks:
            if b.strip() == "":
                continue
            parts.append(strip_sources(mask_names(b)))
        return "\n\n".join(parts)

    def _open_block(self):
        self._blocks.append("")
        self._open = True

    def _handle_stream_event(self, obj):
        event = obj.get("event")
        if not isinstance(event, dict):
            return []
        et = event.get("type")
        if et == "content_block_start":
            cb = event.get("content_block")
            if not isinstance(cb, dict):
                return []
            if cb.get("type") == "text":
                self._open_block()
                return [{"t": "new_bubble"}]
            return []
        if et == "content_block_delta":
            delta = event.get("delta")
            if not isinstance(delta, dict):
                return []
            if delta.get("type") == "text_delta":
                text = delta.get("text")
                if not isinstance(text, str):
                    return []
                out = []
                if not self._open:
                    self._open_block()
                    out.append({"t": "new_bubble"})
                self._blocks[-1] += text
                out.append({"t": "text", "text": strip_sources(mask_names(self._blocks[-1]))})
                return out
            return []
        return []

    def _handle_assistant(self, obj):
        msg = obj.get("message")
        if not isinstance(msg, dict):
            return []
        content = msg.get("content")
        if not isinstance(content, list):
            return []
        has_tool = any(isinstance(b, dict) and b.get("type") == "tool_use" for b in content)
        if not has_tool:
            return []
        self._open = False
        return [{"t": "tool", "text": "Aura is working on it..."}]

    def _handle_result(self, obj):
        is_error = bool(obj.get("is_error", False))
        result = obj.get("result")
        if is_error and isinstance(result, str):
            text = mask_names(result)
        else:
            text = ""
        self._blocks = []
        self._open = False
        return [{"t": "done", "error": is_error, "text": text}]
