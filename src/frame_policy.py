def can_embed(headers):
    if not headers:
        return True
    if not isinstance(headers, dict):
        return True

    # Normalize headers to lowercase keys
    normalized = {}
    for k, v in headers.items():
        if isinstance(k, str):
            normalized[k.lower()] = v

    # Check X-Frame-Options
    xfo = normalized.get("x-frame-options")
    if isinstance(xfo, str):
        val = xfo.strip().lower()
        if val == "deny" or val == "sameorigin" or val.startswith("allow-from"):
            return False

    # Check Content-Security-Policy
    csp = normalized.get("content-security-policy")
    if isinstance(csp, str):
        # Split into policies by comma
        policies = csp.split(",")
        for policy in policies:
            # Split into directives by semicolon
            directives = policy.split(";")
            for directive in directives:
                directive = directive.strip()
                if not directive:
                    continue
                parts = directive.split(None, 1)
                if not parts:
                    continue
                first_word = parts[0].lower()
                if first_word == "frame-ancestors":
                    # Check if any source token is exactly "*"
                    if len(parts) < 2:
                        # No source tokens, so no "*"
                        return False
                    tokens = parts[1].split()
                    if "*" not in tokens:
                        return False

    return True
