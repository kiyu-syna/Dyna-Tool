import os
import subprocess
import sys

sys.dont_write_bytecode = True

import core.config as config


def launch_desktop_app() -> int:
    """Launch the built Electron frontend; Electron owns the Python API process."""
    desktop_dir = os.path.join(config.BASE_DIR, "desktop")
    electron_exe = os.path.join(
        desktop_dir,
        "node_modules",
        "electron",
        "dist",
        "electron.exe",
    )
    entry_html = os.path.join(desktop_dir, "dist", "index.html")
    if not os.path.exists(electron_exe):
        raise RuntimeError(
            "Chưa cài Electron. Hãy chạy 'npm install' trong thư mục desktop."
        )
    if not os.path.exists(entry_html):
        raise RuntimeError(
            "Frontend Electron chưa được build. Hãy chạy 'npm run build' "
            "trong thư mục desktop."
        )

    process = subprocess.Popen([electron_exe, desktop_dir], cwd=desktop_dir)
    try:
        return int(process.wait() or 0)
    except KeyboardInterrupt:
        process.terminate()
        return int(process.wait() or 0)


if __name__ == "__main__":
    raise SystemExit(launch_desktop_app())
