from __future__ import annotations

import threading
import time
from datetime import datetime
from urllib.parse import urlparse

import psutil

from services.browser.gemlogin_browser_service import (
    get_cached_gemlogin_debug_address,
    get_gemlogin_profile_health,
)
from services.browser.browser_profile_service import browser_provider
from services.browser.local_chromium_browser_service import get_local_chromium_processes


class ProfileResourceMonitor:
    """Attribute GemLogin browser process trees to configured Dyna profiles."""

    def __init__(self, cache_seconds: float = 1.0):
        self.cache_seconds = max(0.2, float(cache_seconds))
        self._lock = threading.RLock()
        self._last_sample_at = 0.0
        self._last_snapshot: dict = {}
        self._processes: dict[int, psutil.Process] = {}

    @staticmethod
    def _port(debug_address: str) -> int:
        value = str(debug_address or "").strip()
        if not value:
            return 0
        parsed = urlparse(value if "://" in value else f"http://{value}")
        try:
            return int(parsed.port or 0)
        except ValueError:
            return 0

    @staticmethod
    def _gemlogin_ids(profile_id: str, profile: dict) -> list[str]:
        ids = {str(profile_id)}
        for section in ("douyin", "tiktok", "facebook", "youtube"):
            configured = str(
                ((profile.get(section) or {}).get("gemlogin_profile_id") or "")
            ).strip()
            if configured:
                ids.add(configured)
        return sorted(ids)

    @staticmethod
    def _listener_pids() -> dict[int, int]:
        listeners: dict[int, int] = {}
        try:
            connections = psutil.net_connections(kind="tcp")
        except (psutil.AccessDenied, OSError):
            return listeners
        for connection in connections:
            if not connection.pid or not connection.laddr:
                continue
            status = str(connection.status or "").upper()
            if status and status != "LISTEN":
                continue
            try:
                port = int(getattr(connection.laddr, "port", connection.laddr[1]))
            except (IndexError, TypeError, ValueError):
                continue
            listeners[port] = int(connection.pid)
        return listeners

    def _process_tree(self, root_pid: int) -> list[psutil.Process]:
        try:
            root = self._processes.get(root_pid) or psutil.Process(root_pid)
            self._processes[root_pid] = root
            processes = [root, *root.children(recursive=True)]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return []
        result = []
        seen = set()
        for process in processes:
            if process.pid in seen:
                continue
            seen.add(process.pid)
            self._processes.setdefault(process.pid, process)
            result.append(self._processes[process.pid])
        return result

    def snapshot(self, profiles: dict[str, dict]) -> dict:
        now = time.monotonic()
        with self._lock:
            if self._last_snapshot and now - self._last_sample_at < self.cache_seconds:
                return self._last_snapshot

            listeners = self._listener_pids()
            profile_resources: dict[str, dict] = {}
            live_pids: set[int] = set()
            for profile_id, profile in profiles.items():
                roots = set()
                addresses = []
                provider = browser_provider(profile)
                gemlogin_ids: list[str] = []
                if provider == "local_chromium":
                    try:
                        roots.update(
                            process.pid for process in get_local_chromium_processes(profile)
                        )
                    except Exception:
                        pass
                else:
                    gemlogin_ids = self._gemlogin_ids(str(profile_id), profile)
                    for gemlogin_id in gemlogin_ids:
                        address = get_cached_gemlogin_debug_address(gemlogin_id)
                        if not address:
                            address = str(
                                get_gemlogin_profile_health(gemlogin_id).get("debug_address") or ""
                            )
                        if address:
                            addresses.append(address)
                        port = self._port(address)
                        if port and listeners.get(port):
                            roots.add(listeners[port])

                processes: dict[int, psutil.Process] = {}
                for root_pid in roots:
                    for process in self._process_tree(root_pid):
                        processes[process.pid] = process
                live_pids.update(processes)

                ram_bytes = 0
                cpu_percent = 0.0
                for process in processes.values():
                    try:
                        ram_bytes += int(process.memory_info().rss)
                        cpu_percent += float(process.cpu_percent(interval=None))
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
                profile_resources[str(profile_id)] = {
                    "ram_mb": round(ram_bytes / (1024 * 1024), 1),
                    "cpu_percent": round(cpu_percent, 1),
                    "process_count": len(processes),
                    "browser_pids": sorted(processes),
                    "browser_provider": provider,
                    "gemlogin_ids": gemlogin_ids,
                    "debug_addresses": sorted(set(addresses)),
                }

            self._processes = {
                pid: process for pid, process in self._processes.items() if pid in live_pids
            }
            memory = psutil.virtual_memory()
            snapshot = {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "system": {
                    "ram_percent": round(float(memory.percent), 1),
                    "ram_used_mb": round(float(memory.used) / (1024 * 1024), 1),
                    "ram_available_mb": round(float(memory.available) / (1024 * 1024), 1),
                    "cpu_percent": round(float(psutil.cpu_percent(interval=None)), 1),
                },
                "profiles": profile_resources,
            }
            self._last_sample_at = now
            self._last_snapshot = snapshot
            return snapshot


profile_resource_monitor = ProfileResourceMonitor()
