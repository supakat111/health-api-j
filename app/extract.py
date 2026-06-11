"""Lab value extraction using Claude.

Takes the raw bytes of a bloodwork PDF, asks Claude to read every result
off it, and returns a clean list of structured values. We deliberately ask
for EVERY value on the report (not a fixed subset), plus each value's unit
and reference range, so the data is complete and chartable later.

Two kinds of flags are computed here, not by the model:
  - out_of_range: the numeric value falls outside its printed reference range
  - needs_review: the value looks implausible or the model was unsure
This keeps the review screen short — you only eyeball the flagged ones.
"""
import base64
import json
from anthropic import Anthropic

from app.config import settings


client = Anthropic(api_key=settings.anthropic_api_key)


EXTRACTION_PROMPT = """You are reading a laboratory blood test report. Extract EVERY individual test result you can find on the report.

Return ONLY a JSON object (no prose, no markdown fences) in exactly this shape:

{
  "report_date": "YYYY-MM-DD or null if not found",
  "lab_name": "the lab/company name if shown, else null",
  "results": [
    {
      "test_name": "the test name exactly as printed, e.g. Ferritin",
      "value_text": "the result exactly as printed, e.g. 45 or <5 or Positive",
      "value_num": 45.0,            // the numeric value if it is a number, else null
      "unit": "ng/mL or null",
      "ref_low": 30.0,              // low end of reference range if printed, else null
      "ref_high": 400.0,            // high end of reference range if printed, else null
      "ref_text": "30-400 or whatever was printed, else null",
      "uncertain": false            // true if you could not read it confidently
    }
  ]
}

Rules:
- Include every result, even ones without a reference range.
- value_num must be a real number or null — never a string, never a range.
- If a value is like "<5" or "Positive", put that in value_text and set value_num to null.
- Do not invent reference ranges. If none is printed, use null.
- Set "uncertain": true for any result whose text or numbers you are not confident you read correctly.
- Output the JSON object and nothing else.
"""


def _is_pdf(file_bytes: bytes) -> bool:
    return file_bytes[:5] == b"%PDF-"


def extract_from_file(file_bytes: bytes, media_type: str) -> dict:
    """Call Claude with the document and return parsed extraction dict.

    media_type is e.g. 'application/pdf' or 'image/jpeg'.
    Raises ValueError if the model output can't be parsed as JSON.
    """
    b64 = base64.standard_b64encode(file_bytes).decode("utf-8")

    if media_type == "application/pdf":
        source_block = {
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
        }
    else:
        source_block = {
            "type": "image",
            "source": {"type": "base64", "media_type": media_type, "data": b64},
        }

    msg = client.messages.create(
        model=settings.model,
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": [source_block, {"type": "text", "text": EXTRACTION_PROMPT}],
        }],
    )

    text = "".join(block.text for block in msg.content if block.type == "text").strip()

    # Be tolerant if the model wraps the JSON in code fences despite instructions.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Could not parse extraction output as JSON: {e}")

    return _apply_flags(data)


def _apply_flags(data: dict) -> dict:
    """Compute out_of_range and needs_review for each result."""
    for r in data.get("results", []):
        v = r.get("value_num")
        lo = r.get("ref_low")
        hi = r.get("ref_high")

        out_of_range = False
        if isinstance(v, (int, float)):
            if isinstance(lo, (int, float)) and v < lo:
                out_of_range = True
            if isinstance(hi, (int, float)) and v > hi:
                out_of_range = True
        r["out_of_range"] = out_of_range

        # needs_review when: the model was unsure, OR a numeric value has no
        # range to check against AND no unit (often a sign of a misread), OR
        # the number is wildly implausible (negative, or absurdly large).
        needs_review = bool(r.get("uncertain"))
        if isinstance(v, (int, float)):
            if v < 0 or v > 1_000_000:
                needs_review = True
        r["needs_review"] = needs_review

    return data
