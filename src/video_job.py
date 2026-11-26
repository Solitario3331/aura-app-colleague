"""Background worker: python video_job.py <url> <model> <ollama_url>
Runs the whole video analysis and keeps writing progress to the video's cache folder (see video_brain.py)."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import video_brain as vb  # noqa: E402
from video_cache import Store, video_key  # noqa: E402

LOG = Path(__file__).resolve().parent.parent / "logs" / "video_job.log"


def log(msg):
    try:
        LOG.parent.mkdir(exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except OSError:
        pass


def main():
    url, model, ollama_url = sys.argv[1], sys.argv[2], sys.argv[3]
    store = Store(vb.CACHE / video_key(url))
    log("job start " + url)
    try:
        vb.analyze(url, model, ollama_url, log)
        log("job done " + url)
    except Exception as exc:
        log("job FAILED %r" % (exc,))
        msg = str(exc) if isinstance(exc, RuntimeError) else "something went wrong (%s)" % type(exc).__name__
        store.progress("failed", 0, 0, msg, failed=True)


if __name__ == "__main__":
    main()
