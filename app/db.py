"""Supabase access — uses the SERVICE key, so this must only ever run
server-side. It can write to the lab tables and the storage bucket directly.
"""
import uuid
from datetime import datetime
from supabase import create_client, Client

from app.config import settings


def _client() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_key)


def upload_pdf(file_bytes: bytes, original_name: str, content_type: str) -> str:
    """Store the original file in the bloodwork bucket. Returns the storage path."""
    sb = _client()
    safe_name = original_name.replace("/", "_").replace("\\", "_")
    path = f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}_{safe_name}"
    sb.storage.from_(settings.bucket).upload(
        path,
        file_bytes,
        {"content-type": content_type},
    )
    return path


def save_report(report_date, lab_name, file_path, file_name, results: list) -> str:
    """Create one lab_reports row and its lab_results rows. Returns report id."""
    sb = _client()

    report_insert = sb.table("lab_reports").insert({
        "report_date": report_date,
        "lab_name": lab_name,
        "file_path": file_path,
        "file_name": file_name,
        "status": "reviewed",
    }).execute()
    report_id = report_insert.data[0]["id"]

    rows = []
    for r in results:
        rows.append({
            "report_id": report_id,
            "report_date": report_date,
            "test_name": r.get("test_name"),
            "value_num": r.get("value_num"),
            "value_text": r.get("value_text"),
            "unit": r.get("unit"),
            "ref_low": r.get("ref_low"),
            "ref_high": r.get("ref_high"),
            "ref_text": r.get("ref_text"),
            "out_of_range": bool(r.get("out_of_range")),
            "needs_review": bool(r.get("needs_review")),
            "canonical_id": r.get("canonical_id"),
            "raw_test_name": r.get("raw_test_name") or r.get("test_name"),
            "raw_unit": r.get("raw_unit") or r.get("unit"),
            "unit_flag": r.get("unit_flag"),
        })
    if rows:
        sb.table("lab_results").insert(rows).execute()

    return report_id


# ── Reads for the charts / trends dashboard ──────────────────────────────────

def fetch_daily_entries() -> list:
    """All tracker daily entries from the tracker's `entries` table, oldest first.
    Each row's `data` is the JSON the tracker saved (pain, sleep, etc.) and the
    key/id encodes the date."""
    sb = _client()
    rows = sb.table("entries").select("id, data").execute().data or []
    out = []
    for r in rows:
        d = r.get("data") or {}
        # the tracker stores the date inside data.date; fall back to the id
        date = d.get("date") or r.get("id")
        if date:
            d = {**d, "date": date}
            out.append(d)
    out.sort(key=lambda e: e.get("date", ""))
    return out


def fetch_lab_series() -> dict:
    """Lab results grouped by canonical test (falling back to raw name when a
    value was never mapped). Returns:
       { test_label: {"unit": str|None, "ref_low": .., "ref_high": ..,
                      "points": [{"date":.., "value":..}, ...]} }
    """
    sb = _client()
    results = sb.table("lab_results").select(
        "report_date, test_name, raw_test_name, value_num, unit, ref_low, ref_high, canonical_id"
    ).execute().data or []

    canon = {c["id"]: c for c in (sb.table("canonical_tests").select("*").execute().data or [])}

    series = {}
    for r in results:
        if r.get("value_num") is None:
            continue  # non-numeric values can't be charted
        cid = r.get("canonical_id")
        if cid and cid in canon:
            label = canon[cid]["display_name"]
            unit = canon[cid].get("canonical_unit") or r.get("unit")
            ref_low = canon[cid].get("ref_low")
            ref_high = canon[cid].get("ref_high")
        else:
            label = r.get("test_name") or r.get("raw_test_name") or "Unknown"
            unit = r.get("unit")
            ref_low = r.get("ref_low")
            ref_high = r.get("ref_high")

        s = series.setdefault(label, {"unit": unit, "ref_low": ref_low, "ref_high": ref_high, "points": []})
        s["points"].append({"date": r.get("report_date"), "value": r["value_num"]})

    for s in series.values():
        s["points"].sort(key=lambda p: p.get("date") or "")
    return series


# ── Lab surveillance: what's off, borderline, or changed ─────────────────────

def lab_surveillance(borderline_pct: float = 0.10) -> dict:
    """For each canonical (or raw) lab marker, look at its most recent value and
    classify it. Borderline = within `borderline_pct` of either reference edge.
    Also report the change from the previous draw when available.

    Returns:
      { "markers": [ { name, unit, latest_value, latest_date, ref_low, ref_high,
                       status: 'high'|'low'|'borderline_high'|'borderline_low'|'normal'|'unknown',
                       prev_value, prev_date, delta, direction } ],
        "counts": {high, low, borderline, normal, unknown} }
    """
    series = fetch_lab_series()
    markers = []
    counts = {"high": 0, "low": 0, "borderline": 0, "normal": 0, "unknown": 0}

    for name, s in series.items():
        pts = [p for p in s["points"] if p.get("value") is not None]
        if not pts:
            continue
        pts.sort(key=lambda p: p.get("date") or "")
        latest = pts[-1]
        prev = pts[-2] if len(pts) >= 2 else None
        lo, hi = s.get("ref_low"), s.get("ref_high")
        v = latest["value"]

        status = "unknown"
        if isinstance(lo, (int, float)) or isinstance(hi, (int, float)):
            status = "normal"
            if isinstance(hi, (int, float)) and v > hi:
                status = "high"
            elif isinstance(lo, (int, float)) and v < lo:
                status = "low"
            else:
                # within range — check borderline against whichever edges exist
                if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
                    band = (hi - lo) * borderline_pct
                    if v <= lo + band:
                        status = "borderline_low"
                    elif v >= hi - band:
                        status = "borderline_high"

        if status in ("high",):
            counts["high"] += 1
        elif status in ("low",):
            counts["low"] += 1
        elif status in ("borderline_low", "borderline_high"):
            counts["borderline"] += 1
        elif status == "normal":
            counts["normal"] += 1
        else:
            counts["unknown"] += 1

        delta = direction = None
        if prev and isinstance(prev.get("value"), (int, float)):
            delta = round(v - prev["value"], 4)
            direction = "up" if delta > 0 else ("down" if delta < 0 else "flat")

        markers.append({
            "name": name, "unit": s.get("unit"),
            "latest_value": v, "latest_date": latest.get("date"),
            "ref_low": lo, "ref_high": hi, "status": status,
            "prev_value": prev["value"] if prev else None,
            "prev_date": prev.get("date") if prev else None,
            "delta": delta, "direction": direction,
            "n_points": len(pts),
        })

    # sort so attention-worthy items float to top
    rank = {"high":0,"low":0,"borderline_high":1,"borderline_low":1,"normal":2,"unknown":3}
    markers.sort(key=lambda m: (rank.get(m["status"],3), m["name"].lower()))
    return {"markers": markers, "counts": counts}
