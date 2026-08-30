"""Server-rendered approval UI. One page, vanilla JS polling /incidents."""
from __future__ import annotations

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Self-Healing SRE Agent — Incidents</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 14px/1.5 system-ui, sans-serif; margin: 0; background: #0f1117; color: #e6e6e6; }
  header { padding: 16px 24px; border-bottom: 1px solid #262b36; display: flex; align-items: baseline; gap: 12px; }
  header h1 { font-size: 16px; margin: 0; }
  header .sub { color: #8b93a1; font-size: 12px; }
  main { padding: 24px; max-width: 1100px; margin: 0 auto; }
  .incident { border: 1px solid #262b36; border-radius: 10px; padding: 16px 18px; margin-bottom: 16px; background: #151923; }
  .row { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
  .badge { font-size: 11px; font-weight: 600; padding: 2px 8px; border-radius: 999px; text-transform: uppercase; letter-spacing: .04em; }
  .OPEN { background: #3b2f00; color: #ffd466; }
  .APPROVED { background: #0d3b2e; color: #5be3b8; }
  .REJECTED { background: #3b1d1d; color: #ff9a9a; }
  .RESOLVED { background: #123d1e; color: #7ee787; }
  .ESCALATED { background: #3b1d00; color: #ffb066; }
  h2 { font-size: 14px; margin: 8px 0 4px; }
  .diag { color: #cdd3dd; margin: 6px 0 10px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 8px; margin: 8px 0; }
  .metric { background: #0f1117; border: 1px solid #262b36; border-radius: 8px; padding: 8px 10px; }
  .metric b { display: block; font-size: 16px; }
  .metric span { color: #8b93a1; font-size: 11px; }
  ul.actions { margin: 6px 0; padding-left: 18px; }
  code { background: #0f1117; padding: 1px 5px; border-radius: 4px; }
  button { font: inherit; border: 0; border-radius: 8px; padding: 8px 16px; cursor: pointer; font-weight: 600; }
  .approve { background: #1f8f5f; color: #fff; }
  .reject { background: #a83232; color: #fff; margin-left: 8px; }
  button:disabled { opacity: .4; cursor: default; }
  .empty { color: #8b93a1; text-align: center; padding: 48px; }
  .ts { color: #6b7280; font-size: 11px; }
</style>
</head>
<body>
<header>
  <h1>🩺 Self-Healing SRE Agent</h1>
  <span class="sub">incident approval console · auto-refresh 2s</span>
</header>
<main id="app"><p class="empty">loading…</p></main>
<script>
const fmtPct = v => (v * 100).toFixed(1) + '%';
const fmtMs  = v => (v * 1000).toFixed(0) + ' ms';
const fmtRps = v => (v).toFixed(1) + ' rps';

async function decide(id, approved) {
  const verb = approved ? 'approve' : 'reject';
  document.querySelectorAll('button[data-inc="'+id+'"]').forEach(b => b.disabled = true);
  await fetch(`/incidents/${id}/${verb}`, { method: 'POST' });
  load();
}

function metric(label, value) {
  return `<div class="metric"><b>${value}</b><span>${label}</span></div>`;
}

function render(incidents) {
  const app = document.getElementById('app');
  if (!incidents.length) { app.innerHTML = '<p class="empty">No incidents yet. The agent is watching.</p>'; return; }
  app.innerHTML = incidents.map(i => {
    const s = i.signals || {}, b = i.baseline || {};
    const rec = i.recommendation || {};
    const actions = (rec.actions || []).map(a =>
      `<li><code>${a.id}</code> <span class="ts">(${a.executor})</span> — ${a.summary}</li>`).join('');
    const controls = i.status === 'OPEN'
      ? `<div><button class="approve" data-inc="${i.id}" onclick="decide('${i.id}',true)">Approve</button>
         <button class="reject" data-inc="${i.id}" onclick="decide('${i.id}',false)">Reject</button></div>`
      : '';
    const vr = i.verify_result ? `<p class="ts">verify: ${i.verify_result.ok ? 'recovered' : 'NOT recovered'} after ${i.verify_result.attempts} check(s)</p>` : '';
    return `<div class="incident">
      <div class="row">
        <div><strong>${i.id}</strong> · ${i.anomaly}
          <span class="badge ${i.status}">${i.status}</span></div>
        <span class="ts">${i.created_at}</span>
      </div>
      <p class="diag">${(i.diagnosis || {}).summary || ''}</p>
      <div class="grid">
        ${metric('throughput (base ' + fmtRps(b.throughput_rps || 0) + ')', fmtRps(s.throughput_rps || 0))}
        ${metric('error rate', fmtPct(s.error_rate || 0))}
        ${metric('p95 (base ' + fmtMs(b.p95_seconds || 0) + ')', fmtMs(s.p95_seconds || 0))}
        ${metric('DB span share', fmtPct((i.trace_summary || {}).db_fraction || 0))}
      </div>
      <h2>Recommended remediation <span class="ts">(${i.recommendation_source || rec.source || 'rules'})</span></h2>
      <ul class="actions">${actions}</ul>
      <p class="diag">${rec.rationale || ''}</p>
      ${vr}
      ${controls}
    </div>`;
  }).join('');
}

async function load() {
  try {
    const r = await fetch('/incidents');
    render(await r.json());
  } catch (e) { /* keep last render */ }
}
load();
setInterval(load, 2000);
</script>
</body>
</html>
"""
