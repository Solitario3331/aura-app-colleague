import re

def needs_thinking(text, has_images=False):
    try:
        if not isinstance(text, str):
            return False
        if not text.strip():
            return False

        # Step 0: clean the text
        # 1. If the text contains the marker "New request from the user:" keep only what comes after its LAST occurrence
        marker = "New request from the user:"
        if marker in text:
            last_idx = text.rfind(marker)
            text = text[last_idx + len(marker):]

        # 2. If the text contains "Look ONLY at the attached picture" remove everything from that phrase up to and including the first "itself shows."
        pic_marker = "Look ONLY at the attached picture"
        if pic_marker in text:
            start_idx = text.find(pic_marker)
            end_phrase = "itself shows."
            end_idx = text.find(end_phrase, start_idx)
            if end_idx != -1:
                text = text[:start_idx] + text[end_idx + len(end_phrase):]
            else:
                text = text[:start_idx] + text[start_idx + len(pic_marker):]

        text = text.strip()
        if not text:
            return False

        lower = text.lower()

        # Rule 1: REASONING WORDS
        reasoning_words = [
            "why", "how do", "how does", "how can", "how would", "how should",
            "explain", "compare", "difference", "pros and cons", "calculate",
            "solve", "prove", "step by step", "debug", "bug", "riddle", "puzzle",
            "equation", "algorithm", "design", "architecture", "tradeoff", "trade-off",
            "estimate", "recommend", "decide", "strategy", "better", "should i",
            "think", "what's wrong", "whats wrong", "what is wrong"
        ]
        stems = ["analy", "optimi", "plan", "explain"]

        for word in reasoning_words:
            pattern = r'\b' + re.escape(word) + r'\b'
            if re.search(pattern, lower):
                return True

        for stem in stems:
            pattern = r'\b' + re.escape(stem)
            if re.search(pattern, lower):
                return True

        # Rule 2: MATH
        has_digit = any(c.isdigit() for c in lower)
        if has_digit:
            math_words = [
                "times", "plus", "minus", "divided by", "multiplied by",
                "percent of", "sqrt", "square root", "squared", "factorial",
                "derivative", "integral"
            ]
            for word in math_words:
                pattern = r'\b' + re.escape(word) + r'\b'
                if re.search(pattern, lower):
                    return True

            # digit followed by * / ^ = × ÷
            if re.search(r'\d\s*[*/^=×÷]', lower):
                return True

            # digit + plus + digit
            if re.search(r'\d\s*\+\s*\d', lower):
                return True

        # Rule 3: LONG
        words = lower.split()
        if len(words) > 70:
            return True

        # Rule 4: MULTI-STEP
        then_count = len(re.findall(r'\bthen\b', lower))
        if then_count >= 2:
            return True

        # Rule 5: REAL QUESTION
        if "?" in lower and len(words) >= 8:
            # Check if it's an action request
            polite_words = {"please", "hey", "hi", "can you", "could you", "would you", "will you", "kindly"}
            action_words = {
                "open", "play", "launch", "start", "download", "install", "create",
                "make", "save", "list", "show", "find", "search", "go", "visit",
                "delete", "remove", "copy", "run", "take", "set", "turn", "close",
                "type", "write", "click", "press", "put", "move", "send", "check"
            }

            # Remove leading polite words and phrases (they can be several words, e.g. "can you", in any order, repeated)
            rest = lower.strip()
            polite = sorted(polite_words, key=len, reverse=True)
            changed = True
            while changed:
                changed = False
                for pw in polite:
                    if rest == pw or rest.startswith(pw + " "):
                        rest = rest[len(pw):].strip()
                        changed = True
                        break
            first_word = rest.split()[0] if rest.split() else ""

            if first_word not in action_words:
                return True

        # Rule 6: Everything else
        return False

    except Exception:
        return False
