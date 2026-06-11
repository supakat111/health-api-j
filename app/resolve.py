"""Canonical test resolution.

Turns the messy, lab-specific test names and units coming off a report into a
consistent set you can chart over time, WITHOUT ever silently merging things
that might be different or converting units that aren't safe to convert.

How it works:
  - normalize_name(): lowercases + trims so "FERRITIN" and "Ferritin " match.
  - resolve_results(): for each extracted value, look up its normalized name in
    test_aliases. If found, attach the canonical test (and convert units when
    safe). If not found, mark it as needing a mapping decision from the user.
  - Unit handling is conservative: only known 1:1 relabels are auto-converted.
    Anything else is left untouched and flagged 'unit_mismatch' for human review.
"""
from supabase import create_client, Client
from app.config import settings


def _client() -> Client:
    return create_client(settings.supabase_url, settings.supabase_service_key)


def normalize_name(name: str) -> str:
    return (name or "").strip().lower()


# Known SAFE 1:1 unit relabels: same physical quantity, factor of exactly 1.
# Stored as frozensets of equivalent unit spellings. Only conversions where the
# number does NOT change belong here. Anything needing a molecular-weight factor
# (e.g. mg/dL <-> mmol/L) is deliberately excluded — those get flagged instead.
SAFE_EQUIVALENT_UNITS = [
    {"ng/ml", "ug/l", "µg/l", "mcg/l"},      # ferritin, vitamin D, etc.
    {"pg/ml", "ng/l"},
    {"g/dl", "g/100ml"},
    {"mg/l", "ug/ml", "µg/ml", "mcg/ml"},
    {"k/ul", "k/µl", "10*3/ul", "10e3/ul", "thousand/ul", "x10^3/ul"},
    {"m/ul", "m/µl", "10*6/ul", "10e6/ul", "million/ul", "x10^6/ul"},
    {"u/l", "iu/l"},
    {"%", "percent"},
]


def _norm_unit(u: str) -> str:
    return (u or "").strip().lower().replace(" ", "")


def units_are_safely_equivalent(raw_unit: str, canonical_unit: str) -> bool:
    a, b = _norm_unit(raw_unit), _norm_unit(canonical_unit)
    if not a or not b:
        return False
    if a == b:
        return True
    for group in SAFE_EQUIVALENT_UNITS:
        if a in group and b in group:
            return True
    return False


def load_alias_map() -> dict:
    """Return {normalized_alias_name: canonical_id}."""
    sb = _client()
    rows = sb.table("test_aliases").select("alias_name, canonical_id").execute().data or []
    return {r["alias_name"]: r["canonical_id"] for r in rows}


def load_canonical_tests() -> list:
    """Return list of canonical tests for the review-screen dropdown."""
    sb = _client()
    return sb.table("canonical_tests").select("*").order("display_name").execute().data or []


def resolve_results(results: list) -> dict:
    """Annotate each result with canonical mapping + unit status.

    Returns:
      {
        "results": [ ...each result now has canonical_id (or None),
                     canonical_name, unit_flag... ],
        "unmapped": [ list of distinct raw test names with no alias yet ]
      }
    """
    alias_map = load_alias_map()
    canon = {c["id"]: c for c in load_canonical_tests()}

    unmapped = {}
    for r in results:
        raw_name = r.get("test_name") or ""
        r["raw_test_name"] = raw_name
        r["raw_unit"] = r.get("unit")
        norm = normalize_name(raw_name)

        cid = alias_map.get(norm)
        r["canonical_id"] = cid
        r["canonical_name"] = canon[cid]["display_name"] if cid in canon else None

        # Unit status, only meaningful once we know the canonical unit.
        r["unit_flag"] = None
        if cid in canon:
            cunit = canon[cid].get("canonical_unit")
            if cunit and r.get("unit"):
                if units_are_safely_equivalent(r["unit"], cunit):
                    # 1:1 relabel — number unchanged, just record canonical unit.
                    r["unit_flag"] = "converted" if _norm_unit(r["unit"]) != _norm_unit(cunit) else None
                else:
                    r["unit_flag"] = "unit_mismatch"

        if cid is None and norm:
            unmapped[norm] = raw_name  # keep first-seen original spelling

    return {"results": results, "unmapped": list(unmapped.values())}


def create_canonical(display_name: str, canonical_unit: str = None,
                     ref_low=None, ref_high=None, category: str = None) -> str:
    sb = _client()
    row = sb.table("canonical_tests").insert({
        "display_name": display_name,
        "canonical_unit": canonical_unit,
        "ref_low": ref_low,
        "ref_high": ref_high,
        "category": category,
    }).execute()
    return row.data[0]["id"]


def add_alias(raw_name: str, canonical_id: str):
    sb = _client()
    norm = normalize_name(raw_name)
    # upsert so re-mapping the same alias doesn't error
    sb.table("test_aliases").upsert(
        {"alias_name": norm, "canonical_id": canonical_id},
        on_conflict="alias_name",
    ).execute()
