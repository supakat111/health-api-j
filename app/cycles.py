"""Cycle phase calculation — mirrors the tracker's logic so charts can show
the same phase context. Cycle start dates live in the tracker's app_state
table under key 'cycle_starts'.
"""
from datetime import date as _date
from supabase import create_client, Client
from app.config import settings

PERIOD_DURATION = 8


def _client() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_key)


def load_cycle_starts() -> list:
    sb = _client()
    row = sb.table("app_state").select("value").eq("key", "cycle_starts").maybe_single().execute()
    if row and row.data and row.data.get("value"):
        val = row.data["value"]
        return val if isinstance(val, list) else []
    return []


def _parse(d: str) -> _date:
    y, m, dd = d.split("-")
    return _date(int(y), int(m), int(dd))


def cycle_info(date_str: str, cycle_starts: list) -> dict:
    """Return {'day': int|None, 'phase': str} for a given YYYY-MM-DD date."""
    try:
        target = _parse(date_str)
    except Exception:
        return {"day": None, "phase": "Unknown"}

    starts = sorted([s for s in cycle_starts], reverse=True)
    current = None
    for s in starts:
        try:
            if _parse(s) <= target:
                current = s
                break
        except Exception:
            continue
    if not current:
        return {"day": None, "phase": "Unknown"}

    day = (target - _parse(current)).days + 1
    if day <= PERIOD_DURATION:
        phase = "Menstrual"
    elif day <= 13:
        phase = "Follicular"
    elif day <= 16:
        phase = "Ovulatory"
    else:
        phase = "Luteal"
    return {"day": day, "phase": phase}
