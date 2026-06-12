"""Import old Tally tracker data (xlsx) into the current `entries` table.

Mapping (confirmed with user):
  date            <- "Previous Day"
  pain            <- "Pain Level" (1-10)
  sleepQuality    <- "Overall Sleep Quality" (1-5)
  stress          <- "Stress Level" text: Low=2, Medium=5, Spicy=8
  immuneActivation<- "Flu Like Feeling" 0-3 -> 1/4/7/10
  exercise        <- "Did I Exercise" Yes/No
  exerciseNote    <- "Exercise Type" (+ minutes)
  notes           <- "Additional Notes For The Day"
Unmapped fields dropped. Dates that already exist are skipped (never overwrite).
"""
import io
import math
import pandas as pd

from app.db import _client


STRESS_MAP = {"low stress": 2, "medium stress": 5, "spicy stress": 8}
FLU_MAP = {0: 1, 1: 4, 2: 7, 3: 10}


def _num(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        return int(round(float(v)))
    except (ValueError, TypeError):
        return None


def _yesno(v):
    if isinstance(v, str):
        s = v.strip().lower()
        if s == "yes":
            return True
        if s == "no":
            return False
    return None


def _date(v):
    if pd.isna(v):
        return None
    if isinstance(v, str):
        try:
            v = pd.to_datetime(v)
        except Exception:
            return None
    return v.strftime("%Y-%m-%d")


def _blank(date):
    return {
        "date": date, "migraine": None, "pain": None, "bedBound": None,
        "sleepQuality": None, "stress": None, "exercise": None, "exerciseNote": "",
        "ivig": None, "vitCDrip": None, "glutathione": None,
        "hrtProg": None, "hrtEstrogen": None, "hrtDhea": None,
        "sauna": None, "alcohol": None, "suppChange": None, "suppNote": "",
        "medChange": None, "medNote": "", "immuneActivation": None,
        "symptoms": [], "notes": "", "_imported": True,
    }


def build_entries(file_bytes: bytes) -> list:
    df = pd.read_excel(io.BytesIO(file_bytes), sheet_name="Sheet1")
    by_date = {}
    for _, r in df.iterrows():
        date = _date(r.get("Previous Day"))
        if not date:
            continue
        e = _blank(date)
        e["pain"] = _num(r.get("Pain Level"))
        e["sleepQuality"] = _num(r.get("Overall Sleep Quality"))

        st = r.get("Stress Level")
        e["stress"] = STRESS_MAP.get(str(st).strip().lower()) if isinstance(st, str) else None

        flu = _num(r.get("Flu Like Feeling"))
        e["immuneActivation"] = FLU_MAP.get(flu) if flu is not None else None

        e["exercise"] = _yesno(r.get("Did I Exercise"))
        ex_type = r.get("Exercise Type")
        ex_min = r.get("Exercise Minutes")
        note = ex_type.strip() if isinstance(ex_type, str) else ""
        if pd.notna(ex_min):
            note = (note + f" ({int(ex_min)} min)").strip()
        e["exerciseNote"] = note

        notes = r.get("Additional Notes For The Day")
        e["notes"] = notes.strip() if isinstance(notes, str) else ""

        by_date[date] = e  # last submission per day wins
    return sorted(by_date.values(), key=lambda e: e["date"])


def preview(file_bytes: bytes) -> dict:
    entries = build_entries(file_bytes)
    sb = _client()
    existing = {r["id"] for r in (sb.table("entries").select("id").execute().data or [])}
    new = [e for e in entries if e["date"] not in existing]
    dupes = [e["date"] for e in entries if e["date"] in existing]
    return {
        "total": len(entries),
        "new_count": len(new),
        "skip_count": len(dupes),
        "date_range": [entries[0]["date"], entries[-1]["date"]] if entries else [None, None],
        "sample": new[:5],
        "skipped_dates": dupes,
    }


def commit(file_bytes: bytes) -> dict:
    entries = build_entries(file_bytes)
    sb = _client()
    existing = {r["id"] for r in (sb.table("entries").select("id").execute().data or [])}
    written, skipped = 0, 0
    for e in entries:
        if e["date"] in existing:
            skipped += 1
            continue
        sb.table("entries").upsert({"id": e["date"], "data": e}).execute()
        written += 1
    return {"written": written, "skipped": skipped, "total": len(entries)}
