from __future__ import annotations

from pathlib import Path

import core.config as config


def data_root(base_dir: str | Path | None = None) -> Path:
    return Path(base_dir or config.BASE_DIR)


def runtime_root(base_dir: str | Path | None = None) -> Path:
    return data_root(base_dir) / "runtime"


def state_dir(base_dir: str | Path | None = None) -> Path:
    return runtime_root(base_dir) / "state"


def logs_dir(base_dir: str | Path | None = None) -> Path:
    return runtime_root(base_dir) / "logs"


def temp_dir(base_dir: str | Path | None = None) -> Path:
    return runtime_root(base_dir) / "tmp"


def profile_state_dir(base_dir: str | Path | None = None) -> Path:
    return state_dir(base_dir) / "profile-automation"
