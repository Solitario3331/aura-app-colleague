"""Start helper programs without a console window popping up (Windows).

Atlas runs under pythonw.exe, which has no console. Any console program it starts (powershell, claude,
python, taskkill, pip...) would then get its OWN new console window that flashes open and shut.
hidden() gives such a child a hidden console instead; its own children inherit that hidden console,
so they stay invisible too. child_python() swaps pythonw.exe for python.exe: pythonw is a window-less
GUI program, so CREATE_NO_WINDOW does nothing for it and every program IT starts would flash again.
"""
import os
import subprocess
import sys

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def hidden() -> dict:
    """Keyword arguments for subprocess.run/Popen: hidden console, hidden window, no busy cursor."""
    if os.name != "nt":
        return {}
    opts = {"creationflags": NO_WINDOW}
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW | 0x80  # 0x80 = STARTF_FORCEOFFFEEDBACK
        si.wShowWindow = 0  # SW_HIDE
        opts["startupinfo"] = si
    except Exception:
        pass
    return opts


DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def start_detached(cmd, cwd):
    """Start a window program that must outlive this one (Atlas restarting itself): own process group,
    no console, not tied to our job object. Unlike hidden(), its window is NOT forced hidden."""
    io = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
          "close_fds": True, "cwd": cwd}
    if os.name != "nt":
        return subprocess.Popen(cmd, start_new_session=True, **io)
    base = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    try:
        return subprocess.Popen(cmd, creationflags=base | CREATE_BREAKAWAY_FROM_JOB, **io)
    except OSError:  # our job object does not allow breakaway: plain detached is still fine
        return subprocess.Popen(cmd, creationflags=base, **io)


def gui_python() -> str:
    """The window-less pythonw.exe next to the running interpreter (falls back to sys.executable)."""
    exe = sys.executable or "pythonw"
    folder, name = os.path.split(exe)
    if name.lower() == "python.exe":
        gui = os.path.join(folder, "pythonw.exe")
        if os.path.exists(gui):
            return gui
    return exe


def kill_tree(proc) -> None:
    """Stop a helper and everything it started (Windows keeps grandchildren alive otherwise)."""
    try:
        if proc.stdin:
            proc.stdin.close()
    except Exception:
        pass
    if proc.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=10,
                           stdin=subprocess.DEVNULL, **hidden())
        except Exception:
            pass
    try:
        if proc.poll() is None:
            proc.kill()
    except Exception:
        pass


def run_capture(cmd, timeout, cwd=None, env=None, input_text=None, on_line=None, should_stop=None,
                on_start=None, poll=0.2):
    """Run a console helper hidden and NEVER hang.

    subprocess.run(timeout=...) can block forever on Windows: after the timeout it kills only the child and
    then waits for the output pipes to close, but a grandchild (PowerShell, a program it opened...) still holds
    them. Here the output is read by background threads, the whole process tree is killed on timeout/stop, and
    the reader threads are only waited for a few seconds.

    on_line(text) is called for every stdout line as it arrives; should_stop() is checked a few times a second;
    on_start(proc) gets the process right after it started.
    Returns (stdout, stderr, returncode, reason) where reason is None, "timeout" or "stopped".
    """
    import threading
    import time
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", **hidden())
    if on_start is not None:
        try:
            on_start(proc)
        except Exception:
            pass
    out, err = [], []

    def read_out():
        try:
            for line in proc.stdout:
                out.append(line)
                if on_line is not None:
                    try:
                        on_line(line.rstrip("\r\n"))
                    except Exception:
                        pass
        except Exception:
            pass

    def read_err():
        try:
            for line in proc.stderr:
                err.append(line)
                if len(err) > 2000:
                    del err[:1000]
        except Exception:
            pass

    def write_in():
        try:
            proc.stdin.write(input_text)
            proc.stdin.close()
        except Exception:
            pass

    threads = [threading.Thread(target=read_out, daemon=True), threading.Thread(target=read_err, daemon=True)]
    if input_text is not None:
        threads.append(threading.Thread(target=write_in, daemon=True))
    for t in threads:
        t.start()
    deadline = time.time() + max(1.0, float(timeout))
    reason = None
    while proc.poll() is None:
        if should_stop is not None:
            try:
                if should_stop():
                    reason = "stopped"
                    break
            except Exception:
                pass
        if time.time() > deadline:
            reason = "timeout"
            break
        time.sleep(poll)
    if reason:
        kill_tree(proc)
    for t in threads[:2]:
        t.join(timeout=3)
    try:
        code = proc.wait(timeout=5)
    except Exception:
        code = proc.poll()
    return "".join(out), "".join(err), code, reason


def child_python() -> str:
    """The console python.exe next to the running interpreter (falls back to sys.executable)."""
    exe = sys.executable or "python"
    folder, name = os.path.split(exe)
    if name.lower() == "pythonw.exe":
        console = os.path.join(folder, "python.exe")
        if os.path.exists(console):
            return console
    return exe
