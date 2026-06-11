"""Health API (Jennifer) — bloodwork extraction service.

Endpoints:
  GET  /healthz            -> simple liveness check (no auth)
  GET  /                   -> the upload page (password gate in the page itself)
  POST /extract            -> receives a PDF/image, returns extracted values (no save yet)
  POST /save               -> receives confirmed values, stores file + rows

Auth is a simple shared password (APP_PASSWORD) sent as a header. This is the
same lightweight gate pattern as the warehouse app — enough to keep the upload
endpoint from being open to the whole internet. It is not multi-user accounts.
"""
from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from app.config import settings
from app.extract import extract_from_file, _is_pdf
from app.db import upload_pdf, save_report, fetch_daily_entries, fetch_lab_series, lab_surveillance
from app.cycles import load_cycle_starts, cycle_info
from app.hub import HUB_PAGE
from app.resolve import (
    resolve_results, load_canonical_tests, create_canonical, add_alias,
    suggest_mappings,
)


app = FastAPI(title="Health API J")

ALLOWED_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp"}


def _check_auth(password: str | None):
    if not settings.app_password:
        raise HTTPException(500, "Server missing APP_PASSWORD configuration.")
    if password != settings.app_password:
        raise HTTPException(401, "Wrong or missing password.")


@app.get("/app", response_class=HTMLResponse)
def hub():
    return HUB_PAGE


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.post("/extract")
async def extract(
    file: UploadFile = File(...),
    x_app_password: str | None = Header(default=None),
):
    _check_auth(x_app_password)

    content_type = file.content_type or ""
    if content_type not in ALLOWED_TYPES:
        raise HTTPException(400, f"Unsupported file type: {content_type}. Upload a PDF or image.")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(400, "Empty file.")

    try:
        data = extract_from_file(file_bytes, content_type)
    except ValueError as e:
        raise HTTPException(422, f"Extraction failed: {e}")
    except Exception as e:
        raise HTTPException(500, f"Unexpected extraction error: {e}")

    # Map each value to a canonical test (or mark as needing a mapping decision).
    try:
        resolved = resolve_results(data.get("results", []))
        data["results"] = resolved["results"]
        data["unmapped"] = resolved["unmapped"]
        cts = load_canonical_tests()
        data["canonical_tests"] = cts
        # AI-suggested mappings for the unmapped names (optional, best-effort).
        data["suggestions"] = suggest_mappings(resolved["unmapped"], cts)
    except Exception as e:
        data["unmapped"] = []
        data["canonical_tests"] = []
        data["suggestions"] = {}
        data["resolve_error"] = str(e)

    return JSONResponse(data)


@app.get("/canonical")
def list_canonical(x_app_password: str | None = Header(default=None)):
    _check_auth(x_app_password)
    return {"canonical_tests": load_canonical_tests()}


@app.post("/canonical")
async def add_canonical(
    payload: str = Form(...),
    x_app_password: str | None = Header(default=None),
):
    """Create a new canonical test and/or map raw names to it.

    payload JSON:
      {
        "display_name": "Ferritin",          # required if creating new
        "canonical_id": "uuid-or-null",      # set to map to an existing one instead
        "canonical_unit": "ng/mL",           # optional
        "aliases": ["FERRITIN", "Ferritin, Serum"]   # raw names to map
      }
    """
    _check_auth(x_app_password)
    import json
    try:
        p = json.loads(payload)
    except json.JSONDecodeError:
        raise HTTPException(400, "payload is not valid JSON.")

    cid = p.get("canonical_id")
    try:
        if not cid:
            if not p.get("display_name"):
                raise HTTPException(400, "Need display_name to create a canonical test.")
            cid = create_canonical(
                display_name=p["display_name"],
                canonical_unit=p.get("canonical_unit"),
                ref_low=p.get("ref_low"),
                ref_high=p.get("ref_high"),
                category=p.get("category"),
            )
        for alias in p.get("aliases", []):
            add_alias(alias, cid)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Saving canonical/alias failed: {e}")

    return {"status": "ok", "canonical_id": cid}


@app.get("/surveillance")
def surveillance(x_app_password: str | None = Header(default=None)):
    _check_auth(x_app_password)
    return lab_surveillance(borderline_pct=0.10)


@app.get("/chartdata")
def chart_data(x_app_password: str | None = Header(default=None)):
    """All data the dashboard needs: daily metrics, lab series, and a per-date
    cycle-phase lookup so the chart can optionally show cycle context."""
    _check_auth(x_app_password)

    daily = fetch_daily_entries()
    labs = fetch_lab_series()
    starts = load_cycle_starts()

    # Build a compact daily-metric series for the numeric fields worth charting.
    metric_fields = ["pain", "sleepQuality", "stress", "immuneActivation"]
    daily_series = {f: [] for f in metric_fields}
    bool_events = {"migraine": [], "bedBound": [], "ivig": [], "vitCDrip": [], "glutathione": []}
    phase_by_date = {}

    for e in daily:
        d = e.get("date")
        if not d:
            continue
        phase_by_date[d] = cycle_info(d, starts)
        for f in metric_fields:
            v = e.get(f)
            if isinstance(v, (int, float)):
                daily_series[f].append({"date": d, "value": v})
        for f in bool_events:
            if e.get(f) is True:
                bool_events[f].append(d)

    # cycle phase for lab dates too
    for s in labs.values():
        for p in s["points"]:
            if p.get("date") and p["date"] not in phase_by_date:
                phase_by_date[p["date"]] = cycle_info(p["date"], starts)

    return {
        "daily_series": daily_series,
        "bool_events": bool_events,
        "labs": labs,
        "phase_by_date": phase_by_date,
    }


@app.post("/save")
async def save(
    file: UploadFile = File(...),
    payload: str = Form(...),
    x_app_password: str | None = Header(default=None),
):
    _check_auth(x_app_password)
    import json
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        raise HTTPException(400, "payload is not valid JSON.")

    content_type = file.content_type or "application/pdf"
    file_bytes = await file.read()

    try:
        path = upload_pdf(file_bytes, file.filename or "report", content_type)
    except Exception as e:
        raise HTTPException(500, f"File upload to storage failed: {e}")

    try:
        report_id = save_report(
            report_date=parsed.get("report_date"),
            lab_name=parsed.get("lab_name"),
            file_path=path,
            file_name=file.filename or "report",
            results=parsed.get("results", []),
        )
    except Exception as e:
        raise HTTPException(500, f"Saving to database failed: {e}")

    return {"status": "saved", "report_id": report_id, "count": len(parsed.get("results", []))}


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Bloodwork upload</title>
<style>
  body { font-family: Georgia, serif; background:#faf7f3; color:#3a2e28; max-width:760px; margin:0 auto; padding:24px 16px; }
  h1 { font-size:20px; }
  .card { background:#fffdf9; border:1px solid #efe8e0; border-radius:14px; padding:18px; margin-bottom:16px; }
  input[type=password], input[type=text] { padding:9px 11px; border:1px solid #e0d8d0; border-radius:9px; font-size:14px; font-family:inherit; }
  button { padding:11px 18px; border:none; border-radius:11px; background:#5a3a1a; color:#fff; font-family:inherit; font-size:14px; font-weight:700; cursor:pointer; }
  button.secondary { background:transparent; color:#5a3a1a; border:2px solid #8a6a4a; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { text-align:left; padding:6px 8px; border-bottom:1px solid #efe8e0; }
  td input { width:100%; box-sizing:border-box; padding:5px 7px; border:1px solid #e8e0d6; border-radius:6px; font-size:13px; font-family:inherit; }
  tr.oor td { background:#fdecea; }
  tr.review td { background:#fdf6e3; }
  .flag { font-size:10px; font-weight:700; padding:2px 6px; border-radius:6px; }
  .flag.oor { background:#f3c7c1; color:#9a3b30; }
  .flag.review { background:#ece0bf; color:#8a6a1a; }
  .muted { color:#9a8a7a; font-size:12px; }
  #status { margin-top:10px; font-size:13px; }
</style></head><body>
<h1>Bloodwork upload</h1>
<div class="card">
  <div><label>Password <input type="password" id="pw" placeholder="app password"/></label></div>
  <p class="muted">Choose a bloodwork PDF (or photo), then Extract. Review the values, fix any highlighted ones, then Save.</p>
  <input type="file" id="file" accept="application/pdf,image/*"/>
  <button id="extractBtn">Extract</button>
  <div id="status"></div>
</div>

<div class="card" id="mapCard" style="display:none">
  <h2 style="font-size:16px; margin-top:0">New tests to map</h2>
  <p class="muted">These names haven't been seen before. For each one, pick an existing test it matches, or mark it as new. This is remembered so the same lab maps automatically next time.</p>
  <table id="mapTbl"><thead><tr>
    <th>Lab printed</th><th>Map to</th><th>If new: clean name</th><th>Unit</th>
  </tr></thead><tbody></tbody></table>
  <p style="margin-top:12px"><button id="applyMapBtn">Save mappings</button></p>
  <div id="mapStatus" class="muted"></div>
</div>

<div class="card" id="resultCard" style="display:none">
  <div style="display:flex; gap:16px; flex-wrap:wrap; margin-bottom:12px">
    <label>Report date <input type="text" id="reportDate" placeholder="YYYY-MM-DD"/></label>
    <label>Lab <input type="text" id="labName" placeholder="lab name"/></label>
  </div>
  <table id="tbl"><thead><tr>
    <th>Test</th><th>Maps to</th><th>Value</th><th>Unit</th><th>Ref low</th><th>Ref high</th><th>Flags</th>
  </tr></thead><tbody></tbody></table>
  <p style="margin-top:14px"><button id="saveBtn">Save to database</button></p>
  <div id="saveStatus" class="muted"></div>
</div>

<script>
let lastResults = [];
let canonicalTests = [];
let unmapped = [];
let suggestions = {};
let lastFile = null;
const $ = s => document.querySelector(s);

$("#extractBtn").onclick = async () => {
  const pw = $("#pw").value;
  const f = $("#file").files[0];
  if (!pw) { $("#status").textContent = "Enter the password first."; return; }
  if (!f) { $("#status").textContent = "Choose a file first."; return; }
  lastFile = f;
  $("#status").textContent = "Extracting… (this takes a few seconds)";
  const fd = new FormData(); fd.append("file", f);
  try {
    const res = await fetch("/extract", { method:"POST", headers:{ "x-app-password": pw }, body: fd });
    if (!res.ok) { $("#status").textContent = "Error: " + (await res.text()); return; }
    const data = await res.json();
    lastResults = data.results || [];
    canonicalTests = data.canonical_tests || [];
    unmapped = data.unmapped || [];
    suggestions = data.suggestions || {};
    $("#reportDate").value = data.report_date || "";
    $("#labName").value = data.lab_name || "";
    renderMapPanel();
    renderTable();
    $("#resultCard").style.display = "block";
    let msg = "Found " + lastResults.length + " values.";
    if (unmapped.length) msg += " " + unmapped.length + " new test name(s) to map first.";
    $("#status").textContent = msg;
  } catch (e) { $("#status").textContent = "Request failed: " + e; }
};

function canonOptions(selected) {
  let opts = '<option value="">— map to —</option>';
  canonicalTests.forEach(c => {
    opts += `<option value="${c.id}" ${selected===c.id?"selected":""}>${esc(c.display_name)}</option>`;
  });
  opts += `<option value="__new__" ${selected==='__new__'?"selected":""}>+ It\\'s a new test</option>`;
  return opts;
}

function renderMapPanel() {
  if (!unmapped.length) { $("#mapCard").style.display = "none"; return; }
  $("#mapCard").style.display = "block";
  const tb = $("#mapTbl tbody"); tb.innerHTML = "";
  unmapped.forEach((name, i) => {
    const sug = suggestions[name] || {};
    // Pre-select: high-confidence existing match -> that test;
    // suggested new -> "__new__"; otherwise leave blank for human attention.
    let preselect = "";
    if (sug.canonical_id && sug.confidence === "high") preselect = sug.canonical_id;
    else if (sug.is_new) preselect = "__new__";

    const conf = sug.canonical_id
      ? `<span class="muted">AI: ${esc(sug.canonical_name)} (${sug.confidence})</span>`
      : (sug.is_new ? `<span class="muted">AI: looks new</span>` : `<span class="muted">AI: unsure</span>`);

    const newName = sug.is_new ? name : name;
    const newUnit = sug.suggested_unit || "";
    const newDisabled = preselect === "__new__" ? "" : "disabled";

    const tr = document.createElement("tr");
    tr.innerHTML =
      `<td>${esc(name)}<br>${conf}</td>`+
      `<td><select data-i="${i}" class="mapSel">${canonOptions(preselect)}</select></td>`+
      `<td><input data-i="${i}" class="mapNew" placeholder="clean name" value="${esc(newName)}" ${newDisabled}/></td>`+
      `<td><input data-i="${i}" class="mapUnit" placeholder="unit" value="${esc(newUnit)}" ${newDisabled}/></td>`;
    tb.appendChild(tr);
  });
  tb.querySelectorAll(".mapSel").forEach(sel => {
    sel.onchange = () => {
      const i = sel.dataset.i;
      const isNew = sel.value === "__new__";
      tb.querySelector(`.mapNew[data-i="${i}"]`).disabled = !isNew;
      tb.querySelector(`.mapUnit[data-i="${i}"]`).disabled = !isNew;
    };
  });
}

$("#applyMapBtn").onclick = async () => {
  const pw = $("#pw").value;
  const tb = $("#mapTbl tbody");
  const rows = [...tb.querySelectorAll("tr")];
  $("#mapStatus").textContent = "Saving mappings…";
  try {
    for (let i = 0; i < rows.length; i++) {
      const sel = tb.querySelector(`.mapSel[data-i="${i}"]`);
      const rawName = unmapped[i];
      if (!sel.value) continue; // skipped
      let payload;
      if (sel.value === "__new__") {
        const clean = tb.querySelector(`.mapNew[data-i="${i}"]`).value.trim() || rawName;
        const unit = tb.querySelector(`.mapUnit[data-i="${i}"]`).value.trim() || null;
        payload = JSON.stringify({ display_name: clean, canonical_unit: unit, aliases: [rawName] });
      } else {
        payload = JSON.stringify({ canonical_id: sel.value, aliases: [rawName] });
      }
      const fd = new FormData(); fd.append("payload", payload);
      const res = await fetch("/canonical", { method:"POST", headers:{ "x-app-password": pw }, body: fd });
      if (!res.ok) { $("#mapStatus").textContent = "Error on '" + rawName + "': " + (await res.text()); return; }
    }
    // Re-extract to re-resolve with the new mappings applied.
    $("#mapStatus").textContent = "Mappings saved. Refreshing values…";
    const fd = new FormData(); fd.append("file", lastFile);
    const res = await fetch("/extract", { method:"POST", headers:{ "x-app-password": pw }, body: fd });
    const data = await res.json();
    lastResults = data.results || [];
    canonicalTests = data.canonical_tests || [];
    unmapped = data.unmapped || [];
    suggestions = data.suggestions || {};
    renderMapPanel();
    renderTable();
    $("#mapStatus").textContent = unmapped.length ? (unmapped.length + " still unmapped.") : "All tests mapped.";
  } catch (e) { $("#mapStatus").textContent = "Failed: " + e; }
};

function renderTable() {
  const tb = $("#tbl tbody"); tb.innerHTML = "";
  lastResults.forEach((r, i) => {
    const tr = document.createElement("tr");
    if (r.out_of_range) tr.className = "oor";
    else if (r.needs_review) tr.className = "review";
    tr.innerHTML =
      `<td><input value="${esc(r.test_name||"")}" data-i="${i}" data-k="test_name"/></td>`+
      `<td class="muted">${r.canonical_name?esc(r.canonical_name):'<span style="color:#c4934a">unmapped</span>'}</td>`+
      `<td><input value="${esc(r.value_text!=null?r.value_text:(r.value_num!=null?r.value_num:""))}" data-i="${i}" data-k="value_text"/></td>`+
      `<td><input value="${esc(r.unit||"")}" data-i="${i}" data-k="unit"/></td>`+
      `<td><input value="${r.ref_low!=null?r.ref_low:""}" data-i="${i}" data-k="ref_low"/></td>`+
      `<td><input value="${r.ref_high!=null?r.ref_high:""}" data-i="${i}" data-k="ref_high"/></td>`+
      `<td>${r.out_of_range?'<span class="flag oor">out of range</span>':''}${r.needs_review?' <span class="flag review">review</span>':''}${r.unit_flag==='unit_mismatch'?' <span class="flag review">unit?</span>':''}${r.unit_flag==='converted'?' <span class="flag" style="background:#dce9e0;color:#3a6a4a">unit ok</span>':''}</td>`;
    tb.appendChild(tr);
  });
  tb.querySelectorAll("input").forEach(inp => {
    inp.oninput = () => {
      const i = +inp.dataset.i, k = inp.dataset.k;
      lastResults[i][k] = inp.value;
    };
  });
}

function esc(s){ return String(s).replace(/"/g,"&quot;").replace(/</g,"&lt;"); }

$("#saveBtn").onclick = async () => {
  const pw = $("#pw").value;
  const f = $("#file").files[0];
  // normalize numeric fields back to numbers/null before saving
  const results = lastResults.map(r => ({
    ...r,
    value_num: isFinite(parseFloat(r.value_text)) ? parseFloat(r.value_text) : null,
    ref_low: r.ref_low===""||r.ref_low==null ? null : parseFloat(r.ref_low),
    ref_high: r.ref_high===""||r.ref_high==null ? null : parseFloat(r.ref_high),
  }));
  const payload = JSON.stringify({
    report_date: $("#reportDate").value || null,
    lab_name: $("#labName").value || null,
    results,
  });
  const fd = new FormData(); fd.append("file", f); fd.append("payload", payload);
  $("#saveStatus").textContent = "Saving…";
  try {
    const res = await fetch("/save", { method:"POST", headers:{ "x-app-password": pw }, body: fd });
    if (!res.ok) { $("#saveStatus").textContent = "Error: " + (await res.text()); return; }
    const out = await res.json();
    $("#saveStatus").textContent = "Saved " + out.count + " values (report " + out.report_id + ").";
  } catch (e) { $("#saveStatus").textContent = "Request failed: " + e; }
};
</script>
</body></html>"""


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD


DASHBOARD = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Health trends</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/luxon@3.4.4/build/global/luxon.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-luxon@1.3.1/dist/chartjs-adapter-luxon.umd.min.js"></script>
<style>
  body { font-family: Georgia, serif; background:#faf7f3; color:#3a2e28; max-width:920px; margin:0 auto; padding:24px 16px; }
  h1 { font-size:20px; } h2 { font-size:15px; margin:0 0 10px; }
  .card { background:#fffdf9; border:1px solid #efe8e0; border-radius:14px; padding:16px; margin-bottom:16px; }
  input[type=password] { padding:9px 11px; border:1px solid #e0d8d0; border-radius:9px; font-size:14px; font-family:inherit; }
  button { padding:10px 16px; border:none; border-radius:10px; background:#5a3a1a; color:#fff; font-family:inherit; font-size:14px; font-weight:700; cursor:pointer; }
  select { padding:8px 10px; border:1px solid #e0d8d0; border-radius:9px; font-size:13px; font-family:inherit; }
  .muted { color:#9a8a7a; font-size:12px; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  @media (max-width:640px){ .grid{ grid-template-columns:1fr; } }
  label.tog { font-size:13px; display:inline-flex; align-items:center; gap:6px; }
  .chartwrap { position:relative; height:240px; }
</style></head><body>
<h1>Health trends</h1>
<div class="card">
  <label>Password <input type="password" id="pw" placeholder="app password"/></label>
  <button id="loadBtn">Load data</button>
  <label class="tog" style="margin-left:16px"><input type="checkbox" id="cycleTog"/> Show cycle phase</label>
  <div id="status" class="muted" style="margin-top:8px"></div>
</div>

<div class="card" id="overlayCard" style="display:none">
  <h2>Compare two metrics</h2>
  <div style="margin-bottom:10px">
    <select id="ovA"></select>
    <span class="muted">vs</span>
    <select id="ovB"></select>
  </div>
  <div class="chartwrap"><canvas id="overlay"></canvas></div>
</div>

<div id="charts" class="grid"></div>

<script>
let DATA = null;
const $ = s => document.querySelector(s);
const PHASE_COLORS = { Menstrual:"#fdf0ef", Follicular:"#eef5f9", Ovulatory:"#eef7f1", Luteal:"#f4eff9", Unknown:"transparent" };
const charts = [];

$("#loadBtn").onclick = load;
$("#cycleTog").onchange = () => { if (DATA) renderAll(); };

async function load() {
  const pw = $("#pw").value;
  if (!pw) { $("#status").textContent = "Enter the password."; return; }
  $("#status").textContent = "Loading…";
  try {
    const res = await fetch("/chartdata", { headers:{ "x-app-password": pw } });
    if (!res.ok) { $("#status").textContent = "Error: " + (await res.text()); return; }
    DATA = await res.json();
    buildSeriesList();
    renderAll();
    $("#status").textContent = "Loaded.";
  } catch(e){ $("#status").textContent = "Failed: " + e; }
}

// Combine daily metrics + lab series into one named map of {label: [{date,value}]}
function allSeries() {
  const out = {};
  const dn = { pain:"Pain", sleepQuality:"Sleep quality", stress:"Stress", immuneActivation:"Immune activation" };
  for (const k in DATA.daily_series) if (DATA.daily_series[k].length) out[dn[k]||k] = DATA.daily_series[k];
  for (const lab in DATA.labs) if (DATA.labs[lab].points.length) out[lab] = DATA.labs[lab].points;
  return out;
}

function buildSeriesList() {
  const s = allSeries();
  const names = Object.keys(s);
  const fill = sel => { sel.innerHTML = names.map(n=>`<option>${n}</option>`).join(""); };
  fill($("#ovA")); fill($("#ovB"));
  if (names.length>1) $("#ovB").selectedIndex = 1;
  $("#overlayCard").style.display = names.length ? "block" : "none";
  $("#ovA").onchange = renderOverlay; $("#ovB").onchange = renderOverlay;
}

function phaseBands() {
  if (!$("#cycleTog").checked) return [];
  const pb = DATA.phase_by_date || {};
  return Object.entries(pb).map(([date,info]) => ({ date, color: PHASE_COLORS[info.phase]||"transparent" }));
}

function makeChart(canvas, datasets, withBands) {
  return new Chart(canvas, {
    type:"line",
    data:{ datasets },
    options:{
      parsing:false, maintainAspectRatio:false,
      scales:{ x:{ type:"time", time:{ unit:"day" } } },
      plugins:{ legend:{ labels:{ font:{ family:"Georgia" } } } }
    }
  });
}

function toXY(points){ return points.map(p=>({x:p.date, y:p.value})); }

function renderAll() {
  charts.forEach(c=>c.destroy()); charts.length=0;
  const wrap = $("#charts"); wrap.innerHTML="";
  const s = allSeries();
  for (const name in s) {
    const card = document.createElement("div"); card.className="card";
    card.innerHTML = `<h2>${name}</h2><div class="chartwrap"><canvas></canvas></div>`;
    wrap.appendChild(card);
    const ds = [{ label:name, data:toXY(s[name]), borderColor:"#5a3a1a", backgroundColor:"#5a3a1a", tension:0.2, spanGaps:true }];
    charts.push(makeChart(card.querySelector("canvas"), ds, true));
  }
  renderOverlay();
}

let overlayChart = null;
function renderOverlay() {
  if (!DATA) return;
  const s = allSeries();
  const a = $("#ovA").value, b = $("#ovB").value;
  if (overlayChart) overlayChart.destroy();
  const ds = [
    { label:a, data:toXY(s[a]||[]), borderColor:"#5a3a1a", backgroundColor:"#5a3a1a", yAxisID:"y", tension:0.2, spanGaps:true },
    { label:b, data:toXY(s[b]||[]), borderColor:"#5b8fa8", backgroundColor:"#5b8fa8", yAxisID:"y1", tension:0.2, spanGaps:true },
  ];
  overlayChart = new Chart($("#overlay"), {
    type:"line", data:{ datasets:ds },
    options:{ parsing:false, maintainAspectRatio:false,
      scales:{ x:{type:"time", time:{unit:"day"}},
        y:{ position:"left", title:{display:true,text:a} },
        y1:{ position:"right", title:{display:true,text:b}, grid:{drawOnChartArea:false} } },
      plugins:{ legend:{ labels:{ font:{ family:"Georgia" } } } } }
  });
}
</script>
</body></html>"""
