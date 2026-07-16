import json
import os
from datetime import datetime, timedelta
import core.config as config

STATS_FILE = os.path.join(config.BASE_DIR, "state", "stats.json")
STATS_RETENTION_DAYS = 400


def _prune_daily_stats(stats):
    if not isinstance(stats, dict):
        return {"daily": {}}
    daily = stats.get("daily", {})
    if not isinstance(daily, dict):
        daily = {}
    cutoff = datetime.now().date() - timedelta(days=STATS_RETENTION_DAYS)
    retained = {}
    for day, values in daily.items():
        try:
            if datetime.strptime(str(day), "%Y-%m-%d").date() >= cutoff:
                retained[str(day)] = values
        except (TypeError, ValueError):
            continue
    return {**stats, "daily": retained}

def _load_stats():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                return _prune_daily_stats(json.load(f))
        except:
            pass
    return {"daily": {}}

def _save_stats(stats):
    os.makedirs(os.path.dirname(STATS_FILE), exist_ok=True)
    temp_path = f"{STATS_FILE}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(_prune_daily_stats(stats), f, ensure_ascii=False, indent=4)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp_path, STATS_FILE)

def increment_stat(type_stat):
    """type_stat: 'scanned' hoặc 'uploaded'"""
    stats = _load_stats()
    today = datetime.now().strftime("%Y-%m-%d")
    
    if today not in stats["daily"]:
        stats["daily"][today] = {"scanned": 0, "uploaded": 0}
    
    stats["daily"][today][type_stat] = stats["daily"][today].get(type_stat, 0) + 1
    _save_stats(stats)

def get_stats_today():
    stats = _load_stats()
    today = datetime.now().strftime("%Y-%m-%d")
    return stats["daily"].get(today, {"scanned": 0, "uploaded": 0})


def get_pending_count() -> int:
    """Ước lượng video đã quét nhưng chưa đăng hôm nay."""
    t = get_stats_today()
    return max(0, int(t.get("scanned", 0)) - int(t.get("uploaded", 0)))


_VI_DAYS = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"]


def get_last_7_days_stats():
    stats = _load_stats()
    import datetime as dt
    result = []
    for i in range(6, -1, -1):
        day_dt = datetime.now() - dt.timedelta(days=i)
        date = day_dt.strftime("%Y-%m-%d")
        day_name = _VI_DAYS[day_dt.weekday()]
        data = stats["daily"].get(date, {"scanned": 0, "uploaded": 0})
        result.append({
            "date": date,
            "day": day_name,
            "scanned": data.get("scanned", 0),
            "uploaded": data.get("uploaded", 0),
        })
    return result


def prune_stats_history() -> int:
    if not os.path.exists(STATS_FILE):
        return 0
    try:
        with open(STATS_FILE, "r", encoding="utf-8") as f:
            stats = json.load(f)
    except Exception:
        return 0
    before = len(stats.get("daily", {}))
    pruned = _prune_daily_stats(stats)
    _save_stats(pruned)
    return max(0, before - len(pruned.get("daily", {})))

