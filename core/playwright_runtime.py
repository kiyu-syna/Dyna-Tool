from __future__ import annotations

import os
from pathlib import Path


def configure_packaged_playwright_driver() -> tuple[Path, Path] | None:
    """Point Playwright at the driver shipped beside the packaged backend."""
    configured = str(os.environ.get("DYNA_PLAYWRIGHT_DRIVER_DIR") or "").strip()
    if not configured:
        return None

    driver_dir = Path(configured).expanduser().resolve()
    node_path = driver_dir / ("node.exe" if os.name == "nt" else "node")
    cli_path = driver_dir / "package" / "cli.js"
    if not node_path.is_file() or not cli_path.is_file():
        raise RuntimeError(
            "Bản cài Dyna thiếu Playwright driver: "
            f"{node_path} hoặc {cli_path}. Hãy cài lại bản Dyna đầy đủ."
        )

    def packaged_driver_executable() -> tuple[str, str]:
        return str(node_path), str(cli_path)

    from playwright._impl import _driver, _transport

    _driver.compute_driver_executable = packaged_driver_executable
    _transport.compute_driver_executable = packaged_driver_executable
    os.environ["PLAYWRIGHT_NODEJS_PATH"] = str(node_path)
    return node_path, cli_path
