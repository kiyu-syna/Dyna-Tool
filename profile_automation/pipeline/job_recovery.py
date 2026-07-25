from __future__ import annotations

import os


def next_status_after_cancellation(job: dict) -> str:
    """Return the resumable state after resource waiting is cancelled."""
    return "downloaded" if job.get("download_path") else "caption_ready"


def discard_invalid_download(path: str) -> bool:
    """Remove an invalid media file; report whether it is gone."""
    if not path:
        return True
    try:
        os.remove(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return True


def cleanup_completed_download(path: str) -> bool:
    """Remove media after every enabled upload has completed."""
    if not path or not os.path.isfile(path):
        return True
    try:
        os.remove(path)
    except OSError:
        return False
    return True
