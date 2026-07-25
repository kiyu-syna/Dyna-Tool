from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.browser.browser_runtime_service import install_browser_runtime


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sao chép một Chromium runtime sang vùng do Dyna quản lý."
    )
    parser.add_argument("source_executable", help="Đường dẫn chrome.exe/Chromium nguồn")
    parser.add_argument("--runtime-id", default="", help="Ví dụ: iron-141")
    parser.add_argument("--destination-root", default="", help="Thư mục runtime tùy chọn")
    args = parser.parse_args()
    info = install_browser_runtime(
        args.source_executable,
        runtime_id=args.runtime_id,
        destination_root=args.destination_root or None,
    )
    print(json.dumps(info.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
