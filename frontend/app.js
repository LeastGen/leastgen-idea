// ════════════════════════════════════════════════════════════════════════════

// ════════════════════════════════════════════════════════════════════════════
//  Landing Page Logic
// ════════════════════════════════════════════════════════════════════════════

function scrollToTop() {
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function toggleMobileNav() {
  document.getElementById('nav-links').classList.toggle('open');
}

// ════════════════════════════════════════════════════════════════════════════
//  API
// ════════════════════════════════════════════════════════════════════════════

const API = (() => {
  const base = window.location.pathname.match(/^\/d\/(\d+)\//)
    ? `${window.location.protocol}//${window.location.hostname}:${RegExp.$1}`
    : '';
  const h = (p, o) => fetch(base + p, { ...o, credentials: 'include' }).then(r => {
    if (r.status === 401) { handleAuthError(); throw Error('Unauthorized'); }
    if (!r.ok) return r.json().then(e => { throw Error(e.detail || e.error || r.statusText); });
    const ct = r.headers.get('content-type') || '';
    if (ct.includes('json')) return r.json();
    return r.text();
  });
  return {
    login: (e, p) => h('/api/auth/login', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email:e, password:p}) }),
    signup: (n, e, p) => h('/api/auth/signup', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:n, email:e, password:p}) }),
    me: () => h('/api/auth/me'),
    logout: () => h('/api/auth/logout', { method:'POST' }),
    limits: () => h('/api/auth/limits'),
    runs: () => h('/api/pipeline/runs'),
    run: (id) => h(`/api/pipeline/runs/${id}`),
    startPipeline: (q) => h('/api/pipeline/start', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({query:q}) }),
    resume: (id) => h(`/api/pipeline/runs/${id}/resume`, { method:'POST' }),
    runPhase: (id, phase) => h(`/api/pipeline/runs/${id}/phase/${phase}`, { method:'POST' }),
    artifacts: (id) => h(`/api/pipeline/runs/${id}/artifacts`),
    artifactContent: (id, pdir, fn) => h(`/api/pipeline/runs/${id}/artifacts/${pdir}/${fn}`),
    scoopStart: (p, n) => h('/api/scoop-check/start', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({problem:p, novelty:n}) }),
    scoopStatus: (id) => h(`/api/scoop-check/${id}`),
    plans: () => h('/api/billing/plans'),
    createCheckout: (planId) => h('/api/billing/create-checkout', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({plan_id:planId}) }),
    subscription: () => h('/api/billing/subscription'),
  };
})();

// ════════════════════════════════════════════════════════════════════════════
//  Checkout Redirect Handler
// ════════════════════════════════════════════════════════════════════════════

function handleCheckoutRedirect() {
  const params = new URLSearchParams(window.location.search);
  const checkout = params.get('checkout');
  if (!checkout) return;

  if (checkout === 'success') {
    refreshSubscriptionStatus();
    alert('Subscription activated successfully!');
  } else if (checkout === 'canceled') {
    alert('Checkout was canceled. No changes were made.');
  }

  // Clean URL
  window.history.replaceState({}, document.title, window.location.pathname);
}

// ════════════════════════════════════════════════════════════════════════════
//  Auth
// ════════════════════════════════════════════════════════════════════════════

let currentUser = null;
let authChecked = false;

async function checkAuth() {
  try {
    currentUser = await API.me();
    authChecked = true;
    // Show app, hide landing
    document.getElementById('landing').classList.add('hidden');
    document.getElementById('app').hidden = false;
    document.getElementById('auth-overlay').classList.add('hidden');
    document.getElementById('user-menu').hidden = false;
    document.getElementById('user-name-display').textContent = currentUser.name || currentUser.email.split('@')[0];
    document.getElementById('user-email-display').textContent = currentUser.email;
    document.getElementById('user-runs-display').textContent = `Runs: ${currentUser.runs_used}/${currentUser.runs_limit}`;
    refreshSubscriptionStatus();
    initApp();
  } catch (e) {
    authChecked = false;
    // Show landing, hide app
    document.getElementById('landing').classList.remove('hidden');
    document.getElementById('app').hidden = true;
    document.getElementById('auth-overlay').classList.add('hidden');
  }
}

function handleAuthError() {
  currentUser = null;
  authChecked = false;
  document.getElementById('landing').classList.remove('hidden');
  document.getElementById('app').hidden = true;
  document.getElementById('auth-overlay').classList.add('hidden');
  document.getElementById('user-menu').hidden = true;
}

function showAuth(form) {
  document.getElementById('landing').classList.add('hidden');
  document.getElementById('auth-overlay').classList.remove('hidden');
  showAuthForm(form);
}

function showAuthForm(form) {
  document.getElementById('login-form').style.display = form === 'login' ? '' : 'none';
  document.getElementById('signup-form').style.display = form === 'signup' ? '' : 'none';
  document.getElementById('login-error').textContent = '';
  document.getElementById('signup-error').textContent = '';
}

async function doLogin(e) {
  e.preventDefault();
  const btn = document.getElementById('login-btn');
  btn.disabled = true;
  btn.textContent = "Sign In" + '...';
  document.getElementById('login-error').textContent = '';
  try {
    const result = await API.login(
      document.getElementById('login-email').value,
      document.getElementById('login-password').value
    );
    currentUser = result.user;
    document.getElementById('landing').classList.add('hidden');
    document.getElementById('app').hidden = false;
    document.getElementById('auth-overlay').classList.add('hidden');
    document.getElementById('user-menu').hidden = false;
    document.getElementById('user-name-display').textContent = currentUser.name || currentUser.email.split('@')[0];
    document.getElementById('user-email-display').textContent = currentUser.email;
    document.getElementById('user-runs-display').textContent = `Runs: ${currentUser.runs_used}/${currentUser.runs_limit}`;
    refreshSubscriptionStatus();
    initApp();
  } catch (err) {
    document.getElementById('login-error').textContent = err.message || 'Login failed';
  }
  btn.disabled = false;
  btn.textContent = "Sign In";
}

async function doSignup(e) {
  e.preventDefault();
  const btn = document.getElementById('signup-btn');
  btn.disabled = true;
  btn.textContent = "Create Account" + '...';
  document.getElementById('signup-error').textContent = '';
  try {
    const result = await API.signup(
      document.getElementById('signup-name').value,
      document.getElementById('signup-email').value,
      document.getElementById('signup-password').value
    );
    currentUser = result.user;
    document.getElementById('landing').classList.add('hidden');
    document.getElementById('app').hidden = false;
    document.getElementById('auth-overlay').classList.add('hidden');
    document.getElementById('user-menu').hidden = false;
    document.getElementById('user-name-display').textContent = currentUser.name || currentUser.email.split('@')[0];
    document.getElementById('user-email-display').textContent = currentUser.email;
    document.getElementById('user-runs-display').textContent = `Runs: ${currentUser.runs_used}/${currentUser.runs_limit}`;
    refreshSubscriptionStatus();
    initApp();
  } catch (err) {
    document.getElementById('signup-error').textContent = err.message || 'Signup failed';
  }
  btn.disabled = false;
  btn.textContent = "Create Account";
}

async function doLogout() {
  try { await API.logout(); } catch (e) {}
  currentUser = null;
  const subEl = document.getElementById('user-subscription-display');
  if (subEl) subEl.textContent = '';
  document.getElementById('landing').classList.remove('hidden');
  document.getElementById('app').hidden = true;
  document.getElementById('auth-overlay').classList.add('hidden');
  document.getElementById('user-menu').hidden = true;
}

function toggleUserMenu() {
  const dd = document.getElementById('user-dropdown');
  dd.hidden = !dd.hidden;
  if (!dd.hidden) refreshSubscriptionStatus();
}

async function refreshSubscriptionStatus() {
  const el = document.getElementById('user-subscription-display');
  if (!el) return;
  try {
    const data = await API.subscription();
    const tier = data.tier || 'free';
    const status = data.status || 'unknown';
    const tierLabel = tier === 'pro_monthly' ? 'Pro Monthly' : tier === 'pro_yearly' ? 'Pro Yearly' : 'Free';
    el.textContent = `Plan: ${tierLabel} (${status})`;
    el.style.color = tier === 'free' ? 'var(--text3)' : 'var(--accent)';
  } catch (e) {
    el.textContent = 'Plan: Unknown';
  }
}

document.addEventListener('click', (e) => {
  const dd = document.getElementById('user-dropdown');
  if (dd && !dd.hidden && !e.target.closest('.user-menu')) {
    dd.hidden = true;
  }
});

// ════════════════════════════════════════════════════════════════════════════
//  Billing
// ════════════════════════════════════════════════════════════════════════════

async function showBilling() {
  document.getElementById('user-dropdown').hidden = true;
  document.getElementById('billing-modal').style.display = 'flex';
  const el = document.getElementById('billing-plans');
  el.innerHTML = '<div class="billing-loading">' + "Loading plans..." + '</div>';
  try {
    const data = await API.plans();
    const plans = data.plans || [];
    el.innerHTML = plans.map(p => {
      const isPro = p.id.startsWith('pro');
      const isFree = p.id === 'free';
      return `<div class="billing-plan ${isPro ? 'featured' : ''}">
        <div class="billing-plan-name">${p.name}</div>
        <div class="billing-plan-price">${p.price === 0 ? "Free" : '$' + p.price}<span>/${p.interval === 'month' ? 'mo' : 'yr'}</span></div>
        <ul class="billing-plan-features">${(p.features || []).map(f => `<li>${f}</li>`).join('')}</ul>
        ${isFree ? '<div style="font-size:12px;color:var(--text3);text-align:center;">' + "Free" + '</div>'
          : `<button class="btn btn-primary billing-btn" onclick="upgrade('${p.id}')">${"Subscribe"}</button>`}
      </div>`;
    }).join('');
  } catch (e) {
    el.innerHTML = '<div class="billing-loading">Failed to load plans</div>';
  }
}

function hideBilling() {
  document.getElementById('billing-modal').style.display = 'none';
}

async function upgrade(planId) {
  if (planId === 'free') { alert('Already on free plan'); return; }
  try {
    const result = await API.createCheckout(planId);
    if (result.url) window.location.href = result.url;
  } catch (e) {
    alert('Checkout failed: ' + e.message);
  }
}

// ════════════════════════════════════════════════════════════════════════════
//  Export
// ════════════════════════════════════════════════════════════════════════════

async function exportCard(format) {
  const run = getSelectedRun();
  if (!run || !run.has_card) return;
  const url = `/api/export/${run.run_id}/${format}`;
  window.open(url, '_blank');
}

// ════════════════════════════════════════════════════════════════════════════
//  Pipeline
// ════════════════════════════════════════════════════════════════════════════

const PHASES = [
  {id:'phase0', num:1, label:'Literature Search', short:'Lit Search', desc:'Search arXiv, OpenAlex, Semantic Scholar', type:'auto', dir:'phase0'},
  {id:'phase0_fulltext', num:2, label:'Full-Text Fetch', short:'Full-Text', desc:'Download full texts of top papers', type:'auto', dir:'phase0'},
  {id:'phase1', num:3, label:'Bottleneck ID', short:'Bottleneck', desc:'Identify the research bottleneck via LLM', type:'llm', dir:'phase1'},
  {id:'phase2_select', num:4, label:'Gap × Pattern', short:'Gap × Pattern', desc:'Select gaps and ideation patterns', type:'llm', dir:'phase2_select'},
  {id:'phase2_generate', num:5, label:'Candidate Generation', short:'Candidate', desc:'Generate concrete idea candidate', type:'llm', dir:'phase2_generate'},
  {id:'phase2_coherence', num:6, label:'Coherence Trace', short:'Coherence', desc:'Dry-run the algorithm to catch bugs', type:'llm', dir:'phase2_coherence'},
  {id:'phase3_collision', num:7, label:'Collision Check', short:'Collision', desc:'BM25 prior-art search & collision hits', type:'auto', dir:'phase3_collision'},
  {id:'phase3_critique', num:8, label:'5-Check Audit', short:'Audit', desc:'5-check audit of candidate novelty', type:'llm', dir:'phase3_critique'},
  {id:'phase3_revise', num:9, label:'Revision', short:'Revise', desc:'Apply audit fixes or advance', type:'llm', dir:'phase3_revise'},
  {id:'phase4_skeleton', num:10, label:'Skeleton', short:'Skeleton', desc:'Build structured expansion outline', type:'auto', dir:'phase4'},
  {id:'phase4_fill', num:11, label:'Prose Fill', short:'Prose Fill', desc:'Author 30-40 prose fields via LLM', type:'llm', dir:'phase4'},
  {id:'phase4_card', num:12, label:'Idea Card', short:'Idea Card', desc:'Render final card (Markdown, LaTeX)', type:'auto', dir:'phase4'},
];
const PKEYS = PHASES.map(p => p.id);

function formatDuration(sec) {
  if (sec === undefined || sec === null || isNaN(sec) || sec <= 0) return '';
  const s = Math.round(sec);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem > 0 ? `${m}m ${rem}s` : `${m}m`;
}

function formatBytes(bytes) {
  if (!bytes || isNaN(bytes)) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

const PHASE_FILES = {
  phase0: { file:'phase0/lit_results.json', checkFile:true },
  phase0_fulltext: { file:'phase0/fulltext_cache.json', checkFile:true },
  phase1: { file:'phase1/phase1_output.json', checkFile:true },
  phase2_select: { file:'phase2_select/phase2_select_output.json', checkFile:true },
  phase2_generate: { file:'phase2_generate/phase2_generate_output.json', checkFile:true },
  phase2_coherence: { file:'phase2_coherence/phase2_coherence_output.json', checkFile:true },
  phase3_collision: { file:'phase3_collision/collision_hits.json', checkFile:true },
  phase3_critique: { file:'phase3_critique/phase3_critique_output.json', checkFile:true },
  phase3_revise: { file:'phase3_revise/phase3_revise_output.json', checkFile:true },
  phase4_skeleton: { file:'phase4/phase4_skeleton.json', checkFile:true },
  phase4_fill: { file:'phase4/fill_map.json', checkFile:true },
  phase4_card: { file:'phase4/idea.std.en.md', checkFile:true },
};

let runs = [];
let selectedRun = null;
let currentMode = 'scoop';
let pollTimer = null;
let scoopTimer = null;

let liveTickTimer = null;
function startLiveTimerTick() {
  if (liveTickTimer) return;
  liveTickTimer = setInterval(() => {
    const run = getSelectedRun();
    if (!run) return;
    const phases = run.phases || {};
    const activeKey = PKEYS.find(k => phases[k]?.status === 'running');
    if (!activeKey) return;

    run.total_duration_sec = (run.total_duration_sec || 0) + 1;
    const pData = phases[activeKey];
    if (pData) {
      pData.elapsed_seconds = (pData.elapsed_seconds || 0) + 1;
      const stepTimeEl = document.getElementById(`step-time-${activeKey}`);
      if (stepTimeEl) stepTimeEl.textContent = formatDuration(pData.elapsed_seconds);
      const phaseCardTimer = document.querySelector(`#phase-card-${activeKey} .phase-timer`);
      if (phaseCardTimer) phaseCardTimer.textContent = `⏱️ ${formatDuration(pData.elapsed_seconds)}`;
    }
    const totalTimeEl = document.getElementById('stepper-total-time-val');
    if (totalTimeEl) totalTimeEl.textContent = formatDuration(run.total_duration_sec) || '0s';
  }, 1000);
}

function initApp() {
  loadRuns();
  startLiveTimerTick();
  document.getElementById('scoop-problem').focus();
  document.addEventListener('keydown', e => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      if (currentMode === 'scoop') runScoop();
      else runPipeline();
    }
    if (e.key === 'Enter' && currentMode === 'idea' &&
        document.activeElement === document.getElementById('idea-query')) {
      runPipeline();
    }
  });
}

document.addEventListener('DOMContentLoaded', () => {
  handleCheckoutRedirect();
  checkAuth();
});

function switchMode(mode) {
  currentMode = mode;
  document.querySelectorAll('.mode-panel').forEach(el => el.classList.toggle('active', el.id === `panel-${mode}`));
  document.querySelectorAll('.mode-btn').forEach(el => el.classList.toggle('active', el.dataset.mode === mode));
  document.getElementById(mode === 'scoop' ? 'scoop-problem' : 'idea-query').focus();
}

async function loadRuns() {
  try {
    const r = await API.runs();
    runs = r || [];
    if (!selectedRun && runs.length) selectedRun = runs[0].run_id;
  } catch (e) { console.warn('loadRuns:', e); }
  render();
  setTimeout(autoChain, 500);
}

function render() {
  renderStats();
  renderRunTabs();
  renderPhases();
  renderHistory();
  renderIdeaCard();
  renderStepper();
  const hasActive = runs.some(r => {
    const phases = r.phases || {};
    return !phases.phase4_card || phases.phase4_card.status !== 'complete';
  });
  document.getElementById('pipeline-container').hidden = !runs.length;
  document.getElementById('empty-state').hidden = runs.length > 0;
  document.getElementById('running-bar').hidden = !hasActive;
}

function renderStats() {
  const total = runs.length;
  const done = runs.filter(r => r.has_card).length;
  const active = runs.filter(r => {
    const p = r.phases || {};
    return p.phase0 && p.phase0.status === 'complete' && !p.phase4_card;
  }).length;
  let papers = 0;
  runs.forEach(r => {
    const p0 = (r.phases || {}).phase0;
    if (p0 && p0.size) papers += Math.round(p0.size / 2000);
  });
  document.getElementById('stats').innerHTML = `
    <div class="stat-card"><div class="stat-val">${total}</div><div class="stat-lbl">Runs</div></div>
    <div class="stat-card"><div class="stat-val">${done}</div><div class="stat-lbl">Complete</div></div>
    <div class="stat-card"><div class="stat-val">${active}</div><div class="stat-lbl">Active</div></div>
    <div class="stat-card"><div class="stat-val">${papers}</div><div class="stat-lbl">Papers</div></div>
  `;
}

function renderRunTabs() {
  const el = document.getElementById('run-tabs');
  const hasCard = id => { const r = runs.find(x => x.run_id === id); return r && r.has_card; };
  el.innerHTML = runs.map(r =>
    `<button class="run-tab${r.run_id === selectedRun ? ' active' : ''}"
             onclick="selectRun('${r.run_id}')" title="${r.query?.slice(0,80)}">
      ${r.query?.slice(0,30) || r.run_id.slice(0,20)}${hasCard(r.run_id) ? '<span class="check">✓</span>' : ''}
    </button>`
  ).join('');
}

function selectRun(id) { selectedRun = id; renderRunTabs(); renderPhases(); renderStepper(); renderIdeaCard(); }
function getSelectedRun() { return runs.find(r => r.run_id === selectedRun); }

function renderStepper() {
  const run = getSelectedRun();
  const card = document.getElementById('stepper-card');
  if (!run) { if (card) card.hidden = true; return; }
  if (card) card.hidden = false;

  const phases = run.phases || {};
  const doneCount = PKEYS.filter(k => phases[k]?.status === 'complete' || phases[k]?.status === 'completed').length;
  const activeKey = PKEYS.find(k => phases[k]?.status === 'running');
  const failedKey = PKEYS.find(k => phases[k]?.status === 'failed');

  // Title
  const titleEl = document.getElementById('stepper-run-title');
  if (titleEl) titleEl.textContent = run.query || run.run_id;

  // Status pill
  const pill = document.getElementById('stepper-status-pill');
  const pulseDot = document.getElementById('stepper-pulse-dot');
  const statusText = document.getElementById('stepper-status-text');

  if (pill && statusText) {
    pill.className = 'stepper-status-pill';
    if (activeKey) {
      pill.classList.add('running');
      if (pulseDot) pulseDot.style.display = 'inline-block';
      const pDef = PHASES.find(p => p.id === activeKey);
      statusText.textContent = `Running: ${pDef?.short || activeKey}`;
    } else if (failedKey) {
      pill.classList.add('failed');
      if (pulseDot) pulseDot.style.display = 'none';
      const pDef = PHASES.find(p => p.id === failedKey);
      statusText.textContent = `Failed at ${pDef?.short || failedKey}`;
    } else if (doneCount === PKEYS.length || run.has_card) {
      pill.classList.add('completed');
      if (pulseDot) pulseDot.style.display = 'none';
      statusText.textContent = 'All 12 Phases Complete ✓';
    } else {
      if (pulseDot) pulseDot.style.display = 'none';
      statusText.textContent = `${doneCount}/${PKEYS.length} phases complete`;
    }
  }

  // Total elapsed timer
  const totalTimeEl = document.getElementById('stepper-total-time-val');
  if (totalTimeEl) {
    totalTimeEl.textContent = formatDuration(run.total_duration_sec) || '0s';
  }

  // Resume button visibility
  const resumeBtn = document.getElementById('stepper-resume-btn');
  if (resumeBtn) {
    const isRunning = Boolean(activeKey);
    const isDone = doneCount === PKEYS.length || run.has_card;
    resumeBtn.style.display = (!isRunning && !isDone) ? 'inline-flex' : 'none';
  }

  // Horizontal stepper bar items
  let html = '';
  PKEYS.forEach((k, i) => {
    const p = PHASES.find(x => x.id === k) || { short: k, label: k };
    const pData = phases[k] || {};
    const s = pData.status || 'pending';
    const isComplete = s === 'complete' || s === 'completed';
    const isRunning = s === 'running';
    const isFailed = s === 'failed';

    const cls = isComplete ? 'done' : isFailed ? 'fail' : isRunning ? 'active' : 'pending';
    const icon = isComplete ? '✓' : isFailed ? '✗' : isRunning ? '●' : (i + 1);
    const durStr = pData.elapsed_seconds ? formatDuration(pData.elapsed_seconds) : '';

    html += `
      <div class="stepper-step-node ${cls}" onclick="scrollToPhase('${k}')" title="${p.label}: ${s}${durStr ? ' (' + durStr + ')' : ''}">
        <div class="stepper-step ${cls}">${icon}</div>
        <div class="stepper-step-lbl">${p.short}</div>
        <div class="stepper-step-time" id="step-time-${k}">${durStr || (isRunning ? 'live' : '')}</div>
      </div>
    `;

    if (i < PKEYS.length - 1) {
      const connCls = isComplete ? 'done' : isRunning ? 'active' : isFailed ? 'fail' : '';
      html += `<div class="stepper-connector ${connCls}"></div>`;
    }
  });

  const bar = document.getElementById('stepper-bar');
  if (bar) bar.innerHTML = html;
}

function renderPhases() {
  const run = getSelectedRun();
  const listEl = document.getElementById('phases-list');
  if (!run || !listEl) { if (listEl) listEl.innerHTML = ''; return; }

  const phases = run.phases || {};
  const activeKey = PKEYS.find(k => phases[k]?.status === 'running');
  let html = '';
  let prevDone = true;

  PKEYS.forEach(k => {
    const p = PHASES.find(x => x.id === k) || { label: k, desc: '', type: 'auto', dir: k };
    const pData = phases[k] || {};
    const s = pData.status || 'pending';
    const isComplete = s === 'complete' || s === 'completed';
    const isRunning = s === 'running';
    const isFailed = s === 'failed';

    const ready = prevDone || isComplete || isRunning;
    const cls = isComplete ? 'done' : isFailed ? 'fail' : isRunning ? 'active' : ready ? 'ready' : 'pending';
    const icon = isComplete ? '✓' : isFailed ? '✗' : isRunning ? '●' : '·';
    const durStr = pData.elapsed_seconds ? formatDuration(pData.elapsed_seconds) : '';

    // Artifacts list
    let artifactsHtml = '';
    const artifacts = pData.artifacts || [];
    if (artifacts.length > 0) {
      artifactsHtml = `
        <div class="phase-artifacts-row">
          <span style="font-size:10px; color:var(--text3); font-weight:500;">Artifacts:</span>
          ${artifacts.map(a => {
            const extIcon = a.ext === 'md' ? '📑' : a.ext === 'json' ? '📄' : a.ext === 'tex' ? '📐' : '📦';
            return `
              <a class="artifact-chip" onclick="viewArtifact('${run.run_id}', '${p.dir}', '${a.name}')" title="View ${a.name}">
                <span>${extIcon}</span>
                <span>${a.name}</span>
                <span class="art-size">(${formatBytes(a.size)})</span>
              </a>
            `;
          }).join('')}
        </div>
      `;
    } else if (isComplete && PHASE_FILES[k]) {
      const defFile = PHASE_FILES[k].file;
      const fname = defFile.split('/').pop();
      artifactsHtml = `
        <div class="phase-artifacts-row">
          <span style="font-size:10px; color:var(--text3); font-weight:500;">Artifacts:</span>
          <a class="artifact-chip" onclick="viewArtifact('${run.run_id}', '${p.dir}', '${fname}')" title="View ${fname}">
            <span>📄</span>
            <span>${fname}</span>
          </a>
        </div>
      `;
    }

    // Error box if failed
    let errorHtml = '';
    if (isFailed && pData.error_message) {
      errorHtml = `
        <div class="phase-error-box">
          <span><strong>Error:</strong> ${pData.error_message.slice(0, 150)}</span>
          <button class="phase-action-btn" onclick="retryPhase('${run.run_id}', '${k}')">Retry Phase</button>
        </div>
      `;
    }

    html += `
      <div class="phase-item ${cls}" id="phase-card-${k}">
        <div class="phase-item-main">
          <div class="phase-dot ${cls}">${icon}</div>
          <div class="phase-info">
            <div class="phase-title-row">
              <span class="phase-name">${p?.num ? p.num + '. ' : ''}${p?.label || k}</span>
              <span class="phase-badge ${p?.type || 'auto'}">${p?.type === 'llm' ? 'LLM' : 'Auto'}</span>
            </div>
            <div class="phase-desc">${p?.desc || ''}</div>
          </div>
          <div class="phase-meta-col">
            ${durStr ? `<span class="phase-timer" title="Phase Duration">⏱️ ${durStr}</span>` : ''}
            <span class="phase-status ${cls}">${isComplete ? 'Done' : isRunning ? 'Running...' : isFailed ? 'Failed' : 'Pending'}</span>
            ${!isComplete && !isRunning ? `<button class="phase-action-btn" onclick="retryPhase('${run.run_id}', '${k}')" title="Run this phase">Run</button>` : ''}
          </div>
        </div>
        ${artifactsHtml}
        ${errorHtml}
      </div>
    `;

    if (isComplete || isRunning) prevDone = true; else prevDone = false;
  });

  listEl.innerHTML = html;
  const runningBar = document.getElementById('running-bar');
  if (runningBar) runningBar.hidden = !activeKey;
  if (activeKey) {
    const msg = document.getElementById('running-msg');
    const pDef = PHASES.find(p => p.id === activeKey);
    if (msg) msg.textContent = `${pDef?.label || activeKey} running...`;
  }
}

function scrollToPhase(phaseKey) {
  const el = document.getElementById(`phase-card-${phaseKey}`);
  const details = document.getElementById('phase-details');
  if (details && !details.open) details.open = true;
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.style.borderColor = 'var(--accent)';
    setTimeout(() => { el.style.borderColor = ''; }, 1200);
  }
}

async function handleResumeClick() {
  const run = getSelectedRun();
  if (!run) return;
  const btn = document.getElementById('stepper-resume-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Resuming...'; }
  try {
    await API.resume(run.run_id);
    await loadRuns();
    startPolling();
  } catch (e) {
    console.error('Resume error:', e);
    alert('Could not resume: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '▶ Resume Pipeline'; }
  }
}

async function retryPhase(runId, phaseKey) {
  try {
    await API.runPhase(runId, phaseKey);
    await loadRuns();
    startPolling();
  } catch (e) {
    console.error('Run phase error:', e);
    alert('Failed to run phase: ' + e.message);
  }
}

async function viewArtifact(runId, phaseDir, filename) {
  const modal = document.getElementById('artifact-modal');
  const title = document.getElementById('artifact-modal-title');
  const meta = document.getElementById('artifact-modal-meta');
  const content = document.getElementById('artifact-modal-content');
  const dl = document.getElementById('artifact-modal-download');

  if (!modal || !content) return;

  title.textContent = filename;
  meta.textContent = `${runId} / ${phaseDir} / ${filename}`;
  content.textContent = 'Loading artifact...';
  dl.href = `/api/pipeline/runs/${runId}/artifacts/${phaseDir}/${filename}`;
  dl.setAttribute('download', filename);
  modal.classList.add('open');

  try {
    const res = await fetch(`/api/pipeline/runs/${runId}/artifacts/${phaseDir}/${filename}`);
    if (!res.ok) throw new Error('Could not load artifact file');
    const text = await res.text();
    if (filename.endsWith('.json')) {
      try {
        content.textContent = JSON.stringify(JSON.parse(text), null, 2);
      } catch {
        content.textContent = text;
      }
    } else {
      content.textContent = text;
    }
  } catch (err) {
    content.textContent = 'Error loading artifact: ' + err.message;
  }
}

function closeArtifactModal() {
  const modal = document.getElementById('artifact-modal');
  if (modal) modal.classList.remove('open');
}

function renderHistory() {
  const el = document.getElementById('history-body');
  if (!runs.length) { el.innerHTML = '<p style="font-size:12px;color:var(--text3);">No runs yet.</p>'; return; }
  el.innerHTML = runs.map(r =>
    `<div class="history-item">
      <span class="history-query">${r.query || r.run_id}</span>
      <span class="history-meta">
        ${r.has_card ? '<span style="color:var(--green)">✓ Card</span>' : `<span style="color:var(--text3)">${Object.values(r.phases||{}).filter(p=>p.status==='complete').length}/${PKEYS.length} phases</span>`}
        <span>${r.run_id?.slice(0,12)}</span>
      </span>
    </div>`
  ).join('');
}

function renderIdeaCard() {
  const el = document.getElementById('idea-card');
  const banner = document.getElementById('result-banner');
  const run = getSelectedRun();
  if (!run || !run.has_card) { el.hidden = true; banner.hidden = true; return; }
  fetch(`/api/pipeline/runs/${run.run_id}`)
    .then(r => r.json())
    .then(async data => {
      const cardResp = await fetch(`/api/ui/card/${run.run_id}`);
      const md = await cardResp.text();
      if (!md) { el.hidden = true; banner.hidden = true; return; }
      const titleMatch = md.match(/^#\s+(.+)/m);
      const title = titleMatch ? titleMatch[1] : 'Idea Card';
      el.hidden = false; banner.hidden = false;
      document.getElementById('banner-title').textContent = title;
      if (typeof marked !== 'undefined') {
        const html = marked.parse(md, { breaks: true, gfm: true });
        el.innerHTML = html;
        if (typeof renderMathInElement !== 'undefined') {
          renderMathInElement(el, { delimiters: [
            {left:'$$', right:'$$', display:true}, {left:'$', right:'$', display:false},
            {left:'\\[', right:'\\]', display:true}, {left:'\\(', right:'\\)', display:false},
          ], throwOnError: false });
        }
      } else { el.innerHTML = `<pre style="font-size:12px;white-space:pre-wrap;">${md.slice(0,2000)}</pre>`; }
    }).catch(() => { el.hidden = true; banner.hidden = true; });
}

async function autoChain() {}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try { const r = await API.runs(); runs = r || []; render(); const allDone = runs.every(r => r.has_card); if (allDone && pollTimer) { clearInterval(pollTimer); pollTimer = null; } } catch (e) { console.warn('poll:', e); }
  }, 3000);
}

function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

async function runPipeline() {
  const query = document.getElementById('idea-query').value.trim();
  if (!query) { const inp = document.getElementById('idea-query'); inp.style.borderColor = 'var(--red)'; setTimeout(() => inp.style.borderColor = '', 800); return; }
  const btn = document.getElementById('idea-btn');
  btn.disabled = true; btn.textContent = 'Starting...';
  try {
    const result = await API.startPipeline(query);
    if (result && result.run_id && result.run_id !== 'pending') {
      selectedRun = result.run_id;
    }
    await loadRuns(); startPolling();
    document.getElementById('pipeline-container').hidden = false;
    document.getElementById('empty-state').hidden = true;
    render();
    btn.disabled = false; btn.textContent = 'Generate Idea';
  } catch (e) {
    console.error('Pipeline start failed:', e);
    btn.textContent = 'Failed — try again';
    setTimeout(() => { btn.disabled = false; btn.textContent = 'Generate Idea'; }, 2000);
  }
}

async function runScoop() {
  const problem = document.getElementById('scoop-problem').value.trim();
  const novelty = document.getElementById('scoop-novelty').value.trim();
  if (!problem || !novelty) {
    if (!problem) document.getElementById('scoop-problem').style.borderColor = 'var(--red)';
    if (!novelty) document.getElementById('scoop-novelty').style.borderColor = 'var(--red)';
    setTimeout(() => { document.getElementById('scoop-problem').style.borderColor = ''; document.getElementById('scoop-novelty').style.borderColor = ''; }, 800);
    return;
  }
  const btn = document.getElementById('scoop-btn');
  btn.disabled = true; btn.textContent = 'Checking...';
  const progress = document.getElementById('scoop-progress'); progress.hidden = false;
  const statusText = document.getElementById('scoop-status-text');
  const STEPS = ['Decompose', 'Search', 'Triage', 'Identify', 'Deep Dive', 'Verdict', 'Summarize'];
  updateScoopSteps(0, STEPS); statusText.textContent = 'Decomposing novelty claim...';
  try {
    const result = await API.scoopStart(problem, novelty);
    const scoopId = result.scoop_id;
    if (scoopTimer) clearInterval(scoopTimer);
    scoopTimer = setInterval(async () => {
      try {
        const status = await API.scoopStatus(scoopId);
        const step = status.step || 0; const stepIdx = Math.min(step, STEPS.length);
        updateScoopSteps(stepIdx, STEPS);
        if (stepIdx > 0 && stepIdx <= STEPS.length) statusText.textContent = STEPS[Math.min(stepIdx - 1, STEPS.length - 1)] + '...';
        if (status.status === 'done' && status.result) {
          clearInterval(scoopTimer); scoopTimer = null;
          btn.disabled = false; btn.textContent = 'Check Novelty';
          statusText.textContent = 'Complete ✓'; renderScoopResult(status.result);
        } else if (status.status?.startsWith('failed')) {
          clearInterval(scoopTimer); scoopTimer = null;
          btn.disabled = false; btn.textContent = 'Check Novelty'; statusText.textContent = 'Failed';
        }
      } catch (e) { console.warn('scoop poll:', e); }
    }, 2000);
  } catch (e) {
    console.error('Scoop start failed:', e);
    btn.disabled = false; btn.textContent = 'Check Novelty'; statusText.textContent = 'Failed to start';
  }
}

function updateScoopSteps(activeIdx, steps) {
  const el = document.getElementById('scoop-steps-bar');
  let html = '';
  steps.forEach((s, i) => {
    const cls = i < activeIdx ? 'done' : i === activeIdx ? 'active' : 'pending';
    html += `<div class="scoop-step"><div class="scoop-step-dot ${cls}">${i < activeIdx ? '✓' : i === activeIdx ? '●' : i+1}</div><div class="scoop-step-label">${s}</div></div>`;
  });
  el.innerHTML = html;
}

function renderScoopResult(result) {
  const el = document.getElementById('scoop-result'); el.hidden = false;
  const level = result.level || 0;
  const levelClass = `verdict-${level}`;
  const levelLabels = { 1:'Fully Scooped', 2:'Largely Scooped', 3:'Partial Overlap', 4:'Mostly Novel', 5:'Fully Novel' };
  const candidates = (result.top_candidates || []).slice(0, 5);
  const perAxis = result.per_axis || {};
  el.innerHTML = `
    <div class="verdict-large ${levelClass}">${level}/5 — ${levelLabels[level] || 'Unknown'}</div>
    ${result.summary ? `<div class="verdict-meta">${result.summary}</div>` : ''}
    <div class="axis-grid">${Object.entries(perAxis).map(([name, val]) => {
      const scoreCls = val === 'clear' ? 'clear' : val === 'partial' ? 'partial' : 'scooped';
      const scoreLbl = val === 'clear' ? 'Clear' : val === 'partial' ? 'Partial' : 'Scooped';
      return `<div class="axis-card"><div class="axis-name">${name}</div><div class="axis-score ${scoreCls}">${scoreLbl}</div></div>`;
    }).join('')}</div>
    ${candidates.length ? `<div class="candidate-list"><div style="font-size:11px;color:var(--text3);margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px;">Top Candidates</div>
      ${candidates.map(c => `<div class="candidate-item"><span class="candidate-score">${c.overlap_score}/4</span><span class="candidate-title">${c.title}</span></div>`).join('')}</div>` : ''}
    ${result.recommendation ? `<div style="margin-top:12px;font-size:13px;">Recommendation: <strong style="color:var(--accent)">${result.recommendation}</strong></div>` : ''}
  `;
}

function scrollToForm() { document.querySelector('.hero-card').scrollIntoView({ behavior: 'smooth' }); }
function closeModal() { document.getElementById('modal').classList.remove('open'); }
function openModal(title, content) {
  document.getElementById('modal-title').textContent = title || 'Details';
  document.getElementById('modal-content').textContent = typeof content === 'string' ? content : JSON.stringify(content, null, 2);
  document.getElementById('modal').classList.add('open');
}