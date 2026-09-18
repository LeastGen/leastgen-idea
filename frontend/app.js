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
  const h = async (p, o) => {
    const r = await fetch(base + p, { ...o, credentials: 'include' });
    if (r.status === 401) { handleAuthError(); throw Error('Unauthorized'); }
    if (!r.ok) {
      let msg = r.statusText;
      try {
        const text = await r.text();
        try {
          const e = JSON.parse(text);
          msg = e.detail || e.error || msg;
        } catch { if (text) msg = text.slice(0, 300); }
      } catch { /* fall back to statusText */ }
      throw Error(msg);
    }
    const ct = r.headers.get('content-type') || '';
    if (ct.includes('json')) return r.json();
    return r.text();
  };
  return {
    url: (p) => base + p,
    blob: async (p) => {
      const r = await fetch(base + p, { credentials: 'include' });
      if (r.status === 401) { handleAuthError(); throw Error('Unauthorized'); }
      if (!r.ok) throw Error(`Download failed: ${r.status} ${r.statusText}`);
      return r.blob();
    },
    login: (e, p) => h('/api/auth/login', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({email:e, password:p}) }),
    signup: (n, e, p) => h('/api/auth/signup', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({name:n, email:e, password:p}) }),
    me: () => h('/api/auth/me'),
    logout: () => h('/api/auth/logout', { method:'POST' }),
    limits: () => h('/api/auth/limits'),
    runs: (limit = 20, offset = 0) => h(`/api/pipeline/runs?limit=${encodeURIComponent(limit)}&offset=${encodeURIComponent(offset)}`),
    runsAll: () => h('/api/pipeline/runs?limit=100'),
    run: (id) => h(`/api/pipeline/runs/${id}`),
    startPipeline: (q) => h('/api/pipeline/start', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({query:q}) }),
    resume: (id) => h(`/api/pipeline/runs/${id}/resume`, { method:'POST' }),
    gate: (id) => h(`/api/pipeline/runs/${id}/gate`),
    gateOverride: (id) => h(`/api/pipeline/runs/${id}/gate-override`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({override:true}) }),
    deleteRun: (id) => h(`/api/pipeline/runs/${id}`, { method:'DELETE' }),
    cancel: (id) => h(`/api/pipeline/runs/${id}/cancel`, { method:'POST' }),
    runPhase: (id, phase) => h(`/api/pipeline/runs/${id}/phase/${phase}`, { method:'POST' }),
    artifacts: (id) => h(`/api/pipeline/runs/${id}/artifacts`),
    artifactContent: (id, pdir, fn) => h(`/api/pipeline/runs/${id}/artifacts/${pdir}/${fn}`),
    scoopStart: (p, n) => h('/api/scoop-check/start', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({problem:p, novelty:n}) }),
    scoopStatus: (id) => h(`/api/scoop-check/${id}`),
    card: (id) => h(`/api/ui/card/${id}`),
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
    alert('Subscription activated.');
  } else if (checkout === 'canceled') {
    alert('Checkout canceled. No changes made.');
  }

  // Clean URL
  window.history.replaceState({}, document.title, window.location.pathname);
}

// ════════════════════════════════════════════════════════════════════════════
//  Auth
// ════════════════════════════════════════════════════════════════════════════

// ── XSS helpers: escape all user-controlled strings before innerHTML ──────
// escapeHtml: for text/attribute contexts. escapeAttr: also escapes quotes.
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function escapeAttr(s) {
  return escapeHtml(s).replace(/`/g, '&#96;');
}
// jsStr: escape a value embedded in a single-quoted JS string inside onclick="".
function jsStr(s) {
  return String(s ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;').replace(/</g, '\\x3c').replace(/>/g, '\\x3e').replace(/\n/g, '\\n').replace(/\r/g, '\\r');
}
// sanitizeHtml: DOMPurify-less sanitizer for marked output. Strips <script>,
// <iframe>/<object>/<embed>/<form>, event-handler attributes, javascript:/vbscript:
// URLs, and <style>/<link>. Everything else passes through.
function sanitizeHtml(html) {
  let out = String(html || '');
  out = out.replace(/<script[\s\S]*?<\/script\s*>/gi, '');
  out = out.replace(/<\/?(iframe|object|embed|form|base|meta|link|style)\b[^>]*>/gi, '');
  out = out.replace(/\s+on[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, '');
  out = out.replace(/(href|src|xlink:href)\s*=\s*("([^"]*)"|'([^']*)'|([^\s>]+))/gi, (m, attr, q, d1, d2, d3) => {
    const v = (d1 ?? d2 ?? d3 ?? '').trim();
    if (/^\s*(javascript|vbscript|data\s*:)/i.test(v)) return `${attr}="#"`;
    return m;
  });
  return out;
}

let currentUser = null;
let authChecked = false;

async function checkAuth() {
  try {
    currentUser = await API.me();
    authChecked = true;
    enterApp({ guest: false });
    refreshSubscriptionStatus();
    initApp();
  } catch (e) {
    // Not logged in → guest trial mode (1 free run, no account).
    // Pipeline/scoop endpoints are unauthenticated; the trial limit
    // is enforced client-side via localStorage (see TRIAL_* below).
    authChecked = false;
    currentUser = null;
    if (!trialConsumed()) enterApp({ guest: true });
    else {
      document.getElementById('landing').classList.remove('hidden');
      document.getElementById('app').hidden = true;
      document.getElementById('auth-overlay').classList.add('hidden');
    }
    initApp();
  }
}

// ── Guest trial: 1 free run before signup ──────────────────────────────────
// TRIAL_KEY in localStorage marks the single free run as consumed.

const TRIAL_KEY = 'leastgen_trial_used';

function trialConsumed() {
  try { return localStorage.getItem(TRIAL_KEY) === '1'; } catch (e) { return false; }
}

function consumeTrial() {
  try { localStorage.setItem(TRIAL_KEY, '1'); } catch (e) {}
  updateTrialBanner();
}

function enterApp({ guest }) {
  document.getElementById('landing').classList.add('hidden');
  document.getElementById('app').hidden = false;
  document.getElementById('auth-overlay').classList.add('hidden');
  if (guest) {
    document.getElementById('user-menu').hidden = true;
    showTrialBanner();
  } else {
    hideTrialBanner();
    document.getElementById('user-menu').hidden = false;
    document.getElementById('user-name-display').textContent = currentUser.name || currentUser.email.split('@')[0];
    document.getElementById('user-email-display').textContent = currentUser.email;
    document.getElementById('user-runs-display').textContent = `Runs: ${currentUser.runs_used}/${currentUser.runs_limit}`;
  }
}

function tryTrial() {
  // Landing "Try 1 free run" → straight into the app, no account.
  enterApp({ guest: true });
  initApp();
  document.getElementById('scoop-problem').focus();
  window.scrollTo({ top: 0 });
}

function showTrialBanner() {
  let bar = document.getElementById('trial-banner');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'trial-banner';
    bar.className = 'trial-banner';
    const header = document.querySelector('#app .header');
    if (header) header.after(bar);
  }
  updateTrialBanner();
  bar.hidden = false;
}

function updateTrialBanner() {
  const bar = document.getElementById('trial-banner');
  if (!bar) return;
  if (trialConsumed()) {
    bar.innerHTML = `<span>Trial run used.</span> <a href="#" onclick="showAuth('signup'); return false;">Create a free account</a><span>&nbsp;for 10 runs/month.</span>`;
  } else {
    bar.innerHTML = `<span>Guest trial — 1 free run, no account needed.</span> <a href="#" onclick="showAuth('signup'); return false;">Create account</a><span>&nbsp;for more.</span>`;
  }
}

function hideTrialBanner() {
  const bar = document.getElementById('trial-banner');
  if (bar) bar.hidden = true;
}

// Returns true if the run may proceed. Guests get exactly 1 run total
// (pipeline OR scoop); afterwards the signup overlay is shown instead.
function checkTrialGate() {
  if (currentUser) return true;
  if (!trialConsumed()) return true;
  showTrialBanner();
  showAuth('signup');
  return false;
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
    enterApp({ guest: false });
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
  const btn = document.querySelector('.user-btn');
  dd.hidden = !dd.hidden;
  if (btn) btn.setAttribute('aria-expanded', dd.hidden ? 'false' : 'true');
  if (!dd.hidden) {
    refreshSubscriptionStatus();
    // Move focus to the first actionable item for keyboard users.
    const first = dd.querySelector('.dropdown-item[tabindex="0"]');
    if (first) first.focus();
  } else if (btn) {
    btn.focus();
  }
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
    const btn = document.querySelector('.user-btn');
    if (btn) btn.setAttribute('aria-expanded', 'false');
  }
});

// Escape closes the user dropdown and returns focus to the trigger.
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  const dd = document.getElementById('user-dropdown');
  if (dd && !dd.hidden) {
    dd.hidden = true;
    const btn = document.querySelector('.user-btn');
    if (btn) { btn.setAttribute('aria-expanded', 'false'); btn.focus(); }
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
      const isPro = String(p.id || '').startsWith('pro');
      const isFree = p.id === 'free';
      return `<div class="billing-plan ${isPro ? 'featured' : ''}">
        <div class="billing-plan-name">${escapeHtml(p.name)}</div>
        <div class="billing-plan-price">${p.price === 0 ? "Free" : '$' + escapeHtml(p.price)}<span>/${p.interval === 'month' ? 'mo' : 'yr'}</span></div>
        <ul class="billing-plan-features">${(p.features || []).map(f => `<li>${escapeHtml(f)}</li>`).join('')}</ul>
        ${isFree ? '<div style="font-size:12px;color:var(--text3);text-align:center;">' + "Free" + '</div>'
          : `<button class="btn btn-primary billing-btn" onclick="upgrade('${jsStr(p.id)}')">${"Select"}</button>`}
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
  if (!run || !run.has_idea_card) return;
  // Base-aware + credentialed: fetch the bytes via the API helper (which
  // targets the /d/<port>/ backend origin with credentials:'include'),
  // then trigger the download from a blob URL. A bare window.open() to a
  // relative /api/... path bypasses the base prefix and drops auth.
  try {
    const blob = await API.blob(`/api/export/${run.run_id}/${format}`);
    const objUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = objUrl;
    a.download = `idea-card-${run.run_id}.${format}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(objUrl), 5000);
  } catch (e) {
    console.error('Export failed:', e);
    alert('Export failed: ' + (e.message || e));
  }
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
  {id:'phase3_collision', num:7, label:'Collision Check', short:'Collision', desc:'Prior-art search & overlap hits', type:'auto', dir:'phase3_collision'},
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
      if (phaseCardTimer) phaseCardTimer.textContent = `${formatDuration(pData.elapsed_seconds)}`;
    }
    const totalTimeEl = document.getElementById('stepper-total-time-val');
    if (totalTimeEl) totalTimeEl.textContent = formatDuration(run.total_duration_sec) || '0s';
  }, 1000);
}

function initApp() {
  loadRuns();
  startLiveTimerTick();
  document.getElementById('scoop-problem').focus();
  // initApp() runs on every login/signup/guest entry; guard so the global
  // Ctrl+Enter shortcut is registered exactly once (otherwise N logins →
  // N duplicate pipeline/scoop POSTs per keypress).
  if (initApp._keydownBound) return;
  initApp._keydownBound = true;
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
  document.querySelectorAll('.mode-panel').forEach(el => {
    const on = el.id === `panel-${mode}`;
    el.classList.toggle('active', on);
    if (on) el.removeAttribute('tabindex');
  });
  document.querySelectorAll('.mode-btn').forEach(el => {
    const on = el.dataset.mode === mode;
    el.classList.toggle('active', on);
    el.setAttribute('aria-selected', on ? 'true' : 'false');
    el.tabIndex = on ? 0 : -1;
  });
  document.getElementById(mode === 'scoop' ? 'scoop-problem' : 'idea-query').focus();
}

// Arrow-key navigation between mode tabs (roving tabindex, ARIA tab pattern).
document.addEventListener('keydown', e => {
  const tab = e.target && e.target.closest ? e.target.closest('.mode-btn[role="tab"]') : null;
  if (!tab) return;
  if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft' && e.key !== 'Home' && e.key !== 'End') return;
  e.preventDefault();
  const tabs = [...document.querySelectorAll('.mode-btn[role="tab"]')];
  let i = tabs.indexOf(tab);
  if (e.key === 'ArrowRight') i = (i + 1) % tabs.length;
  else if (e.key === 'ArrowLeft') i = (i - 1 + tabs.length) % tabs.length;
  else if (e.key === 'Home') i = 0;
  else i = tabs.length - 1;
  switchMode(tabs[i].dataset.mode);
  tabs[i].focus();
});

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
  const done = runs.filter(r => r.has_idea_card).length;
  const active = runs.filter(r => {
    const p = r.phases || {};
    return p.phase0 && p.phase0.status === 'complete' && !p.phase4_card;
  }).length;
  // Prefer the server-computed paper_count (real lit_results.json length);
  // fall back to the old byte-size estimate only when it is absent so
  // legacy payloads never render a bogus zero.
  let papers = 0;
  runs.forEach(r => {
    if (typeof r.paper_count === 'number' && r.paper_count >= 0) { papers += r.paper_count; return; }
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
  const hasCard = id => { const r = runs.find(x => x.run_id === id); return r && r.has_idea_card; };
  el.innerHTML = runs.map(r =>
    `<button class="run-tab${r.run_id === selectedRun ? ' active' : ''}"
             onclick="selectRun('${jsStr(r.run_id)}')" title="${escapeAttr(r.query?.slice(0,80) ?? '')}">
      ${escapeHtml(r.query?.slice(0,30) || r.run_id.slice(0,20))}${hasCard(r.run_id) ? '<span class="check">✓</span>' : ''}
    </button>`
  ).join('');
}

function selectRun(id) { if (selectedRun !== id) _cardCache.delete(id); selectedRun = id; renderRunTabs(); renderPhases(); renderStepper(); renderIdeaCard(); }
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
    } else if (doneCount === PKEYS.length || run.has_idea_card) {
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

  // Resume + Cancel button visibility
  const resumeBtn = document.getElementById('stepper-resume-btn');
  const cancelBtn = document.getElementById('stepper-cancel-btn');
  const isRunning = Boolean(activeKey);
  const isDone = doneCount === PKEYS.length || run.has_idea_card;
  if (resumeBtn) {
    resumeBtn.style.display = (!isRunning && !isDone) ? 'inline-flex' : 'none';
  }
  if (cancelBtn) {
    cancelBtn.style.display = isRunning ? 'inline-flex' : 'none';
  }

  // Progress %, ETA estimate, and ARIA progressbar wiring.
  // ETA heuristic: mean elapsed of completed phases × remaining phases.
  const doneElapsed = PKEYS
    .map(k => phases[k]?.elapsed_seconds)
    .filter(v => typeof v === 'number' && v > 0);
  const avgPhase = doneElapsed.length
    ? doneElapsed.reduce((a, b) => a + b, 0) / doneElapsed.length
    : 0;
  const remaining = PKEYS.length - doneCount;
  const etaSec = (isRunning && avgPhase > 0 && remaining > 0)
    ? Math.round(avgPhase * remaining)
    : 0;
  const pct = Math.round((doneCount / PKEYS.length) * 100);
  const pbar = document.getElementById('stepper-progressbar');
  const pfill = document.getElementById('stepper-progress-fill');
  const ptext = document.getElementById('stepper-progress-text');
  if (pbar) {
    pbar.setAttribute('aria-valuenow', String(doneCount));
    pbar.setAttribute('aria-valuetext',
      `${doneCount} of ${PKEYS.length} phases complete, ${pct} percent${etaSec ? `, estimated ${formatDuration(etaSec)} remaining` : ''}`);
  }
  if (pfill) pfill.style.width = `${pct}%`;
  if (ptext) {
    ptext.textContent = isDone
      ? `${doneCount}/${PKEYS.length} · 100% · done`
      : `${doneCount}/${PKEYS.length} · ${pct}%${etaSec ? ` · ETA ~${formatDuration(etaSec)}` : ' · ETA —'}`;
  }

  // Horizontal stepper bar items — real <button>s so they are keyboard
  // operable (Tab + Enter) with arrow-key nav (roving via listbox handler).
  // aria-current="step" marks the running phase; completed/failed get labels.
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
    const stateLbl = isComplete ? 'completed' : isFailed ? 'failed' : isRunning ? 'running' : 'pending';
    const ariaCur = isRunning ? ' aria-current="step"' : '';

    html += `
      <button type="button" class="stepper-step-node ${cls}" role="option" aria-selected="${isRunning ? 'true' : 'false'}"${ariaCur}
        data-phase="${k}" onclick="scrollToPhase('${k}')"
        aria-label="Phase ${i + 1} of ${PKEYS.length}: ${escapeAttr(p.label)}, ${stateLbl}${durStr ? ', ' + durStr : ''}"
        title="${escapeAttr(p.label)}: ${escapeAttr(s)}${durStr ? ' (' + durStr + ')' : ''}">
        <span class="stepper-step ${cls}" aria-hidden="true">${icon}</span>
        <span class="stepper-step-lbl">${p.short}</span>
        <span class="stepper-step-time" id="step-time-${k}">${durStr || (isRunning ? 'live' : '')}</span>
      </button>
    `;

    if (i < PKEYS.length - 1) {
      const connCls = isComplete ? 'done' : isRunning ? 'active' : isFailed ? 'fail' : '';
      html += `<div class="stepper-connector ${connCls}" aria-hidden="true"></div>`;
    }
  });

  const bar = document.getElementById('stepper-bar');
  if (bar) bar.innerHTML = html;
}

function renderPhases() {
  const run = getSelectedRun();
  const listEl = document.getElementById('phases-list');
  if (!run) { if (listEl) listEl.innerHTML = ''; return; }
  if (!listEl) return;
  const phases = run.phases || {};
  // No-results state: run exists but no phase data has landed yet.
  if (!Object.keys(phases).length && !run.has_idea_card) {
    listEl.innerHTML = `<div class="inline-empty">No results yet — this run hasn't produced any phases.<br>The pipeline fills phases in as it works; you can also retry.<br><button class="btn btn-secondary btn-sm" onclick="loadRuns()">Refresh status</button></div>`;
    const rb0 = document.getElementById('running-bar');
    if (rb0) rb0.hidden = true;
    return;
  }
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
            const extIcon = String(a.ext || 'file').toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 8) || 'FILE';
            return `
              <button type="button" class="artifact-chip" onclick="viewArtifact('${jsStr(run.run_id)}', '${jsStr(p.dir)}', '${jsStr(a.name)}')" title="View ${escapeAttr(a.name)}">
                <span class="art-ext">${escapeHtml(extIcon)}</span>
                <span>${escapeHtml(a.name)}</span>
                <span class="art-size">(${formatBytes(a.size)})</span>
              </button>
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
          <button type="button" class="artifact-chip" onclick="viewArtifact('${jsStr(run.run_id)}', '${jsStr(p.dir)}', '${jsStr(fname)}')" title="View ${escapeAttr(fname)}">
            <span class="art-ext">${escapeHtml(fname.split('.').pop().toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 8))}</span>
            <span>${escapeHtml(fname)}</span>
          </button>
        </div>
      `;
    }

    // Full error text with copy button + retry CTA (no more 150-char truncation,
    // no stranded users). The <details> preview shows the first 200 chars;
    // expanding reveals the complete message for copy/debug.
    let errorHtml = '';
    if (isFailed && pData.error_message) {
      const fullErr = String(pData.error_message);
      const preview = fullErr.length > 200 ? fullErr.slice(0, 200) + '…' : fullErr;
      const errId = `phase-err-${k}`;
      errorHtml = `
        <div class="phase-error-box" role="alert">
          <details class="phase-error-text">
            <summary><strong>Error:</strong> ${escapeHtml(preview)}</summary>
            <pre class="phase-error-full" id="${errId}">${escapeHtml(fullErr)}</pre>
          </details>
          <div class="phase-error-actions">
            <button class="phase-action-btn" onclick="copyPhaseError('${errId}', this)">Copy error</button>
            <button class="phase-action-btn" onclick="retryPhase('${jsStr(run.run_id)}', '${jsStr(k)}')">Retry Phase</button>
          </div>
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
            ${durStr ? `<span class="phase-timer" title="Phase duration">${durStr}</span>` : ''}
            <span class="phase-status ${cls}">${isComplete ? 'Done' : isRunning ? 'Running' : isFailed ? 'Failed' : 'Pending'}</span>
            ${!isComplete && !isRunning ? `<button class="phase-action-btn" onclick="retryPhase('${jsStr(run.run_id)}', '${jsStr(k)}')" title="Run this phase">Run</button>` : ''}
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
    if (msg) msg.textContent = `${pDef?.label || activeKey} running`;
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
  if (btn) { btn.disabled = true; btn.textContent = 'Resuming'; }
  try {
    await API.resume(run.run_id);
    await loadRuns();
    startPolling();
  } catch (e) {
    console.error('Resume error:', e);
    alert('Could not resume: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Resume pipeline'; }
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

// Cancel the selected pipeline run. Wired to POST
// /api/pipeline/runs/{run_id}/cancel (backend-concurrency owns the endpoint).
// If the backend has no cancel route yet, the call 404s — degrade gracefully
// by stopping local polling and noting the run as cancelled locally so the
// user is never stuck with a spinning timer they cannot stop.
async function cancelPipeline() {
  const run = getSelectedRun();
  if (!run) return;
  const btn = document.getElementById('stepper-cancel-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Cancelling'; }
  stopPolling();
  try {
    await API.cancel(run.run_id);
  } catch (e) {
    // No cancel endpoint yet (or network error): mark locally so the UI
    // stops polling instead of stranding the user on a phantom run.
    console.warn('Cancel endpoint unavailable, stopping locally:', e);
    run._cancelledLocally = true;
    const msg = document.getElementById('running-msg');
    if (msg) msg.textContent = 'Run cancelled (local stop — server may still be working)';
  } finally {
    await loadRuns();
    if (btn) { btn.disabled = false; btn.textContent = 'Cancel run'; }
  }
}

// Copy a full phase error to the clipboard (fallback: select the <pre> text).
async function copyPhaseError(errId, btn) {
  const el = document.getElementById(errId);
  copyPhaseErrorText(el ? el.textContent : '', btn);
  // Fallback when the clipboard write fails (permissions/insecure context):
  // leave the text selected so keyboard users can copy manually.
  if (el && !navigator.clipboard) {
    const range = document.createRange();
    range.selectNodeContents(el);
    const sel = window.getSelection();
    if (sel) { sel.removeAllRanges(); sel.addRange(range); }
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
  // Base-aware download link: the modal <a> fetches through the API helper
  // (credentials:'include') and swaps in a blob URL, so the /d/<port>/
  // prefix and session cookie are never dropped.
  dl.removeAttribute('href');
  dl.setAttribute('download', filename);
  dl.onclick = async (ev) => {
    ev.preventDefault();
    try {
      const blob = await API.blob(`/api/pipeline/runs/${runId}/artifacts/${phaseDir}/${filename}`);
      const objUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = objUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(objUrl), 5000);
    } catch (err) {
      content.textContent = 'Error downloading artifact: ' + err.message;
    }
  };
  modal.classList.add('open');
  // Focus management: move focus into the dialog, restore on close.
  modal._restoreFocus = document.activeElement;
  const closeBtn = modal.querySelector('.modal-close');
  if (closeBtn) closeBtn.focus();

  try {
    const text = await API.artifactContent(runId, phaseDir, filename);
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
  if (modal) {
    modal.classList.remove('open');
    if (modal._restoreFocus && modal._restoreFocus.focus) modal._restoreFocus.focus();
  }
}

function renderHistory() {
  const el = document.getElementById('history-body');
  if (!runs.length) {
    el.innerHTML = `<div class="inline-empty">No runs yet.<br>Describe your idea above and press <strong>Check &amp; build</strong> — results land here.<br><button class="btn btn-primary btn-sm" onclick="startScoopCTA()">Check an idea</button></div>`;
    return;
  }
  el.innerHTML = runs.map(r =>
    `<div class="history-item">
      <span class="history-query">${escapeHtml(r.query || r.run_id)}</span>
      <span class="history-meta">
        ${r.has_idea_card ? '<span style="color:var(--green)">✓ Card</span>' : `<span style="color:var(--text3)">${Object.values(r.phases||{}).filter(p=>p.status==='complete').length}/${PKEYS.length} phases</span>`}
        <span>${escapeHtml(r.run_id?.slice(0,12) ?? '')}</span>
      </span>
    </div>`
  ).join('');
}

// -- Idea-card render cache (perf t3): skip re-fetch/re-parse/KaTeX when unchanged --
// Poll ticks call render() -> renderIdeaCard() every 3s; without this cache that
// meant 2 fetches + marked.parse + KaTeX on every tick.
const _cardCache = new Map(); // run_id -> { hash, renderedAt }
let _cardInflight = null;     // dedupe concurrent card fetches for same run

function _hashStr(s) {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); }
  return (h >>> 0).toString(16);
}

function _paintIdeaCard(el, banner, md) {
  const titleMatch = md.match(/^#\s+(.+)/m);
  const title = titleMatch ? titleMatch[1] : 'Idea Card';
  el.hidden = false; banner.hidden = false;
  document.getElementById('banner-title').textContent = title;
  if (typeof marked !== 'undefined') {
    // Escape raw HTML in the markdown source first so stored/LLM-returned
    // HTML (e.g. <script>, <img onerror>) becomes inert text, then
    // sanitize the rendered HTML as defense-in-depth.
    const safeSrc = escapeHtml(md);
    const html = marked.parse(safeSrc, { breaks: true, gfm: true });
    el.innerHTML = sanitizeHtml(html);
    if (typeof renderMathInElement !== 'undefined') {
      renderMathInElement(el, { delimiters: [
        {left:'$$', right:'$$', display:true}, {left:'$', right:'$', display:false},
        {left:'\\[', right:'\\]', display:true}, {left:'\\(', right:'\\)', display:false},
      ], throwOnError: false });
    }
  } else { el.innerHTML = `<pre style="font-size:12px;white-space:pre-wrap;">${escapeHtml(md.slice(0,2000))}</pre>`; }
}

function renderIdeaCard() {
  const el = document.getElementById('idea-card');
  const banner = document.getElementById('result-banner');
  const run = getSelectedRun();
  if (!run || !run.has_idea_card) { el.hidden = true; banner.hidden = true; return Promise.resolve(false); }
  const runId = run.run_id;
  if (_cardInflight === runId) return Promise.resolve(false);
  _cardInflight = runId;
  // Single fetch: the card markdown itself. The extra run-detail fetch is
  // dropped -- has_idea_card from the polled list is sufficient gating.
  return API.card(runId)
    .then(async (md) => {
      _cardInflight = null;
      if (!md) { el.hidden = true; banner.hidden = true; return false; }
      const hash = _hashStr(md);
      const cached = _cardCache.get(runId);
      if (cached && cached.hash === hash) return false; // unchanged: skip re-render
      _paintIdeaCard(el, banner, md);
      _cardCache.set(runId, { hash, renderedAt: Date.now() });
      return true;
    }).catch(() => { _cardInflight = null; el.hidden = true; banner.hidden = true; return false; });
}

async function autoChain() {}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  // Skip fetch when tab hidden (visibilitychange): no per-poll work at all
  // while hidden; catch-up poll fires on visible.
  let pollStale = false;
  if (!startPolling._visHooked) {
    startPolling._visHooked = true;
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && pollStale && pollTimer) {
        pollStale = false;
        API.runs().then(r => { runs = r || []; render(); }).catch(e => console.warn('poll:', e));
      }
    });
  }
  pollTimer = setInterval(async () => {
    if (document.hidden) { pollStale = true; return; }
    pollStale = false;
    try { const r = await API.runs(); runs = r || []; render(); const allDone = runs.every(r => r.has_idea_card); if (allDone && pollTimer) { clearInterval(pollTimer); pollTimer = null; } } catch (e) { console.warn('poll:', e); }
  }, 3000);
}

function stopPolling() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

async function runPipeline() {
  const query = document.getElementById('idea-query').value.trim();
  if (!query) { const inp = document.getElementById('idea-query'); inp.style.borderColor = 'var(--red)'; setTimeout(() => inp.style.borderColor = '', 800); return; }
  if (!checkTrialGate()) return;
  const btn = document.getElementById('idea-btn');
  btn.disabled = true; btn.textContent = 'Starting';
  try {
    const result = await API.startPipeline(query);
    if (!currentUser) consumeTrial();
    if (result && result.run_id && result.run_id !== 'pending') {
      selectedRun = result.run_id;
    }
    await loadRuns(); startPolling();
    document.getElementById('pipeline-container').hidden = false;
    document.getElementById('empty-state').hidden = true;
    render();
    btn.disabled = false; btn.textContent = 'Generate idea';
  } catch (e) {
    console.error('Pipeline start failed:', e);
    btn.textContent = 'Start failed. Retry.';
    setTimeout(() => { btn.disabled = false; btn.textContent = 'Generate idea'; }, 2000);
  }
}

async function runUnified() {
  // Single entry: one idea box → start the pipeline; the novelty gate
  // (server-side, after phase0) decides whether expensive phases run.
  const raw = document.getElementById('scoop-problem').value.trim();
  if (!raw) {
    document.getElementById('scoop-problem').style.borderColor = 'var(--red)';
    setTimeout(() => { document.getElementById('scoop-problem').style.borderColor = ''; }, 800);
    return;
  }
  if (!checkTrialGate()) return;
  hideGateCard();
  const btn = document.getElementById('scoop-btn');
  btn.disabled = true; btn.textContent = 'Starting';
  try {
    const result = await API.startPipeline(raw);
    if (!currentUser) consumeTrial();
    if (result && result.run_id && result.run_id !== 'pending') {
      selectedRun = result.run_id;
    }
    await loadRuns(); startPolling();
    pollGateForSelected();
    document.getElementById('pipeline-container').hidden = false;
    const empty = document.getElementById('empty-state');
    if (empty) empty.hidden = true;
    render();
    btn.disabled = false; btn.textContent = 'Check & build';
  } catch (e) {
    console.error('Unified start failed:', e);
    btn.textContent = 'Start failed. Retry.';
    setTimeout(() => { btn.disabled = false; btn.textContent = 'Check & build'; }, 2000);
  }
}

// ── Novelty gate card (unified flow) ────────────────────────────────────────
// The gate verdict lands in phase0/novelty_gate.json once phase0 completes.
// Poll it while the selected run is in early phases; render stop/ask UI.

let gatePollTimer = null;

function hideGateCard() {
  const card = document.getElementById('gate-card');
  if (card) card.hidden = true;
}

function pollGateForSelected() {
  if (gatePollTimer) clearInterval(gatePollTimer);
  gatePollTimer = setInterval(async () => {
    try {
      const run = getSelectedRun();
      if (!run) return;
      const phases = run.phases || {};
      const gatePhase = phases.phase0_fulltext;
      // Gate decided (blocked) → show stop/ask card.
      if (gatePhase && gatePhase.status === 'awaiting_gate') {
        const g = await API.gate(run.run_id).catch(() => null);
        renderGateCard(g && g.gate ? g.gate : null, gatePhase.error_message);
        clearInterval(gatePollTimer); gatePollTimer = null;
        return;
      }
      // Run moved past the gate (passed or overridden) → hide card, stop.
      const pastGate = ['phase1', 'phase2_select', 'phase2_generate', 'phase2_coherence',
        'phase3_collision', 'phase3_critique', 'phase3_revise',
        'phase4_skeleton', 'phase4_fill', 'phase4_card']
        .some(k => ['complete', 'completed', 'running'].includes((phases[k] || {}).status));
      if (pastGate || run.has_idea_card) {
        hideGateCard();
        clearInterval(gatePollTimer); gatePollTimer = null;
      }
    } catch (e) { console.warn('gate poll:', e); }
  }, 3000);
}

function renderGateCard(gate, fallbackMsg) {
  const card = document.getElementById('gate-card');
  const verdictEl = document.getElementById('gate-verdict');
  const metaEl = document.getElementById('gate-meta');
  if (!card || !verdictEl || !metaEl) return;
  const level = gate && gate.level ? gate.level : null;
  const labels = { 1: 'Fully Scooped', 2: 'Largely Scooped', 3: 'Partial Overlap', 4: 'Mostly Novel', 5: 'Fully Novel' };
  const backed = gate ? gate.llm_backed !== false : true;
  verdictEl.innerHTML = level
    ? `<span class="verdict-${level}">${level}/5 — ${labels[level] || ''}</span>`
      + (backed ? '' : ' <span class="gate-fallback-note">(baseline fallback — treat with caution)</span>')
    : 'Novelty gate tripped';
  const top = gate && gate.scored
    ? gate.scored.filter(s => s.overlap_score >= 2).slice(0, 3) : [];
  metaEl.innerHTML =
    `<div>${escapeHtml(gate && gate.summary ? gate.summary : (fallbackMsg || 'Strong prior-art overlap found.'))}</div>`
    + (top.length ? `<div class="gate-hits">Top overlaps: ${top.map(s => `paper #${s.paper_index} (${s.overlap_score}/4)`).join(', ')}</div>` : '')
    + `<div class="gate-hint">Revise the idea, or build anyway — your call.</div>`;
  card.hidden = false;
  card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

async function overrideGate() {
  const run = getSelectedRun();
  if (!run) return;
  const btn = document.getElementById('gate-build-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Resuming'; }
  try {
    await API.gateOverride(run.run_id);
    hideGateCard();
    await loadRuns(); startPolling(); pollGateForSelected();
  } catch (e) {
    console.error('Gate override failed:', e);
    alert('Could not override: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Build anyway'; }
  }
}

function reviseFromGate() {
  // Drop the user back at the idea box with the current text selected.
  hideGateCard();
  const inp = document.getElementById('scoop-problem');
  if (inp) { inp.focus(); inp.select(); }
  if (inp) inp.scrollIntoView({ behavior: 'smooth', block: 'center' });
}

async function runScoop() {
  // Legacy standalone check (kept for trial-mode compat): delegates to the
  // unified flow so there is exactly one novelty path.
  return runUnified();
  const raw = document.getElementById('scoop-problem').value.trim();
  if (!raw) {
    document.getElementById('scoop-problem').style.borderColor = 'var(--red)';
    setTimeout(() => { document.getElementById('scoop-problem').style.borderColor = ''; }, 800);
    return;
  }
  if (!checkTrialGate()) return;
  // One box in, two fields out: first sentence (or up to 200 chars)
  // becomes the problem context; the whole text is the novelty claim.
  // Decomposition into problem/novelty happens server-side (Step 1).
  const firstStop = raw.search(/[.!?\n]/);
  const problem = (firstStop > 10 ? raw.slice(0, firstStop + 1) : raw.slice(0, 200)).trim();
  const novelty = raw;
  const btn = document.getElementById('scoop-btn');
  btn.disabled = true; btn.textContent = 'Checking';
  const progress = document.getElementById('scoop-progress'); progress.hidden = false;
  const statusText = document.getElementById('scoop-status-text');
  const STEPS = ['Decompose', 'Search', 'Triage', 'Identify', 'Deep Dive', 'Verdict', 'Summarize'];
  updateScoopSteps(0, STEPS); statusText.textContent = 'Decompose';
  try {
    const result = await API.scoopStart(problem, novelty);
    if (!currentUser) consumeTrial();
    const scoopId = result.scoop_id;
    if (scoopTimer) clearInterval(scoopTimer);
    scoopTimer = setInterval(async () => {
      try {
        const status = await API.scoopStatus(scoopId);
        const step = status.step || 0; const stepIdx = Math.min(step, STEPS.length);
        updateScoopSteps(stepIdx, STEPS);
        if (stepIdx > 0 && stepIdx <= STEPS.length) statusText.textContent = STEPS[Math.min(stepIdx - 1, STEPS.length - 1)];
        // Backend status vocab (backend/routers/scoop.py + run_scoop.py):
        // in-progress: started|running|decomposing|searching|triaging|
        //   identifying|diving|verdict|summarizing|pending (+ step_name mirror)
        // terminal-success: done|complete|completed
        // terminal-failure: failed*|error*
        // Anything terminal must unstick the button — never leave 'Checking'.
        const st = String(status.status || '').toLowerCase();
        const isDone = st === 'done' || st === 'complete' || st === 'completed';
        const isFailed = st === 'failed' || st.startsWith('failed') || st === 'error' || st.startsWith('error');
        if (isDone) {
          clearInterval(scoopTimer); scoopTimer = null;
          btn.disabled = false; btn.textContent = 'Check novelty';
          statusText.textContent = 'Complete';
          if (status.result) renderScoopResult(status.result);
          else statusText.textContent = 'Complete (no result)';
        } else if (isFailed) {
          clearInterval(scoopTimer); scoopTimer = null;
          btn.disabled = false; btn.textContent = 'Check novelty'; statusText.textContent = 'Failed';
          // Backend honesty-fallback may still ship an inconclusive
          // result payload on failure — render it instead of blank.
          if (status.result) renderScoopResult(status.result);
          else renderScoopError(status.error || status.detail || 'The novelty check failed before producing a result.');
        }
      } catch (e) { console.warn('scoop poll:', e); }
    }, 2000);
  } catch (e) {
    console.error('Scoop start failed:', e);
    btn.disabled = false; btn.textContent = 'Check novelty'; statusText.textContent = 'Start failed';
    renderScoopError(e.message || 'Could not start the novelty check.');
  }
}

// Scoop failure panel: full error text (never truncated, never stranded)
// with a copy button and a retry CTA that re-runs the check. Moves focus
// to the panel so keyboard/screen-reader users land on the failure.
function renderScoopError(message) {
  const el = document.getElementById('scoop-result');
  if (!el) return;
  el.hidden = false;
  const full = String(message || 'Unknown error');
  el.innerHTML = `
    <div class="scoop-error-panel" role="alert">
      <h3>Scoop check failed</h3>
      <p>${escapeHtml(full)}</p>
      <div class="scoop-error-actions">
        <button class="btn btn-secondary btn-sm" id="scoop-error-copy">Copy error</button>
        <button class="btn btn-primary btn-sm" onclick="runScoop()">Retry check</button>
        <button class="btn btn-secondary btn-sm" onclick="switchMode('idea')">Try the full pipeline instead</button>
      </div>
    </div>
  `;
  const copyBtn = document.getElementById('scoop-error-copy');
  if (copyBtn) copyBtn.onclick = () => copyPhaseErrorText(full, copyBtn);
  el.focus({ preventScroll: true });
  el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

// Clipboard helper shared by scoop + phase errors (copy button feedback).
async function copyPhaseErrorText(text, btn) {
  try {
    await navigator.clipboard.writeText(String(text || ''));
  } catch (e) {
    console.warn('clipboard copy failed:', e);
  }
  if (btn) {
    const orig = btn.textContent;
    btn.textContent = 'Copied';
    setTimeout(() => { btn.textContent = orig; }, 1500);
  }
}

function updateScoopSteps(activeIdx, steps) {
  const el = document.getElementById('scoop-steps-bar');
  let html = '';
  steps.forEach((s, i) => {
    const cls = i < activeIdx ? 'done' : i === activeIdx ? 'active' : 'pending';
    const stateLbl = i < activeIdx ? 'completed' : i === activeIdx ? 'in progress' : 'pending';
    html += `<div class="scoop-step" role="listitem" aria-label="Step ${i + 1} of ${steps.length}: ${escapeAttr(s)}, ${stateLbl}"><div class="scoop-step-dot ${cls}" aria-hidden="true">${i < activeIdx ? '✓' : i === activeIdx ? '●' : i+1}</div><div class="scoop-step-label">${s}</div></div>`;
  });
  el.innerHTML = html;
}

function renderScoopResult(result) {
  const el = document.getElementById('scoop-result'); el.hidden = false;
  const level = Number(result.level) || 0;
  const isInconclusive = level === 0 || result.verdict === 'inconclusive' || result.recommendation === 'inconclusive';
  const safeLevel = [1, 2, 3, 4, 5].includes(level) && !isInconclusive ? level : 0;
  const levelClass = `verdict-${safeLevel}`;
  const levelLabels = { 0:'Inconclusive — insufficient literature', 1:'Fully Scooped', 2:'Largely Scooped', 3:'Partial Overlap', 4:'Mostly Novel', 5:'Fully Novel' };
  const headline = isInconclusive ? 'Inconclusive — insufficient literature' : `${safeLevel}/5 — ${levelLabels[safeLevel] || 'Unknown'}`;
  const candidates = (result.top_candidates || []).slice(0, 5);
  const perAxis = result.per_axis || {};
  el.innerHTML = `
    <div class="verdict-large ${levelClass}">${escapeHtml(headline)}</div>
    ${result.summary ? `<div class="verdict-meta">${escapeHtml(result.summary)}</div>` : ''}
    <div class="axis-grid">${Object.entries(perAxis).map(([name, val]) => {
      const scoreCls = val === 'clear' ? 'clear' : val === 'partial' ? 'partial' : val === 'unknown' ? 'unknown' : 'scooped';
      const scoreLbl = val === 'clear' ? 'Clear' : val === 'partial' ? 'Partial' : val === 'unknown' ? 'Unknown' : 'Scooped';
      return `<div class="axis-card"><div class="axis-name">${escapeHtml(name)}</div><div class="axis-score ${scoreCls}">${scoreLbl}</div></div>`;
    }).join('')}</div>
    ${candidates.length ? `<div class="candidate-list"><div style="font-size:11px;color:var(--text3);margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px;">Top Candidates</div>
      ${candidates.map(c => `<div class="candidate-item"><span class="candidate-score">${escapeHtml(c.overlap_score)}/4</span><span class="candidate-title">${escapeHtml(c.title)}</span></div>`).join('')}</div>` : ''}
    ${result.recommendation ? `<div style="margin-top:12px;font-size:13px;">Recommendation: <strong style="color:var(--accent-text)">${escapeHtml(result.recommendation)}</strong></div>` : ''}
  `;
  // Move focus to the result so keyboard/screen-reader users land on it.
  el.focus({ preventScroll: true });
}

function scrollToForm() { document.querySelector('.hero-card').scrollIntoView({ behavior: 'smooth' }); }
// Empty-state CTAs: focus the right input and switch to the right mode.
function startScoopCTA() {
  switchMode('scoop');
  const inp = document.getElementById('scoop-problem');
  if (inp) { inp.focus(); inp.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
}
function startPipelineCTA() {
  switchMode('idea');
  const inp = document.getElementById('idea-query');
  if (inp) { inp.focus(); inp.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
}
function closeModal() {
  const modal = document.getElementById('modal');
  if (modal) {
    modal.classList.remove('open');
    if (modal._restoreFocus && modal._restoreFocus.focus) modal._restoreFocus.focus();
  }
}
function openModal(title, content) {
  document.getElementById('modal-title').textContent = title || 'Details';
  document.getElementById('modal-content').textContent = typeof content === 'string' ? content : JSON.stringify(content, null, 2);
  const modal = document.getElementById('modal');
  modal._restoreFocus = document.activeElement;
  modal.classList.add('open');
  const closeBtn = modal.querySelector('.modal-close');
  if (closeBtn) closeBtn.focus();
}

// Escape closes any open modal and returns focus to its opener.
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  for (const id of ['artifact-modal', 'billing-modal', 'modal']) {
    const m = document.getElementById(id);
    if (m && (m.classList.contains('open') || m.style.display === 'flex')) {
      if (id === 'artifact-modal') closeArtifactModal();
      else if (id === 'modal') closeModal();
      else hideBilling();
      e.stopPropagation();
      break;
    }
  }
});

// Arrow-key navigation across stepper phase buttons (listbox pattern):
// Left/Up = previous phase, Right/Down = next, Home/End = ends, Enter opens.
document.addEventListener('keydown', (e) => {
  const node = e.target && e.target.closest ? e.target.closest('#stepper-bar .stepper-step-node') : null;
  if (!node) return;
  const bar = document.getElementById('stepper-bar');
  const nodes = bar ? [...bar.querySelectorAll('.stepper-step-node')] : [];
  const i = nodes.indexOf(node);
  if (['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp', 'Home', 'End'].includes(e.key)) {
    e.preventDefault();
    let j = i;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') j = Math.min(nodes.length - 1, i + 1);
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') j = Math.max(0, i - 1);
    else if (e.key === 'Home') j = 0;
    else j = nodes.length - 1;
    if (nodes[j]) nodes[j].focus();
  }
  // Enter/Space natively activates the <button>; no custom handler needed.
});