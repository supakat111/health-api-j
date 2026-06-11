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
        })
    if rows:
        sb.table("lab_results").insert(rows).execute()

    return report_id
