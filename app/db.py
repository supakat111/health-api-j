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
