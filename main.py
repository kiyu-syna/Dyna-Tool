import os
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True

import core.config as config


def launch_desktop_app() -> int:
    """Launch Tauri from the current source tree."""

    desktop_dir = os.path.join(config.BASE_DIR, "desktop")
    package_json = os.path.join(desktop_dir, "package.json")
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if npm and os.path.isfile(package_json):
        # Always use the current Vite source instead of a release binary that
        # may still contain an older login screen.
        return _wait_for_desktop_process([npm, "run", "dev"], desktop_dir)

    tauri_candidates = (
        os.path.join(desktop_dir, "src-tauri", "target", "debug", "dyna.exe"),
        os.path.join(desktop_dir, "src-tauri", "target", "release", "dyna.exe"),
    )
    tauri_exe = next((path for path in tauri_candidates if os.path.isfile(path)), None)
    if not tauri_exe:
        raise RuntimeError(
            "Không tìm thấy npm hoặc Tauri desktop binary. "
            "Hãy cài Node.js rồi chạy `cd desktop; npm run dev`."
        )

    return _wait_for_desktop_process([tauri_exe], desktop_dir)


def launch_electron_fallback() -> int:
    """Launch the retained Electron fallback explicitly."""
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

    return _wait_for_desktop_process([electron_exe, desktop_dir], desktop_dir)


def _wait_for_desktop_process(
    command: list[str], desktop_dir: str, environment: dict[str, str] | None = None
) -> int:
    process = subprocess.Popen(command, cwd=desktop_dir, env=environment)
    try:
        return int(process.wait() or 0)
    except KeyboardInterrupt:
        process.terminate()
        return int(process.wait() or 0)


if __name__ == "__main__":
    raise SystemExit(launch_desktop_app())
