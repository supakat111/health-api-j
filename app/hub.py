"""The unified health-data hub page (served at /app).

One page, one password entry, tabbed navigation across the features:
  - Surveillance: what's out of range / borderline / changed (opens here)
  - Charts: per-metric time series + compare-two overlay + cycle toggle
  - Upload: the bloodwork PDF extract/map/save flow

Each tab calls the same JSON endpoints the standalone pages use, so this is a
front-end shell over the existing API — no new server logic beyond what the
endpoints already provide.
"""

HUB_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Health data</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/luxon@3.4.4/build/global/luxon.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-luxon@1.3.1/dist/chartjs-adapter-luxon.umd.min.js"></script>
<style>
  body { font-family: Georgia, serif; background:#faf7f3; color:#3a2e28; max-width:960px; margin:0 auto; padding:0 0 40px; }
  header { background:#eef5f9; border-bottom:1px solid #dbe6ec; padding:16px; }
  h1 { font-size:19px; margin:0; }
  h2 { font-size:15px; margin:0 0 10px; }
  nav { display:flex; background:#f0ebe3; border-bottom:1px solid #e0d8d0; position:sticky; top:0; z-index:5; }
  nav button { flex:1; padding:13px 0; background:transparent; border:none; border-bottom:2px solid transparent;
    font-family:inherit; font-size:13px; font-weight:700; color:#9a8a7a; cursor:pointer; letter-spacing:.03em; }
  nav button.active { background:#fffdf9; color:#5a3a1a; border-bottom-color:#8a6a4a; }
  .wrap { padding:18px 16px; }
  .card { background:#fffdf9; border:1px solid #efe8e0; border-radius:14px; padding:16px; margin-bottom:14px; }
  input[type=password], input[type=text] { padding:9px 11px; border:1px solid #e0d8d0; border-radius:9px; font-size:14px; font-family:inherit; }
  button.act { padding:10px 16px; border:none; border-radius:10px; background:#5a3a1a; color:#fff; font-family:inherit; font-size:14px; font-weight:700; cursor:pointer; }
  button.sec { background:transparent; color:#5a3a1a; border:2px solid #8a6a4a; }
  select { padding:8px 10px; border:1px solid #e0d8d0; border-radius:9px; font-size:13px; font-family:inherit; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th,td { text-align:left; padding:7px 8px; border-bottom:1px solid #efe8e0; }
  td input { width:100%; box-sizing:border-box; padding:5px 7px; border:1px solid #e8e0d6; border-radius:6px; font-size:13px; font-family:inherit; }
  .muted { color:#9a8a7a; font-size:12px; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
  @media (max-width:640px){ .grid{ grid-template-columns:1fr; } }
  .chartwrap { position:relative; height:230px; }
  .pill { font-size:11px; font-weight:700; padding:2px 9px; border-radius:9px; }
  .p-high { background:#fdecea; color:#9a3b30; } .p-low { background:#eaf0fb; color:#2f4c86; }
  .p-bord { background:#fdf6e3; color:#8a6a1a; } .p-norm { background:#e7f1ea; color:#3a6a4a; }
  .p-unk { background:#eee; color:#888; }
  .counts { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:12px; }
  .countbox { background:#faf7f3; border-radius:12px; padding:10px 14px; text-align:center; min-width:74px; }
  .countbox .n { font-size:22px; font-weight:700; } .countbox .l { font-size:10px; color:#9a8a7a; text-transform:uppercase; letter-spacing:.04em; }
  label.tog { font-size:13px; display:inline-flex; align-items:center; gap:6px; }
  tr.oor td { background:#fdecea; } tr.review td { background:#fdf6e3; }
  .flag { font-size:10px; font-weight:700; padding:2px 6px; border-radius:6px; }
  .flag.oor { background:#f3c7c1; color:#9a3b30; } .flag.review { background:#ece0bf; color:#8a6a1a; }
  #pwbar { padding:12px 16px; background:#fff; border-bottom:1px solid #efe8e0; }
</style></head><body>
<header><h1>Health data</h1><div class="muted">Jennifer — labs, trends &amp; tracking</div></header>

<div id="pwbar">
  <label>Password <input type="password" id="pw" placeholder="app password"/></label>
  <button class="act" id="unlock">Unlock</button>
  <span id="pwmsg" class="muted"></span>
</div>

<nav id="nav" style="display:none">
  <button data-tab="surv" class="active">⚠ Needs attention</button>
  <button data-tab="charts">📈 Charts</button>
  <button data-tab="analysis">🔍 Analysis</button>
  <button data-tab="upload">⬆ Upload labs</button>
  <button data-tab="import">📥 Import old data</button>
</nav>

<div class="wrap" id="main" style="display:none">

  <!-- SURVEILLANCE -->
  <section data-panel="surv">
    <div class="card">
      <h2>Lab results — what needs attention</h2>
      <div class="muted" style="margin-bottom:10px">Most recent value per test. Borderline = within 10% of a reference limit. This is information to review with your doctor, not medical advice.</div>
      <div class="counts" id="survCounts"></div>
      <table id="survTbl"><thead><tr>
        <th>Test</th><th>Latest</th><th>Range</th><th>Status</th><th>Change</th>
      </tr></thead><tbody></tbody></table>
      <div id="survEmpty" class="muted" style="display:none">No lab data yet — upload a report first.</div>
    </div>
  </section>

  <!-- CHARTS -->
  <section data-panel="charts" style="display:none">
    <div class="card">
      <div style="display:flex; flex-wrap:wrap; gap:14px; align-items:center">
        <label class="tog"><input type="checkbox" id="cycleTog"/> Show cycle phase</label>
        <span style="border-left:1px solid #e0d8d0; height:20px"></span>
        <label class="tog">Range
          <select id="rangePreset">
            <option value="all">All time</option>
            <option value="30">Last 30 days</option>
            <option value="90">Last 90 days</option>
            <option value="180">Last 6 months</option>
            <option value="365">Last year</option>
            <option value="custom">Custom…</option>
          </select>
        </label>
        <span id="customRange" style="display:none">
          <input type="date" id="rangeFrom"/> <span class="muted">to</span> <input type="date" id="rangeTo"/>
        </span>
        <span class="muted" id="chartMsg"></span>
      </div>
    </div>
    <div class="card" id="overlayCard" style="display:none">
      <h2>Compare two metrics</h2>
      <div style="margin-bottom:10px"><select id="ovA"></select> <span class="muted">vs</span> <select id="ovB"></select></div>
      <div class="chartwrap"><canvas id="overlay"></canvas></div>
    </div>
    <div id="charts" class="grid"></div>
  </section>

  <!-- ANALYSIS -->
  <section data-panel="analysis" style="display:none">
    <div class="card">
      <h2>AI pattern analysis</h2>
      <div class="muted" style="margin-bottom:10px">
        This looks across the lab results and daily tracker data and surfaces possible patterns.
        It is <b>information to review with a doctor, not medical advice or a diagnosis</b>.
        Patterns from limited data can be coincidental — treat them as questions to ask, not conclusions.
      </div>
      <button class="act" id="analyzeBtn">Analyze my data</button>
      <span class="muted" id="analysisMsg" style="margin-left:10px"></span>
    </div>
    <div id="analysisOut" class="grid" style="grid-template-columns:1fr"></div>
  </section>

  <!-- UPLOAD -->
  <section data-panel="upload" style="display:none">
    <div class="card">
      <div class="muted" style="margin-bottom:8px">Choose a bloodwork PDF or photo, then Extract. Review the values, fix anything highlighted, map any new tests, then Save.</div>
      <input type="file" id="file" accept="application/pdf,image/*"/>
      <button class="act" id="extractBtn">Extract</button>
      <div id="status" class="muted" style="margin-top:8px"></div>
    </div>
    <div class="card" id="mapCard" style="display:none">
      <h2>New tests to map</h2>
      <div class="muted" style="margin-bottom:8px">AI suggests a match; confirm or change it. Remembered for next time.</div>
      <table id="mapTbl"><thead><tr><th>Lab printed</th><th>Map to</th><th>If new: name</th><th>Unit</th></tr></thead><tbody></tbody></table>
      <p><button class="act" id="applyMapBtn">Save mappings</button></p>
      <div id="mapStatus" class="muted"></div>
    </div>
    <div class="card" id="resultCard" style="display:none">
      <div style="display:flex; gap:16px; flex-wrap:wrap; margin-bottom:12px">
        <label>Report date <input type="text" id="reportDate" placeholder="YYYY-MM-DD"/></label>
        <label>Lab <input type="text" id="labName" placeholder="lab name"/></label>
      </div>
      <table id="tbl"><thead><tr><th>Test</th><th>Maps to</th><th>Value</th><th>Unit</th><th>Ref low</th><th>Ref high</th><th>Flags</th></tr></thead><tbody></tbody></table>
      <p><button class="act" id="saveBtn">Save to database</button></p>
      <div id="saveStatus" class="muted"></div>
    </div>
  </section>

  <!-- IMPORT -->
  <section data-panel="import" style="display:none">
    <div class="card">
      <h2>Import old tracker data</h2>
      <div class="muted" style="margin-bottom:10px">
        Upload the old Tally tracker spreadsheet (.xlsx). It maps pain, sleep, stress,
        immune activation, exercise and notes into daily entries. Dates that already
        exist are skipped — nothing gets overwritten. You'll see a preview before anything is saved.
      </div>
      <input type="file" id="importFile" accept=".xlsx"/>
      <button class="act" id="importPreviewBtn">Preview</button>
      <div id="importMsg" class="muted" style="margin-top:8px"></div>
    </div>
    <div class="card" id="importPreviewCard" style="display:none">
      <div id="importSummary" style="margin-bottom:12px"></div>
      <table id="importTbl"><thead><tr><th>Date</th><th>Pain</th><th>Sleep</th><th>Stress</th><th>Immune</th><th>Notes</th></tr></thead><tbody></tbody></table>
      <p style="margin-top:14px"><button class="act" id="importCommitBtn">Import these entries</button></p>
      <div id="importCommitMsg" class="muted"></div>
    </div>
  </section>

</div>

<script>
const $ = s => document.querySelector(s);
let PW = "";
function hdr(){ return { "x-app-password": PW }; }

$("#unlock").onclick = async () => {
  PW = $("#pw").value;
  if (!PW) { $("#pwmsg").textContent = "Enter the password."; return; }
  // validate with a cheap call
  const res = await fetch("/surveillance", { headers: hdr() });
  if (!res.ok) { $("#pwmsg").textContent = "Wrong password."; return; }
  $("#pwbar").style.display="none"; $("#nav").style.display="flex"; $("#main").style.display="block";
  loadSurv(await res.json());
};

// ---- tab switching ----
document.querySelectorAll("#nav button").forEach(b => b.onclick = () => {
  document.querySelectorAll("#nav button").forEach(x=>x.classList.remove("active"));
  b.classList.add("active");
  const tab = b.dataset.tab;
  document.querySelectorAll("[data-panel]").forEach(p => p.style.display = p.dataset.panel===tab ? "block":"none");
  if (tab==="charts" && !chartsLoaded) loadCharts();
});

// ================= ANALYSIS =================
$("#analyzeBtn").onclick = async () => {
  $("#analysisMsg").textContent = "Analyzing… (this takes a few seconds)";
  $("#analysisOut").innerHTML = "";
  try {
    const res = await fetch("/analyze", { headers: hdr() });
    if (!res.ok) { $("#analysisMsg").textContent = "Error: " + (await res.text()); return; }
    const data = await res.json();
    $("#analysisMsg").textContent = "";
    renderAnalysis(data);
  } catch(e){ $("#analysisMsg").textContent = "Failed: " + e; }
};

function renderAnalysis(data){
  const out = $("#analysisOut");
  if (data.note) { out.innerHTML = `<div class="card muted">${esc(data.note)}</div>`; }
  const framings = data.framings || {};
  const titles = {
    doctor: "① Patterns for your doctor",
    context: "② Patterns + plain-language context",
    interpretation: "③ Fuller interpretation"
  };
  const subtitles = {
    doctor: "Observations only, worded to bring to an appointment.",
    context: "Same observations, with brief lay explanation of terms.",
    interpretation: "Goes further toward what patterns might suggest — highest risk of over-reading."
  };
  ["doctor","context","interpretation"].forEach(key=>{
    if(!framings[key]) return;
    const card=document.createElement("div"); card.className="card";
    card.innerHTML = `<h2>${titles[key]}</h2><div class="muted" style="margin-bottom:10px">${subtitles[key]}</div>`+
      `<div style="white-space:pre-wrap; font-size:14px; line-height:1.6">${esc(framings[key])}</div>`+
      `<div style="margin-top:12px"><button class="sec pickBtn" data-k="${key}">This framing is best</button></div>`;
    out.appendChild(card);
  });
  out.querySelectorAll(".pickBtn").forEach(b=>b.onclick=()=>{
    $("#analysisMsg").textContent = "Noted — '"+titles[b.dataset.k]+"' preferred. Tell Claude to make it the default.";
  });
}

// ================= IMPORT =================
$("#importPreviewBtn").onclick = async () => {
  const f = $("#importFile").files[0];
  if(!f){ $("#importMsg").textContent="Choose the .xlsx file first."; return; }
  $("#importMsg").textContent="Reading…";
  const fd=new FormData(); fd.append("file",f);
  const res=await fetch("/import/preview",{method:"POST",headers:hdr(),body:fd});
  if(!res.ok){ $("#importMsg").textContent="Error: "+(await res.text()); return; }
  const p=await res.json();
  $("#importMsg").textContent="";
  $("#importSummary").innerHTML =
    `<b>${p.total}</b> entries found (${p.date_range[0]} → ${p.date_range[1]}). `+
    `<b>${p.new_count}</b> new to import, <b>${p.skip_count}</b> already exist (will skip).`;
  const tb=$("#importTbl tbody"); tb.innerHTML="";
  (p.sample||[]).forEach(e=>{
    const tr=document.createElement("tr");
    tr.innerHTML=`<td>${e.date}</td><td>${e.pain??""}</td><td>${e.sleepQuality??""}</td>`+
      `<td>${e.stress??""}</td><td>${e.immuneActivation??""}</td>`+
      `<td class="muted">${esc((e.notes||"").slice(0,60))}</td>`;
    tb.appendChild(tr);
  });
  if((p.sample||[]).length) {
    const note=document.createElement("tr");
    note.innerHTML=`<td colspan="6" class="muted">…showing first ${p.sample.length} of ${p.new_count} new entries</td>`;
    tb.appendChild(note);
  }
  $("#importPreviewCard").style.display = p.new_count ? "block":"none";
  if(!p.new_count) $("#importMsg").textContent="Nothing new to import — all those dates already have entries.";
};

$("#importCommitBtn").onclick = async () => {
  const f = $("#importFile").files[0];
  $("#importCommitMsg").textContent="Importing…";
  const fd=new FormData(); fd.append("file",f);
  const res=await fetch("/import/commit",{method:"POST",headers:hdr(),body:fd});
  if(!res.ok){ $("#importCommitMsg").textContent="Error: "+(await res.text()); return; }
  const r=await res.json();
  $("#importCommitMsg").textContent=`Imported ${r.written} entries (skipped ${r.skipped} existing). Check the Charts tab — pick "All time" to see them.`;
};

// ================= SURVEILLANCE =================
function loadSurv(data){
  const c = data.counts || {};
  $("#survCounts").innerHTML = [
    ["high","High"],["low","Low"],["borderline","Borderline"],["normal","Normal"],["unknown","No range"]
  ].map(([k,l])=>`<div class="countbox"><div class="n">${c[k]||0}</div><div class="l">${l}</div></div>`).join("");
  const tb = $("#survTbl tbody"); tb.innerHTML="";
  const ms = data.markers || [];
  $("#survEmpty").style.display = ms.length ? "none":"block";
  $("#survTbl").style.display = ms.length ? "table":"none";
  ms.forEach(m=>{
    const pill = statusPill(m.status);
    const range = (m.ref_low!=null||m.ref_high!=null) ? `${m.ref_low??""}–${m.ref_high??""} ${m.unit||""}` : "—";
    let change = "—";
    if (m.delta!=null){ const arrow = m.direction==="up"?"↑":(m.direction==="down"?"↓":"→");
      change = `${arrow} ${m.delta>0?"+":""}${m.delta} <span class="muted">(from ${m.prev_value})</span>`; }
    const tr=document.createElement("tr");
    tr.innerHTML=`<td><b>${esc(m.name)}</b></td><td>${m.latest_value} ${esc(m.unit||"")}<br><span class="muted">${m.latest_date||""}</span></td>`+
      `<td class="muted">${esc(range)}</td><td>${pill}</td><td>${change}</td>`;
    tb.appendChild(tr);
  });
}
function statusPill(s){
  const map={ high:["p-high","High"], low:["p-low","Low"], borderline_high:["p-bord","Borderline high"],
    borderline_low:["p-bord","Borderline low"], normal:["p-norm","Normal"], unknown:["p-unk","No range"] };
  const [cls,lab]=map[s]||map.unknown; return `<span class="pill ${cls}">${lab}</span>`;
}

// ================= CHARTS =================
let DATA=null, chartsLoaded=false; const charts=[]; let overlayChart=null;
const PHASE_COLORS={Menstrual:"#fdf0ef",Follicular:"#eef5f9",Ovulatory:"#eef7f1",Luteal:"#f4eff9",Unknown:"transparent"};
$("#cycleTog").onchange=()=>{ if(DATA) renderAll(); };
$("#rangePreset").onchange=()=>{
  $("#customRange").style.display = $("#rangePreset").value==="custom" ? "inline":"none";
  if(DATA) renderAll();
};
$("#rangeFrom").onchange=()=>{ if(DATA) renderAll(); };
$("#rangeTo").onchange=()=>{ if(DATA) renderAll(); };

function activeRange(){
  const preset=$("#rangePreset").value;
  if(preset==="all") return {from:null,to:null};
  if(preset==="custom") return {from:$("#rangeFrom").value||null, to:$("#rangeTo").value||null};
  const days=parseInt(preset,10);
  const to=new Date();
  const from=new Date(Date.now()-days*864e5);
  return {from:from.toISOString().slice(0,10), to:to.toISOString().slice(0,10)};
}
function inRange(date,r){
  if(!date) return false;
  if(r.from && date<r.from) return false;
  if(r.to && date>r.to) return false;
  return true;
}
function filterPts(points){ const r=activeRange(); return points.filter(p=>inRange(p.date,r)); }

async function loadCharts(){
  $("#chartMsg").textContent="Loading…";
  const res=await fetch("/chartdata",{headers:hdr()});
  if(!res.ok){ $("#chartMsg").textContent="Error: "+(await res.text()); return; }
  DATA=await res.json(); chartsLoaded=true; buildSeriesList(); renderAll(); $("#chartMsg").textContent="";
}
function allSeries(){
  const out={}; const dn={pain:"Pain",sleepQuality:"Sleep quality",stress:"Stress",immuneActivation:"Immune activation"};
  for(const k in DATA.daily_series) if(DATA.daily_series[k].length) out[dn[k]||k]=filterPts(DATA.daily_series[k]);
  for(const lab in DATA.labs) if(DATA.labs[lab].points.length) out[lab]=filterPts(DATA.labs[lab].points);
  return out;
}
function buildSeriesList(){
  const names=Object.keys(allSeries());
  const fill=sel=>sel.innerHTML=names.map(n=>`<option>${n}</option>`).join("");
  fill($("#ovA")); fill($("#ovB")); if(names.length>1) $("#ovB").selectedIndex=1;
  $("#overlayCard").style.display=names.length?"block":"none";
  $("#ovA").onchange=renderOverlay; $("#ovB").onchange=renderOverlay;
}
function toXY(p){ return p.map(x=>({x:x.date,y:x.value})); }
function renderAll(){
  charts.forEach(c=>c.destroy()); charts.length=0; const wrap=$("#charts"); wrap.innerHTML="";
  const s=allSeries();
  for(const name in s){
    const card=document.createElement("div"); card.className="card";
    card.innerHTML=`<h2>${esc(name)}</h2><div class="chartwrap"><canvas></canvas></div>`; wrap.appendChild(card);
    charts.push(new Chart(card.querySelector("canvas"),{type:"line",
      data:{datasets:[{label:name,data:toXY(s[name]),borderColor:"#5a3a1a",backgroundColor:"#5a3a1a",tension:.2,spanGaps:true}]},
      options:{parsing:false,maintainAspectRatio:false,scales:{x:{type:"time",time:{unit:"day"}}},plugins:{legend:{labels:{font:{family:"Georgia"}}}}}}));
  }
  renderOverlay();
}
function renderOverlay(){
  if(!DATA) return; const s=allSeries(); const a=$("#ovA").value,b=$("#ovB").value;
  if(overlayChart) overlayChart.destroy();
  overlayChart=new Chart($("#overlay"),{type:"line",data:{datasets:[
    {label:a,data:toXY(s[a]||[]),borderColor:"#5a3a1a",backgroundColor:"#5a3a1a",yAxisID:"y",tension:.2,spanGaps:true},
    {label:b,data:toXY(s[b]||[]),borderColor:"#5b8fa8",backgroundColor:"#5b8fa8",yAxisID:"y1",tension:.2,spanGaps:true}]},
    options:{parsing:false,maintainAspectRatio:false,scales:{x:{type:"time",time:{unit:"day"}},
      y:{position:"left",title:{display:true,text:a}},y1:{position:"right",title:{display:true,text:b},grid:{drawOnChartArea:false}}},
      plugins:{legend:{labels:{font:{family:"Georgia"}}}}}});
}

// ================= UPLOAD =================
let lastResults=[],canonicalTests=[],unmapped=[],suggestions={},lastFile=null;
$("#extractBtn").onclick=async()=>{
  const f=$("#file").files[0]; if(!f){ $("#status").textContent="Choose a file."; return; }
  lastFile=f; $("#status").textContent="Extracting…";
  const fd=new FormData(); fd.append("file",f);
  const res=await fetch("/extract",{method:"POST",headers:hdr(),body:fd});
  if(!res.ok){ $("#status").textContent="Error: "+(await res.text()); return; }
  const data=await res.json();
  lastResults=data.results||[]; canonicalTests=data.canonical_tests||[]; unmapped=data.unmapped||[]; suggestions=data.suggestions||{};
  $("#reportDate").value=data.report_date||""; $("#labName").value=data.lab_name||"";
  renderMapPanel(); renderTable(); $("#resultCard").style.display="block";
  $("#status").textContent="Found "+lastResults.length+" values."+(unmapped.length?(" "+unmapped.length+" new to map."):"");
};
function canonOptions(sel){ let o='<option value="">— map to —</option>';
  canonicalTests.forEach(c=>o+=`<option value="${c.id}" ${sel===c.id?"selected":""}>${esc(c.display_name)}</option>`);
  o+=`<option value="__new__" ${sel==='__new__'?"selected":""}>+ It's new</option>`; return o; }
function renderMapPanel(){
  if(!unmapped.length){ $("#mapCard").style.display="none"; return; } $("#mapCard").style.display="block";
  const tb=$("#mapTbl tbody"); tb.innerHTML="";
  unmapped.forEach((name,i)=>{ const sug=suggestions[name]||{};
    let pre=""; if(sug.canonical_id&&sug.confidence==="high") pre=sug.canonical_id; else if(sug.is_new) pre="__new__";
    const conf=sug.canonical_id?`AI: ${esc(sug.canonical_name)} (${sug.confidence})`:(sug.is_new?"AI: looks new":"AI: unsure");
    const dis=pre==="__new__"?"":"disabled";
    const tr=document.createElement("tr");
    tr.innerHTML=`<td>${esc(name)}<br><span class="muted">${conf}</span></td>`+
      `<td><select data-i="${i}" class="mapSel">${canonOptions(pre)}</select></td>`+
      `<td><input data-i="${i}" class="mapNew" value="${esc(name)}" ${dis}/></td>`+
      `<td><input data-i="${i}" class="mapUnit" value="${esc(sug.suggested_unit||"")}" ${dis}/></td>`;
    tb.appendChild(tr); });
  tb.querySelectorAll(".mapSel").forEach(sel=>sel.onchange=()=>{ const i=sel.dataset.i,n=sel.value==="__new__";
    tb.querySelector(`.mapNew[data-i="${i}"]`).disabled=!n; tb.querySelector(`.mapUnit[data-i="${i}"]`).disabled=!n; });
}
$("#applyMapBtn").onclick=async()=>{
  const tb=$("#mapTbl tbody"); const rows=[...tb.querySelectorAll("tr")]; $("#mapStatus").textContent="Saving…";
  for(let i=0;i<rows.length;i++){ const sel=tb.querySelector(`.mapSel[data-i="${i}"]`); const raw=unmapped[i]; if(!sel.value) continue;
    let payload; if(sel.value==="__new__"){ const clean=tb.querySelector(`.mapNew[data-i="${i}"]`).value.trim()||raw;
      const unit=tb.querySelector(`.mapUnit[data-i="${i}"]`).value.trim()||null;
      payload=JSON.stringify({display_name:clean,canonical_unit:unit,aliases:[raw]});
    } else payload=JSON.stringify({canonical_id:sel.value,aliases:[raw]});
    const fd=new FormData(); fd.append("payload",payload);
    const res=await fetch("/canonical",{method:"POST",headers:hdr(),body:fd});
    if(!res.ok){ $("#mapStatus").textContent="Error on '"+raw+"': "+(await res.text()); return; } }
  $("#mapStatus").textContent="Mappings saved. Refreshing…";
  const fd=new FormData(); fd.append("file",lastFile);
  const res=await fetch("/extract",{method:"POST",headers:hdr(),body:fd}); const data=await res.json();
  lastResults=data.results||[]; canonicalTests=data.canonical_tests||[]; unmapped=data.unmapped||[]; suggestions=data.suggestions||{};
  renderMapPanel(); renderTable(); $("#mapStatus").textContent=unmapped.length?(unmapped.length+" still unmapped."):"All mapped.";
};
function renderTable(){
  const tb=$("#tbl tbody"); tb.innerHTML="";
  lastResults.forEach((r,i)=>{ const tr=document.createElement("tr");
    if(r.out_of_range) tr.className="oor"; else if(r.needs_review) tr.className="review";
    tr.innerHTML=`<td><input value="${esc(r.test_name||"")}" data-i="${i}" data-k="test_name"/></td>`+
      `<td class="muted">${r.canonical_name?esc(r.canonical_name):'<span style="color:#c4934a">unmapped</span>'}</td>`+
      `<td><input value="${esc(r.value_text!=null?r.value_text:(r.value_num!=null?r.value_num:""))}" data-i="${i}" data-k="value_text"/></td>`+
      `<td><input value="${esc(r.unit||"")}" data-i="${i}" data-k="unit"/></td>`+
      `<td><input value="${r.ref_low!=null?r.ref_low:""}" data-i="${i}" data-k="ref_low"/></td>`+
      `<td><input value="${r.ref_high!=null?r.ref_high:""}" data-i="${i}" data-k="ref_high"/></td>`+
      `<td>${r.out_of_range?'<span class="flag oor">out of range</span>':''}${r.needs_review?' <span class="flag review">review</span>':''}${r.unit_flag==='unit_mismatch'?' <span class="flag review">unit?</span>':''}</td>`;
    tb.appendChild(tr); });
  tb.querySelectorAll("input").forEach(inp=>inp.oninput=()=>{ lastResults[+inp.dataset.i][inp.dataset.k]=inp.value; });
}
$("#saveBtn").onclick=async()=>{
  const f=$("#file").files[0];
  const results=lastResults.map(r=>({...r,
    value_num:isFinite(parseFloat(r.value_text))?parseFloat(r.value_text):null,
    ref_low:r.ref_low===""||r.ref_low==null?null:parseFloat(r.ref_low),
    ref_high:r.ref_high===""||r.ref_high==null?null:parseFloat(r.ref_high)}));
  const payload=JSON.stringify({report_date:$("#reportDate").value||null,lab_name:$("#labName").value||null,results});
  const fd=new FormData(); fd.append("file",f); fd.append("payload",payload);
  $("#saveStatus").textContent="Saving…";
  const res=await fetch("/save",{method:"POST",headers:hdr(),body:fd});
  if(!res.ok){ $("#saveStatus").textContent="Error: "+(await res.text()); return; }
  const out=await res.json(); $("#saveStatus").textContent="Saved "+out.count+" values. Check 'Needs attention' tab.";
};

function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/"/g,"&quot;").replace(/</g,"&lt;"); }
</script>
</body></html>"""
