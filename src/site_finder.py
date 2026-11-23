from pathlib import Path


def newest_html(roots, since, max_depth=3, skip=()):
    skip_set = {s.lower() for s in skip}

    def is_skipped(name):
        if name.startswith("."):
            return True
        return name.lower() in skip_set

    def is_html_file(name):
        lower = name.lower()
        return lower.endswith(".html") or lower.endswith(".htm")

    candidates = []

    for root in roots:
        root_path = Path(root)
        if not root_path.is_dir():
            continue

        def walk(current, depth):
            try:
                entries = list(current.iterdir())
            except (OSError, PermissionError):
                return

            for entry in entries:
                try:
                    if entry.is_dir():
                        if is_skipped(entry.name):
                            continue
                        if depth < max_depth:
                            walk(entry, depth + 1)
                    elif entry.is_file():
                        if is_html_file(entry.name):
                            try:
                                mtime = entry.stat().st_mtime
                            except (OSError, PermissionError):
                                continue
                            if mtime >= since:
                                candidates.append((mtime, entry))
                except (OSError, PermissionError):
                    continue

        walk(root_path, 0)

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    best_mtime, best_path = candidates[0]

    folder = best_path.parent
    try:
        for entry in folder.iterdir():
            try:
                if entry.is_file() and entry.name.lower() == "index.html":
                    try:
                        mtime = entry.stat().st_mtime
                    except (OSError, PermissionError):
                        continue
                    if mtime >= since:
                        return str(entry.resolve())
            except (OSError, PermissionError):
                continue
    except (OSError, PermissionError):
        pass

    return str(best_path.resolve())
