"""Pipeline dashboard — web UI for viewing runs and idea cards."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
RUN_DIR = PROJECT_ROOT / "ideaspark_run"


@router.get("/pipeline/dashboard", response_class=HTMLResponse)
async def pipeline_dashboard():
    """Legacy redirect to new UI."""
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/api/ui")


# ── Dashboard HTML ──────────────────────────────────────────────────────────

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>IdeaFlow — Pipeline Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box;}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0a0e17;color:#e0e6f0;padding:24px;}
h1{font-size:24px;font-weight:600;}
h1 small{font-size:13px;font-weight:400;color:#7480a0;margin-left:8px;}
.subtitle{color:#7480a0;margin-bottom:24px;font-size:13px;}

.run-card{background:#111827;border:1px solid #1e293b;border-radius:8px;padding:16px;margin-bottom:16px;transition:border-color 0.3s;}
.run-card:hover{border-color:#3b82f6;}
.run-card.complete{border-color:#22c55e;}
.run-card.active{border-color:#3b82f6;animation:pulse 2s infinite;}
@keyframes pulse{0%{border-color:#3b82f6}50%{border-color:#1e40af}100%{border-color:#3b82f6}}
.run-header{display:flex;justify-content:space-between;align-items:start;margin-bottom:12px;}
.run-query{font-size:15px;font-weight:500;flex:1;}
.run-id{font-size:11px;color:#7480a0;font-family:monospace;margin-top:2px;}
.run-progress{font-size:12px;padding:3px 8px;border-radius:4px;white-space:nowrap;}

.phase-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:4px;margin-top:8px;}
.phase-item{display:flex;align-items:center;gap:6px;font-size:11px;padding:3px 6px;border-radius:4px;}
.phase-complete{background:#064e3b;color:#6ee7b7;}
.phase-pending{background:#1e293b;color:#7480a0;}
.phase-running{background:#1e3a5f;color:#93c5fd;animation:pulse 2s infinite;}
.phase-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0;}
.dot-green{background:#22c55e;}
.dot-gray{background:#475569;}
.dot-blue{background:#3b82f6;animation:pulse 2s infinite;}

.actions{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap;}
.btn{font-size:12px;padding:5px 12px;border-radius:4px;border:none;cursor:pointer;text-decoration:none;display:inline-block;}
.btn-primary{background:#3b82f6;color:#fff;}
.btn-primary:hover{background:#2563eb;}
.btn-secondary{background:#1e293b;color:#e0e6f0;border:1px solid #334155;}
.btn-secondary:hover{background:#334155;}
.btn-success{background:#22c55e;color:#fff;}
.btn-warning{background:#f59e0b;color:#111;}
.btn-danger{background:#ef4444;color:#fff;}
.btn:disabled{opacity:0.5;cursor:not-allowed;}

#card-viewer{background:#111827;border:1px solid #1e293b;border-radius:8px;padding:24px;margin-top:24px;display:none;}
#card-viewer h2{font-size:18px;margin-bottom:16px;}
#card-viewer pre{background:#0a0e17;padding:16px;border-radius:4px;overflow-x:auto;font-size:13px;line-height:1.5;white-space:pre-wrap;max-height:600px;overflow-y:auto;}
#card-close{float:right;}

.stats-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:24px;}
.stat-card{background:#111827;border:1px solid #1e293b;border-radius:8px;padding:16px;text-align:center;}
.stat-value{font-size:28px;font-weight:700;color:#3b82f6;}
.stat-label{font-size:12px;color:#7480a0;margin-top:4px;}

.loading{text-align:center;padding:40px;color:#7480a0;}
</style>
</head>
<body>
<h1>IdeaFlow <small>Pipeline Dashboard</small></h1>
<p class="subtitle">ResearchStudio IdeaSpark on OpenRouter — auto-refreshing every 10s</p>

<div class="stats-row" id="stats">
  <div class="stat-card"><div class="stat-value" id="stat-runs">-</div><div class="stat-label">Total Runs</div></div>
  <div class="stat-card"><div class="stat-value" id="stat-cards">-</div><div class="stat-label">Completed Cards</div></div>
  <div class="stat-card"><div class="stat-value" id="stat-active">-</div><div class="stat-label">Active</div></div>
  <div class="stat-card"><div class="stat-value" id="stat-papers">-</div><div class="stat-label">Total Papers</div></div>
</div>

<div style="display:flex;gap:8px;margin-bottom:16px;">
  <button class="btn btn-primary" onclick="startNewRun()">+ New Run</button>
  <button class="btn btn-secondary" onclick="refresh()">Refresh</button>
  <span id="last-updated" style="font-size:11px;color:#7480a0;margin-left:8px;align-self:center;"></span>
</div>

<div id="new-run-form" style="display:none;background:#111827;border:1px solid #1e293b;border-radius:8px;padding:16px;margin-bottom:16px;">
  <input type="text" id="new-query" placeholder="Research query..." style="width:100%;padding:8px 12px;background:#0a0e17;border:1px solid #334155;border-radius:4px;color:#e0e6f0;font-size:14px;margin-bottom:8px;" />
  <div style="display:flex;gap:8px;">
    <button class="btn btn-primary" onclick="submitRun()">Start Pipeline</button>
    <button class="btn btn-secondary" onclick="document.getElementById('new-run-form').style.display='none'">Cancel</button>
  </div>
  <div id="run-progress" style="margin-top:8px;color:#7480a0;display:none;"></div>
</div>

<div id="runs-list">
  <div class="loading">Loading runs...</div>
</div>

<div id="card-viewer">
  <button class="btn btn-secondary" id="card-close" onclick="closeCard()">Close</button>
  <h2 id="card-title">Idea Card</h2>
  <div id="card-content"></div>
</div>

<script>
let runsCache = [];

async function refresh() {
  const res = await fetch('/api/pipeline/runs');
  runsCache = await res.json();
  render(runsCache);
  document.getElementById('last-updated').textContent = 'Updated: ' + new Date().toLocaleTimeString();
}

function render(runs) {
  const el = document.getElementById('runs-list');
  const totalRuns = runs.length;
  const completedCards = runs.filter(r => r.has_idea_card).length;
  let totalPapers = 0;
  let activeRuns = 0;

  if (runs.length === 0) {
    el.innerHTML = '<div style="text-align:center;padding:40px;color:#7480a0;">No runs yet. Start one!</div>';
    document.getElementById('stat-runs').textContent = '0';
    document.getElementById('stat-cards').textContent = '0';
    document.getElementById('stat-active').textContent = '0';
    document.getElementById('stat-papers').textContent = '0';
    return;
  }

  let html = '';
  for (const run of runs) {
    const phases = run.phases || {};
    const phaseKeys = ['phase0', 'phase0_fulltext', 'phase1', 'phase2_select', 'phase2_generate', 'phase2_coherence', 'phase3_collision', 'phase3_critique', 'phase3_revise', 'phase4_skeleton', 'phase4_fill', 'phase4_card'];
    const phaseLabels = {
      'phase0': 'P0 Lit', 'phase0_fulltext': 'P0+ FT', 'phase1': 'P1 Bottleneck',
      'phase2_select': 'P2.1 Sel', 'phase2_generate': 'P2.2 Gen', 'phase2_coherence': 'P2.3 Coh',
      'phase3_collision': 'P3.1 Col', 'phase3_critique': 'P3.2 Aud', 'phase3_revise': 'P3.3 Rev',
      'phase4_skeleton': 'P4 Skel', 'phase4_fill': 'P4 Fill', 'phase4_card': 'Card'
    };

    let completed = 0;
    let running = false;
    let phaseHtml = '';
    for (const key of phaseKeys) {
      const p = phases[key] || {status: 'pending'};
      if (p.status === 'complete') completed++;
      const isComplete = p.status === 'complete';
      const isPending = p.status === 'pending';
      const cls = isComplete ? 'phase-complete' : (isPending ? 'phase-pending' : 'phase-running');
      const dot = isComplete ? 'dot-green' : (isPending ? 'dot-gray' : 'dot-blue');
      if (!isComplete && !isPending) running = true;
      phaseHtml += `<div class="phase-item ${cls}"><span class="phase-dot ${dot}"></span>${phaseLabels[key] || key}</div>`;
    }

    if (run.phases && run.phases.phase0 && run.phases.phase0.status === 'complete') totalPapers++;

    const cardBtn = run.has_idea_card
      ? `<button class="btn btn-success" onclick="viewCard('${run.run_id}')">View Card</button>`
      : '';

    const cardClass = run.has_idea_card ? 'complete' : (running ? 'active' : '');
    if (running) activeRuns++;

    html += `
      <div class="run-card ${cardClass}">
        <div class="run-header">
          <div>
            <div class="run-query">${escapeHtml(run.query || '?')}</div>
            <div class="run-id">${run.run_id} — ${completed}/${phaseKeys.length} phases</div>
          </div>
        </div>
        <div class="phase-grid">${phaseHtml}</div>
        <div class="actions">
          ${cardBtn}
          <button class="btn btn-secondary" onclick="deleteRun('${run.run_id}')">Delete</button>
        </div>
      </div>`;
  }

  el.innerHTML = html;
  document.getElementById('stat-runs').textContent = totalRuns;
  document.getElementById('stat-cards').textContent = completedCards;
  document.getElementById('stat-active').textContent = activeRuns;
  document.getElementById('stat-papers').textContent = totalPapers;
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function startNewRun() {
  document.getElementById('new-run-form').style.display = 'block';
  document.getElementById('new-query').value = '';
  document.getElementById('run-progress').style.display = 'none';
}

async function submitRun() {
  const query = document.getElementById('new-query').value;
  if (!query) return;
  document.getElementById('run-progress').style.display = 'block';
  document.getElementById('run-progress').textContent = 'Starting pipeline (Phase 0 + 0+)...';
  try {
    const res = await fetch('/api/pipeline/start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({query})
    });
    const data = await res.json();
    document.getElementById('run-progress').textContent = 'Run started: ' + data.run_id;
    setTimeout(() => { document.getElementById('new-run-form').style.display = 'none'; refresh(); }, 2000);
  } catch (e) {
    document.getElementById('run-progress').textContent = 'Error: ' + e.message;
  }
}

async function viewCard(runId) {
  const res = await fetch('/api/results/' + runId + '/file?phase=phase4&filename=idea.std.en.md');
  const data = await res.json();
  document.getElementById('card-title').textContent = 'Idea Card \u2014 ' + runId;
  document.getElementById('card-content').innerHTML = '<pre>' + escapeHtml(data.content || 'Not found') + '</pre>';
  document.getElementById('card-viewer').style.display = 'block';
  document.getElementById('card-viewer').scrollIntoView({behavior: 'smooth'});
}

function closeCard() {
  document.getElementById('card-viewer').style.display = 'none';
}

async function deleteRun(runId) {
  if (!confirm('Delete run ' + runId + '?')) return;
  // Canonical REST delete; the POST /api/pipeline/delete-run alias is kept
  // server-side for older dashboard builds.
  const res = await fetch('/api/pipeline/runs/' + encodeURIComponent(runId), {method: 'DELETE'});
  if (!res.ok) {
    const legacy = await fetch('/api/pipeline/delete-run', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({run_id: runId})});
    if (!legacy.ok) { alert('Delete failed: ' + res.status); return; }
  }
  refresh();
}

refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>"""

@router.get("/pipeline/dashboard-v2", response_class=HTMLResponse)
async def pipeline_dashboard_v2():
    """Serve the pipeline dashboard UI (same as /pipeline/dashboard)."""
    return HTMLResponse(DASHBOARD_HTML)