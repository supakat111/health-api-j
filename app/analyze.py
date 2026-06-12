"""AI pattern analysis.

Pulls the daily tracker data and lab results, builds a compact text summary,
and asks Claude to surface possible patterns in THREE framings at once so the
user can compare and choose which style is most useful:

  doctor         - observations only, worded for an appointment
  context        - same observations + brief plain-language explanation
  interpretation - goes further toward what patterns might suggest

SAFETY: every framing is explicitly told it is not giving medical advice or a
diagnosis, to be cautious about coincidental patterns in limited data, and to
frame findings as questions for a clinician. The 'interpretation' framing is
the most permissive but is still bounded to "might suggest / worth asking",
never "you have X" or "you should take Y".
"""
import json
from anthropic import Anthropic

from app.config import settings
from app.db import fetch_daily_entries, fetch_lab_series, lab_surveillance
from app.cycles import load_cycle_starts, cycle_info


def _build_summary() -> dict:
    daily = fetch_daily_entries()
    labs = fetch_lab_series()
    surv = lab_surveillance(0.10)
    starts = load_cycle_starts()

    # Compact daily summary: counts + per-entry compact rows (cap to keep tokens sane)
    daily_rows = []
    for e in daily[-120:]:  # last ~120 entries max
        d = e.get("date")
        ci = cycle_info(d, starts) if d else {"day": None, "phase": None}
        daily_rows.append({
            "date": d, "cycle_day": ci.get("day"), "phase": ci.get("phase"),
            "pain": e.get("pain"), "sleep": e.get("sleepQuality"),
            "stress": e.get("stress"), "immune": e.get("immuneActivation"),
            "migraine": e.get("migraine"), "bedBound": e.get("bedBound"),
            "ivig": e.get("ivig"),
            "symptoms": e.get("symptoms") or [],
        })

    lab_summary = {}
    for name, s in labs.items():
        pts = [p for p in s["points"] if p.get("value") is not None]
        lab_summary[name] = {
            "unit": s.get("unit"), "ref_low": s.get("ref_low"), "ref_high": s.get("ref_high"),
            "points": [{"date": p["date"], "value": p["value"]} for p in pts],
        }

    return {
        "daily_entries": daily_rows,
        "labs": lab_summary,
        "lab_status_counts": surv["counts"],
        "n_daily": len(daily),
        "n_labs": sum(len(s["points"]) for s in labs.values()),
    }


PROMPT = """You are a careful health-data analyst helping a patient and her doctors review her own tracked data. You are NOT a doctor and must not diagnose or give treatment advice.

Below is a JSON summary of the patient's daily symptom/treatment tracker and her lab results over time.

Produce THREE different framings of any patterns you observe, as a JSON object with keys "doctor", "context", "interpretation". Each value is plain text (you may use short bullet lines with "- ").

Definitions:
- "doctor": ONLY neutral observations of patterns/associations/changes in the data, worded as things to bring to a doctor. No interpretation of cause or meaning. e.g. "Immune-activation scores were higher in the 7 days after each IVIG date."
- "context": the same observations, each followed by a brief plain-language note explaining any medical term used. Still no claims about cause.
- "interpretation": you may go one step further and note what a pattern MIGHT suggest or what question it might raise, but always as "this might be worth asking about" / "one possible explanation to discuss", never as a conclusion, diagnosis, or treatment recommendation.

Critical rules for ALL three:
- Do NOT diagnose. Do NOT recommend treatments, doses, supplements, or medication changes.
- Explicitly avoid over-reading: if the data is sparse, say so plainly and note patterns may be coincidental.
- Only describe patterns actually supported by the data provided. Do not invent data points.
- If there is too little data to find meaningful patterns, say that honestly in each framing rather than manufacturing findings.
- Keep each framing concise (a handful of points).

Return ONLY the JSON object, no prose or code fences.

DATA:
"""


def analyze() -> dict:
    summary = _build_summary()

    # If essentially no data, return an honest note without burning an API call.
    if summary["n_daily"] < 3 and summary["n_labs"] < 3:
        msg = ("There isn't enough data yet for meaningful pattern analysis "
               "(only %d daily entries and %d lab values). Add more daily "
               "check-ins and lab reports over time, then run this again."
               % (summary["n_daily"], summary["n_labs"]))
        return {"note": msg, "framings": {
            "doctor": msg, "context": msg, "interpretation": msg,
        }}

    client = Anthropic(api_key=settings.anthropic_api_key)
    try:
        msg = client.messages.create(
            model=settings.model,
            max_tokens=4096,
            messages=[
                {"role": "user", "content": PROMPT + json.dumps(summary, default=str)},
                {"role": "assistant", "content": "{"},
            ],
        )
        raw = "".join(b.text for b in msg.content if b.type == "text")
        framings = json.loads("{" + raw)
    except Exception as e:
        return {"note": "Analysis could not be generated: %s" % e,
                "framings": {}}

    return {"framings": framings,
            "data_note": "Based on %d daily entries and %d lab values."
                         % (summary["n_daily"], summary["n_labs"])}
