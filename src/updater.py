from __future__ import annotations

import re
from pathlib import Path


def version_tuple(v: str) -> tuple[int, int, int]:
    if not isinstance(v, str):
        raise ValueError("version must be a string")
    s = v.strip()
    if s[:1] in ("v", "V"):
        s = s[1:]
    s = s.strip()
    if not s:
        raise ValueError("empty version")
    parts = s.split(".")
    if len(parts) > 3:
        raise ValueError("too many parts")
    nums = []
    for p in parts:
        if not p:
            raise ValueError("empty part")
        if not p.isdigit():
            raise ValueError("non-numeric part")
        nums.append(int(p))
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def is_newer(remote: str, local: str) -> bool:
    return version_tuple(remote) > version_tuple(local)


def _validate_rel_path(p: str) -> None:
    if not isinstance(p, str) or p == "":
        raise ValueError("empty path")
    if "\\" in p:
        raise ValueError("backslash in path")
    if p.startswith("/") or p.startswith("\\"):
        raise ValueError("absolute path")
    # drive letter like C: or c:
    if re.match(r"^[A-Za-z]:", p):
        raise ValueError("drive in path")
    parts = p.split("/")
    if any(part == ".." for part in parts):
        raise ValueError("parent traversal")
    if parts and parts[0].lower() in ("chats", "logs"):
        raise ValueError("protected folder")


def apply_update(files: dict, target) -> list[str]:
    target_path = Path(target)
    keys = list(files.keys())
    for k in keys:
        _validate_rel_path(k)
    written = []
    for k in keys:
        content = files[k]
        dest = target_path / k
        dest.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            dest.write_bytes(content)
        else:
            dest.write_text(content, encoding="utf-8")
        written.append(k)
    return sorted(written)
