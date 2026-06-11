/* JobHunter — frontend app */

const STAGE_RANK = {
  applied: 1, phone_screen: 2, interview: 3,
  technical_assessment: 4, hr_interview: 5, offer: 6,
};

const STAGE_LABELS = {
  applied: 'Applied',
  phone_screen: 'Phone Screen',
  interview: 'Interview',
  technical_assessment: 'Technical Assessment',
  hr_interview: 'HR Interview',
  offer: 'Offer',
  rejected: 'Rejected',
  declined_offer: 'Declined Offer',
  ghosted: 'Ghosted',
};

const FUNNEL_STAGES = [
  'applied', 'phone_screen', 'interview',
  'technical_assessment', 'hr_interview', 'offer',
];

const STAGE_COLORS = [
  '#6c63ff', '#a29bfe', '#00cec9', '#00b894',
  '#fdcb6e', '#e17aff', '#51cf66',
];

const TERMINAL_COLORS = {
  rejected: '#ff5c5c',
  declined_offer: '#ffa94d',
  ghosted: '#636e72',
};

// ── State ─────────────────────────────────────────────────────────────────────

let state = {
  apps: [],
  stats: null,
  settings: {},
  currentApp: null,
  editingAppId: null,
  filterStage: '',
  filterCompany: '',
  sortCol: 'stage',
  sortDir: 'asc',
};

// ── Init ──────────────────────────────────────────────────────────────────────

async function init() {
  const params = new URLSearchParams(window.location.search);
  if (params.has('auth_error')) {
    toast('Google sign-in failed — please try connecting again', 'error');
    history.replaceState(null, '', '/');
  }
  await checkAuth();
  await Promise.all([loadSettings(), loadApps(), loadStats()]);
  renderStats();
  renderFunnel();
  renderWeekly();
  renderTable();
}

// ── Auth ──────────────────────────────────────────────────────────────────────

async function checkAuth() {
  const res = await fetch('/api/auth/status');
  const data = await res.json();
  const banner = document.getElementById('auth-banner');
  // Always show the dashboard; banner is an info notice only
  document.querySelector('.main-content').style.display = 'flex';
  if (data.authenticated) {
    banner.style.display = 'none';
  } else {
    banner.style.display = 'flex';
    if (!data.credentials_ready) {
      document.getElementById('auth-msg').textContent =
        'credentials/credentials.json not found. Follow the setup guide in README.md to connect Google.';
      document.getElementById('auth-connect-btn').style.display = 'none';
    }
  }
}

document.getElementById('auth-connect-btn')?.addEventListener('click', async () => {
  const res = await fetch('/api/auth/url');
  if (!res.ok) { toast('Setup credentials first — see README', 'error'); return; }
  const { url } = await res.json();
  window.location.href = url;
});

// ── Load data ──────────────────────────────────────────────────────────────────

async function loadApps() {
  const params = new URLSearchParams();
  if (state.filterStage) params.set('stage', state.filterStage);
  if (state.filterCompany) params.set('company', state.filterCompany);
  const res = await fetch(`/api/applications?${params}`);
  state.apps = await res.json();
}

async function loadStats() {
  const res = await fetch('/api/stats');
  state.stats = await res.json();
}

async function loadSettings() {
  const res = await fetch('/api/settings');
  state.settings = await res.json();
}

// ── Render Stats ──────────────────────────────────────────────────────────────

function renderStats() {
  const s = state.stats;
  if (!s) return;
  document.getElementById('stat-total').textContent = s.total;
  document.getElementById('stat-active').textContent = s.active;
  document.getElementById('stat-phone-screen').textContent = s.phone_screen_rate + '%';
  document.getElementById('stat-interview').textContent = s.interview_rate + '%';
  document.getElementById('stat-offer').textContent = s.offer_rate + '%';
  document.getElementById('stat-screen').textContent =
    s.screen_to_interview_rate != null ? s.screen_to_interview_rate + '%' : '—';
  document.getElementById('stat-referral').textContent =
    s.referral_interview_rate != null ? s.referral_interview_rate + '%' : '—';
}

function renderWeekly() {
  const container = document.getElementById('weekly-chart');
  const s = state.stats;
  if (!s || !s.weekly_volume) return;

  const weeks = s.weekly_volume;
  const maxCount = Math.max(...weeks.map(w => w.count), 1);
  const maxBarH = 70;

  container.innerHTML = weeks.map((w, i) => {
    const barH = w.count > 0 ? Math.max(3, Math.round(w.count / maxCount * maxBarH)) : 0;
    const isCurrent = i === weeks.length - 1;
    const [y, m, d] = w.week.split('-').map(Number);
    const label = new Date(y, m - 1, d).toLocaleDateString('en-GB', { month: 'short', day: 'numeric' });
    return `
      <div class="wcol" title="${w.count} application${w.count !== 1 ? 's' : ''} — week of ${label}">
        <div class="wcount">${w.count > 0 ? w.count : ''}</div>
        <div class="wbar${isCurrent ? ' is-current' : ''}" style="height:${barH}px"></div>
        <div class="wlabel">${label}</div>
      </div>
    `;
  }).join('');
}

// ── Render Funnel ─────────────────────────────────────────────────────────────

function renderFunnel() {
  const container = document.getElementById('funnel-rows');
  const s = state.stats;
  if (!s || s.total === 0) {
    container.innerHTML = '<p style="color:var(--muted);font-size:13px;">No data yet. Sync emails or add applications.</p>';
    return;
  }

  container.innerHTML = '';

  FUNNEL_STAGES.forEach((stage, i) => {
    const reached = s.reached_by_stage?.[stage] || 0;
    const pct = s.total ? Math.round(reached / s.total * 100) : 0;
    const color = STAGE_COLORS[i] || '#6c63ff';

    const row = document.createElement('div');
    row.className = 'funnel-row';
    row.innerHTML = `
      <div class="funnel-label">${STAGE_LABELS[stage]}</div>
      <div class="funnel-bar-track">
        <div class="funnel-bar-fill" style="width:${pct}%;background:${color}"></div>
      </div>
      <div class="funnel-count">${reached}</div>
      <div class="funnel-pct">${pct}%</div>
    `;
    container.appendChild(row);
  });

  // Terminal stages — these are final states so current count = reached count
  const terminalTotal = (s.by_stage?.rejected || 0) + (s.by_stage?.declined_offer || 0) + (s.by_stage?.ghosted || 0);
  if (terminalTotal > 0) {
    const divider = document.createElement('div');
    divider.style.cssText = 'border-top:1px solid var(--border);margin:12px 0 10px';
    container.appendChild(divider);

    ['rejected', 'declined_offer', 'ghosted'].forEach(stage => {
      const count = s.by_stage?.[stage] || 0;
      if (!count) return;
      const pct = s.total ? Math.round(count / s.total * 100) : 0;
      const row = document.createElement('div');
      row.className = 'funnel-row';
      row.innerHTML = `
        <div class="funnel-label">${STAGE_LABELS[stage]}</div>
        <div class="funnel-bar-track">
          <div class="funnel-bar-fill" style="width:${pct}%;background:${TERMINAL_COLORS[stage]}"></div>
        </div>
        <div class="funnel-count">${count}</div>
        <div class="funnel-pct">${pct}%</div>
      `;
      container.appendChild(row);
    });
  }
}

// ── Render Table ──────────────────────────────────────────────────────────────

let _selectedIds = new Set();

const TERMINAL_STAGES = new Set(['rejected', 'declined_offer', 'ghosted']);

function daysAgo(dateStr) {
  if (!dateStr) return null;
  const diff = Date.now() - new Date(dateStr).getTime();
  return Math.floor(diff / 86400000);
}

function sortedApps() {
  const col = state.sortCol;
  const dir = state.sortDir === 'asc' ? 1 : -1;

  return [...state.apps].sort((a, b) => {
    let av, bv;
    if (col === 'stage') {
      av = STAGE_RANK[a.stage] ?? 99;
      bv = STAGE_RANK[b.stage] ?? 99;
    } else if (col === 'applied_date' || col === 'last_updated') {
      av = a[col] ? new Date(a[col]).getTime() : 0;
      bv = b[col] ? new Date(b[col]).getTime() : 0;
    } else {
      av = (a[col] || '').toString().toLowerCase();
      bv = (b[col] || '').toString().toLowerCase();
    }
    if (av < bv) return -dir;
    if (av > bv) return dir;
    return 0;
  });
}

function updateSortHeaders() {
  document.querySelectorAll('th.sortable').forEach(th => {
    th.classList.remove('sort-asc', 'sort-desc');
    if (th.dataset.col === state.sortCol) {
      th.classList.add(state.sortDir === 'asc' ? 'sort-asc' : 'sort-desc');
    }
  });
}

function renderTable() {
  const tbody = document.getElementById('app-tbody');
  updateSortHeaders();

  if (!state.apps.length) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="7">No applications found. Sync emails or add one manually.</td></tr>`;
    updateMergeBtn();
    return;
  }

  const sorted = sortedApps();

  // Count how many positions each company has
  const companyCounts = {};
  sorted.forEach(a => {
    const key = a.company.toLowerCase();
    companyCounts[key] = (companyCounts[key] || 0) + 1;
  });

  tbody.innerHTML = sorted.map(app => {
    const isMulti = companyCounts[app.company.toLowerCase()] > 1;
    const companyCell = isMulti
      ? `<strong>${esc(app.company)}</strong> <span class="multi-pos-badge" title="${companyCounts[app.company.toLowerCase()]} positions tracked">${companyCounts[app.company.toLowerCase()]}</span>`
      : `<strong>${esc(app.company)}</strong>`;
    return `
    <tr data-id="${app.id}" class="${_selectedIds.has(app.id) ? 'row-selected' : ''}">
      <td onclick="event.stopPropagation()">
        <input type="checkbox" class="row-cb" data-id="${app.id}" ${_selectedIds.has(app.id) ? 'checked' : ''}>
      </td>
      <td>${companyCell}</td>
      <td>${esc(app.role || '—')}</td>
      <td><span class="badge badge-${app.stage}">${STAGE_LABELS[app.stage] || app.stage}</span></td>
      <td>${fmtDate(app.applied_date)}</td>
      <td>${fmtDate(app.last_updated)}</td>
      <td style="color:var(--muted);text-transform:capitalize">${app.source}</td>
    </tr>
  `;
  }).join('');

  tbody.querySelectorAll('tr').forEach(tr => {
    tr.addEventListener('click', () => openDrawer(parseInt(tr.dataset.id)));
  });

  tbody.querySelectorAll('.row-cb').forEach(cb => {
    cb.addEventListener('change', e => {
      const id = parseInt(e.target.dataset.id);
      if (e.target.checked) _selectedIds.add(id);
      else _selectedIds.delete(id);
      const tr = e.target.closest('tr');
      tr.classList.toggle('row-selected', e.target.checked);
      updateMergeBtn();
    });
  });
}

function updateMergeBtn() {
  const btn = document.getElementById('merge-btn');
  const n = _selectedIds.size;
  btn.style.display = n >= 2 ? 'inline-flex' : 'none';
  btn.textContent = n >= 2 ? `Merge ${n} Selected` : 'Merge Selected';
}

// Select-all checkbox
document.getElementById('select-all-cb')?.addEventListener('change', e => {
  const checked = e.target.checked;
  _selectedIds = checked ? new Set(state.apps.map(a => a.id)) : new Set();
  renderTable();
  updateMergeBtn();
});

// ── Merge ─────────────────────────────────────────────────────────────────────

document.getElementById('merge-btn')?.addEventListener('click', () => {
  const selected = state.apps.filter(a => _selectedIds.has(a.id));
  const sel = document.getElementById('merge-keep-select');
  sel.innerHTML = selected.map(a =>
    `<option value="${a.id}">${esc(a.company)}${a.role ? ' — ' + esc(a.role) : ''} [${STAGE_LABELS[a.stage] || a.stage}]</option>`
  ).join('');
  // Default: pre-select the most advanced stage
  const ranked = [...selected].sort((a, b) => {
    const ra = STAGE_RANK[a.stage] || 0;
    const rb = STAGE_RANK[b.stage] || 0;
    return rb - ra;
  });
  if (ranked.length) sel.value = ranked[0].id;
  updateMergeSummary(selected);
  sel.addEventListener('change', () => updateMergeSummary(selected));
  document.getElementById('merge-modal-overlay').classList.add('open');
});

function updateMergeSummary(selected) {
  const keepId = parseInt(document.getElementById('merge-keep-select').value);
  const toDelete = selected.filter(a => a.id !== keepId);
  document.getElementById('merge-summary').textContent =
    `Will delete: ${toDelete.map(a => a.company).join(', ')} — their history moves to the kept record.`;
}

document.getElementById('merge-cancel')?.addEventListener('click', () => {
  document.getElementById('merge-modal-overlay').classList.remove('open');
});

document.getElementById('merge-modal-overlay')?.addEventListener('click', e => {
  if (e.target === e.currentTarget) document.getElementById('merge-modal-overlay').classList.remove('open');
});

document.getElementById('merge-confirm')?.addEventListener('click', async () => {
  const selected = state.apps.filter(a => _selectedIds.has(a.id));
  const keepId = parseInt(document.getElementById('merge-keep-select').value);
  const mergeIds = selected.filter(a => a.id !== keepId).map(a => a.id);

  const res = await fetch('/api/applications/merge', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ keep_id: keepId, merge_ids: mergeIds }),
  });

  document.getElementById('merge-modal-overlay').classList.remove('open');

  if (!res.ok) { toast('Merge failed', 'error'); return; }
  toast(`Merged ${selected.length} applications`, 'success');
  _selectedIds.clear();
  document.getElementById('select-all-cb').checked = false;
  updateMergeBtn();
  await Promise.all([loadApps(), loadStats()]);
  renderTable(); renderStats(); renderFunnel(); renderWeekly();
});

// ── Sort headers ──────────────────────────────────────────────────────────────

document.querySelectorAll('th.sortable').forEach(th => {
  th.addEventListener('click', () => {
    const col = th.dataset.col;
    if (state.sortCol === col) {
      state.sortDir = state.sortDir === 'asc' ? 'desc' : 'asc';
    } else {
      state.sortCol = col;
      state.sortDir = col === 'days_at_stage' ? 'desc' : 'asc';
    }
    renderTable();
  });
});

// ── Filters ───────────────────────────────────────────────────────────────────

document.getElementById('filter-stage')?.addEventListener('change', async e => {
  state.filterStage = e.target.value;
  await loadApps();
  renderTable();
});

let _searchDebounce = null;
document.getElementById('filter-company')?.addEventListener('input', e => {
  clearTimeout(_searchDebounce);
  _searchDebounce = setTimeout(async () => {
    state.filterCompany = e.target.value;
    await loadApps();
    renderTable();
  }, 250);
});

// ── Drawer ────────────────────────────────────────────────────────────────────

async function openDrawer(appId) {
  const app = state.apps.find(a => a.id === appId);
  if (!app) return;
  state.currentApp = app;

  // Find other tracked positions at the same company
  state.siblingApps = state.apps.filter(
    a => a.company.toLowerCase() === app.company.toLowerCase() && a.id !== appId
  );

  document.getElementById('drawer-company').textContent = app.company;
  document.getElementById('drawer-role').textContent = app.role || '—';
  document.getElementById('drawer-stage-badge').className = `badge badge-${app.stage}`;
  document.getElementById('drawer-stage-badge').textContent = STAGE_LABELS[app.stage] || app.stage;
  document.getElementById('drawer-applied').textContent = fmtDate(app.applied_date) || '—';
  document.getElementById('drawer-source').textContent = app.source;
  document.getElementById('drawer-refer-btn').textContent =
    app.source === 'referral' ? 'Unmark Referral' : 'Mark as Referred';
  document.getElementById('drawer-url').innerHTML = app.job_url
    ? `<a href="${esc(app.job_url)}" target="_blank" style="color:var(--accent)">${esc(app.job_url)}</a>`
    : '—';
  document.getElementById('drawer-notes').textContent = app.notes || '—';

  // Populate move-stage select
  const sel = document.getElementById('move-stage-select');
  const allStages = Object.keys(STAGE_LABELS);
  sel.innerHTML = allStages.map(s =>
    `<option value="${s}" ${s === app.stage ? 'selected' : ''}>${STAGE_LABELS[s]}</option>`
  ).join('');

  // Load timeline
  await loadTimeline(appId);

  document.getElementById('overlay').classList.add('open');
  document.getElementById('drawer').classList.add('open');
}

async function loadTimeline(appId) {
  const res = await fetch(`/api/applications/${appId}/events`);
  const events = await res.json();
  const container = document.getElementById('timeline-list');
  if (!events.length) {
    container.innerHTML = '<p style="color:var(--muted);font-size:12px">No events recorded yet.</p>';
    return;
  }

  const siblings = state.siblingApps || [];

  const stageOptions = Object.entries(STAGE_LABELS)
    .map(([v, l]) => `<option value="${v}">${l}</option>`)
    .join('');

  container.innerHTML = events.map(ev => `
    <div class="timeline-item">
      <div class="tl-content">
        <div class="tl-stage-row" style="display:flex;align-items:center;gap:6px">
          <select class="stage-edit-select filter-select" data-event-id="${ev.id}" title="Change stage classification" style="font-size:11px;padding:2px 6px;height:auto;font-weight:600;flex:1">
            ${Object.entries(STAGE_LABELS).map(([v, l]) => `<option value="${v}" ${v === ev.stage ? 'selected' : ''}>${l}</option>`).join('')}
          </select>
          <button class="event-delete-btn btn btn-ghost" data-event-id="${ev.id}" title="Delete this event" style="padding:1px 6px;font-size:12px;color:var(--danger);line-height:1">×</button>
        </div>
        <div class="tl-subject">${esc(ev.subject || '')}</div>
        <div class="tl-date">${fmtDateTime(ev.event_date || ev.created_at)}</div>
        <div class="tl-type">${ev.event_type}</div>
        ${siblings.length > 0 ? `
          <div style="margin-top:4px">
            <select class="reassign-select filter-select" data-event-id="${ev.id}" style="font-size:11px;padding:2px 6px;height:auto">
              <option value="">Move to position…</option>
              ${siblings.map(s => `<option value="${s.id}">${esc(s.role || s.company)} [${STAGE_LABELS[s.stage] || s.stage}]</option>`).join('')}
            </select>
          </div>
        ` : ''}
      </div>
    </div>
  `).join('');

  container.querySelectorAll('.stage-edit-select').forEach(sel => {
    sel.addEventListener('change', async () => {
      const eventId = parseInt(sel.dataset.eventId);
      const newStage = sel.value;
      const res = await fetch(`/api/events/${eventId}/stage`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ stage: newStage }),
      });
      if (!res.ok) { toast('Stage update failed', 'error'); return; }

      toast(`Event reclassified as "${STAGE_LABELS[newStage]}"`, 'success');
      await Promise.all([loadApps(), loadStats()]);
      renderTable(); renderStats(); renderFunnel(); renderWeekly();

      const refreshed = state.apps.find(a => a.id === appId);
      if (refreshed) {
        state.currentApp = refreshed;
        document.getElementById('drawer-stage-badge').className = `badge badge-${refreshed.stage}`;
        document.getElementById('drawer-stage-badge').textContent = STAGE_LABELS[refreshed.stage] || refreshed.stage;
        const moveSelect = document.getElementById('move-stage-select');
        if (moveSelect) moveSelect.value = refreshed.stage;
      }
    });
  });

  container.querySelectorAll('.event-delete-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!confirm('Delete this event from the history?')) return;
      const eventId = parseInt(btn.dataset.eventId);
      const res = await fetch(`/api/events/${eventId}`, { method: 'DELETE' });
      if (!res.ok) { toast('Delete failed', 'error'); return; }
      toast('Event deleted', 'success');
      await Promise.all([loadApps(), loadStats()]);
      renderTable(); renderStats(); renderFunnel(); renderWeekly();
      const refreshed = state.apps.find(a => a.id === appId);
      if (refreshed) {
        state.currentApp = refreshed;
        document.getElementById('drawer-stage-badge').className = `badge badge-${refreshed.stage}`;
        document.getElementById('drawer-stage-badge').textContent = STAGE_LABELS[refreshed.stage] || refreshed.stage;
      }
      await loadTimeline(appId);
    });
  });

  container.querySelectorAll('.reassign-select').forEach(sel => {
    sel.addEventListener('change', async () => {
      const eventId = parseInt(sel.dataset.eventId);
      const targetAppId = parseInt(sel.value);
      if (!targetAppId) return;

      const target = state.siblingApps.find(s => s.id === targetAppId);
      const res = await fetch(`/api/events/${eventId}/reassign`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ application_id: targetAppId }),
      });
      if (!res.ok) { toast('Reassign failed', 'error'); sel.value = ''; return; }

      toast(`Event moved to "${target?.role || target?.company || 'other position'}"`, 'success');
      await Promise.all([loadApps(), loadStats()]);
      renderTable(); renderStats(); renderFunnel(); renderWeekly();

      // Refresh the drawer with updated data
      state.siblingApps = state.apps.filter(
        a => a.company.toLowerCase() === state.currentApp.company.toLowerCase() && a.id !== appId
      );
      const refreshed = state.apps.find(a => a.id === appId);
      if (refreshed) {
        state.currentApp = refreshed;
        document.getElementById('drawer-stage-badge').className = `badge badge-${refreshed.stage}`;
        document.getElementById('drawer-stage-badge').textContent = STAGE_LABELS[refreshed.stage] || refreshed.stage;
      }
      await loadTimeline(appId);
    });
  });
}

function closeDrawer() {
  document.getElementById('overlay').classList.remove('open');
  document.getElementById('drawer').classList.remove('open');
  state.currentApp = null;
}

document.getElementById('overlay')?.addEventListener('click', closeDrawer);
document.getElementById('drawer-close')?.addEventListener('click', closeDrawer);

// Populate add-event stage select
const addEventStageSelect = document.getElementById('add-event-stage');
if (addEventStageSelect) {
  Object.entries(STAGE_LABELS).forEach(([value, label]) => {
    const opt = document.createElement('option');
    opt.value = value; opt.textContent = label;
    addEventStageSelect.appendChild(opt);
  });
}

document.getElementById('add-event-btn')?.addEventListener('click', () => {
  const form = document.getElementById('add-event-form');
  form.style.display = form.style.display === 'none' ? 'block' : 'none';
});

document.getElementById('add-event-cancel')?.addEventListener('click', () => {
  document.getElementById('add-event-form').style.display = 'none';
  document.getElementById('add-event-notes').value = '';
  document.getElementById('add-event-date').value = '';
});

document.getElementById('add-event-save')?.addEventListener('click', async () => {
  const app = state.currentApp;
  if (!app) return;
  const stage = document.getElementById('add-event-stage').value;
  const notes = document.getElementById('add-event-notes').value.trim();
  const dateVal = document.getElementById('add-event-date').value;
  const body = { stage, notes: notes || null };
  if (dateVal) body.event_date = dateVal + 'T12:00:00';

  const res = await fetch(`/api/applications/${app.id}/log-event`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) { toast('Failed to add event', 'error'); return; }

  document.getElementById('add-event-form').style.display = 'none';
  document.getElementById('add-event-notes').value = '';
  document.getElementById('add-event-date').value = '';

  toast(`Logged: ${STAGE_LABELS[stage]}`, 'success');
  await Promise.all([loadApps(), loadStats()]);
  renderTable(); renderStats(); renderFunnel(); renderWeekly();
  const refreshed = state.apps.find(a => a.id === app.id);
  if (refreshed) {
    state.currentApp = refreshed;
    document.getElementById('drawer-stage-badge').className = `badge badge-${refreshed.stage}`;
    document.getElementById('drawer-stage-badge').textContent = STAGE_LABELS[refreshed.stage] || refreshed.stage;
    const moveSelect = document.getElementById('move-stage-select');
    if (moveSelect) moveSelect.value = refreshed.stage;
  }
  await loadTimeline(app.id);
});

document.getElementById('move-stage-btn')?.addEventListener('click', async () => {
  const app = state.currentApp;
  if (!app) return;
  const stage = document.getElementById('move-stage-select').value;
  const notes = document.getElementById('move-stage-notes').value.trim();

  const dateVal = document.getElementById('move-stage-date').value;
  const body = { stage, notes: notes || null };
  if (dateVal) body.event_date = dateVal + 'T12:00:00';

  const res = await fetch(`/api/applications/${app.id}/move-stage`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) { toast('Failed to move stage', 'error'); return; }

  toast(`Moved to ${STAGE_LABELS[stage]}`, 'success');
  document.getElementById('move-stage-notes').value = '';
  document.getElementById('move-stage-date').value = '';
  state.currentApp = { ...app, stage };
  document.getElementById('drawer-stage-badge').className = `badge badge-${stage}`;
  document.getElementById('drawer-stage-badge').textContent = STAGE_LABELS[stage] || stage;

  await Promise.all([loadApps(), loadStats(), loadTimeline(app.id)]);
  renderTable();
  renderStats();
  renderFunnel();
  renderWeekly();
});

document.getElementById('drawer-refer-btn')?.addEventListener('click', async () => {
  const app = state.currentApp;
  if (!app) return;
  const newSource = app.source === 'referral' ? 'manual' : 'referral';
  const res = await fetch(`/api/applications/${app.id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source: newSource }),
  });
  if (!res.ok) { toast('Update failed', 'error'); return; }
  state.currentApp = { ...app, source: newSource };
  document.getElementById('drawer-source').textContent = newSource;
  const btn = document.getElementById('drawer-refer-btn');
  btn.textContent = newSource === 'referral' ? 'Unmark Referral' : 'Mark as Referred';
  toast(newSource === 'referral' ? 'Marked as referral' : 'Unmarked referral', 'success');
  await loadApps();
  renderTable();
});

document.getElementById('drawer-split-btn')?.addEventListener('click', () => {
  if (!state.currentApp) return;
  document.getElementById('split-company').value = state.currentApp.company;
  document.getElementById('split-role').value = '';
  document.getElementById('split-date').value = '';
  document.getElementById('split-stage').value = 'applied';
  document.getElementById('split-modal-overlay').classList.add('open');
});

document.getElementById('split-cancel')?.addEventListener('click', () => {
  document.getElementById('split-modal-overlay').classList.remove('open');
});

document.getElementById('split-modal-overlay')?.addEventListener('click', e => {
  if (e.target === e.currentTarget) document.getElementById('split-modal-overlay').classList.remove('open');
});

document.getElementById('split-confirm')?.addEventListener('click', async () => {
  const role = document.getElementById('split-role').value.trim();
  if (!role) { toast('Role is required', 'error'); return; }

  const body = {
    company: document.getElementById('split-company').value,
    role,
    stage: document.getElementById('split-stage').value,
    applied_date: document.getElementById('split-date').value || null,
    source: 'manual',
  };

  const res = await fetch('/api/applications', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) { toast('Failed to create position', 'error'); return; }

  document.getElementById('split-modal-overlay').classList.remove('open');
  toast(`New position "${role}" created — open it to reassign emails`, 'success');
  await Promise.all([loadApps(), loadStats()]);
  renderTable(); renderStats(); renderFunnel(); renderWeekly();

  // Refresh siblings in current drawer
  if (state.currentApp) {
    state.siblingApps = state.apps.filter(
      a => a.company.toLowerCase() === state.currentApp.company.toLowerCase() && a.id !== state.currentApp.id
    );
    await loadTimeline(state.currentApp.id);
  }
});

// Populate split-stage select
const splitStageSelect = document.getElementById('split-stage');
if (splitStageSelect) {
  Object.entries(STAGE_LABELS).forEach(([value, label]) => {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = label;
    splitStageSelect.appendChild(opt);
  });
}

document.getElementById('drawer-edit-btn')?.addEventListener('click', () => {
  if (!state.currentApp) return;
  openModal(state.currentApp);
});

document.getElementById('drawer-delete-btn')?.addEventListener('click', async () => {
  if (!state.currentApp) return;
  if (!confirm(`Delete ${state.currentApp.company}? This cannot be undone.`)) return;
  const res = await fetch(`/api/applications/${state.currentApp.id}`, { method: 'DELETE' });
  if (!res.ok) { toast('Delete failed', 'error'); return; }
  toast('Application deleted', 'success');
  closeDrawer();
  await Promise.all([loadApps(), loadStats()]);
  renderTable();
  renderStats();
  renderFunnel();
  renderWeekly();
});

// ── Modal (Add / Edit) ────────────────────────────────────────────────────────

function openModal(app = null) {
  state.editingAppId = app?.id || null;
  document.getElementById('modal-title').textContent = app ? 'Edit Application' : 'Add Application';
  document.getElementById('modal-company').value = app?.company || '';
  document.getElementById('modal-role').value = app?.role || '';
  document.getElementById('modal-stage').value = app?.stage || 'applied';
  document.getElementById('modal-date').value = app?.applied_date?.slice(0, 10) || '';
  document.getElementById('modal-source').value = app?.source || 'manual';
  document.getElementById('modal-url').value = app?.job_url || '';
  document.getElementById('modal-notes').value = app?.notes || '';
  document.getElementById('modal-overlay').classList.add('open');
}

function closeModal() {
  document.getElementById('modal-overlay').classList.remove('open');
  state.editingAppId = null;
}

document.getElementById('add-app-btn')?.addEventListener('click', () => openModal());
document.getElementById('modal-cancel')?.addEventListener('click', closeModal);
document.getElementById('modal-overlay')?.addEventListener('click', e => {
  if (e.target === e.currentTarget) closeModal();
});

document.getElementById('modal-save')?.addEventListener('click', async () => {
  const company = document.getElementById('modal-company').value.trim();
  if (!company) { toast('Company name is required', 'error'); return; }

  const body = {
    company,
    role: document.getElementById('modal-role').value.trim() || null,
    stage: document.getElementById('modal-stage').value,
    applied_date: document.getElementById('modal-date').value || null,
    source: document.getElementById('modal-source').value,
    job_url: document.getElementById('modal-url').value.trim() || null,
    notes: document.getElementById('modal-notes').value.trim() || null,
  };

  const url = state.editingAppId ? `/api/applications/${state.editingAppId}` : '/api/applications';
  const method = state.editingAppId ? 'PATCH' : 'POST';

  const res = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) { toast('Save failed', 'error'); return; }

  toast(state.editingAppId ? 'Application updated' : 'Application added', 'success');
  closeModal();
  await Promise.all([loadApps(), loadStats()]);
  renderTable();
  renderStats();
  renderFunnel();
  renderWeekly();
});

// Populate modal stage select
const modalStageSelect = document.getElementById('modal-stage');
if (modalStageSelect) {
  Object.entries(STAGE_LABELS).forEach(([value, label]) => {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = label;
    modalStageSelect.appendChild(opt);
  });
}

// ── Settings panel ────────────────────────────────────────────────────────────

function closeSettings() {
  document.getElementById('settings-panel').classList.remove('open');
}

document.getElementById('settings-close')?.addEventListener('click', closeSettings);

document.getElementById('settings-btn')?.addEventListener('click', () => {
  document.getElementById('settings-panel').classList.toggle('open');
  document.getElementById('settings-label').value = state.settings.gmail_label || '';
  document.getElementById('settings-ghosted').value = state.settings.ghosted_days || '30';
  document.getElementById('settings-confidence').value = state.settings.min_confidence || '0.6';
});

document.getElementById('settings-save')?.addEventListener('click', async () => {
  const updates = {
    gmail_label: document.getElementById('settings-label').value.trim(),
    ghosted_days: parseInt(document.getElementById('settings-ghosted').value) || 30,
    min_confidence: parseFloat(document.getElementById('settings-confidence').value) || 0.6,
  };
  const res = await fetch('/api/settings', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  });
  if (!res.ok) { toast('Settings save failed', 'error'); return; }
  state.settings = await res.json();
  toast('Settings saved', 'success');
  closeSettings();
});

document.getElementById('settings-reclassify')?.addEventListener('click', async () => {
  const btn = document.getElementById('settings-reclassify');
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Clearing…';
  try {
    const res = await fetch('/api/sync/reset-email-cache', { method: 'POST' });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      toast(err.detail || 'Reset failed', 'error');
      return;
    }
    const data = await res.json();
    toast(
      `Cache cleared: ${data.events_cleared} events removed, ${data.applications_removed} auto-entries deleted. Run Sync Emails to re-classify.`,
      'success'
    );
    closeSettings();
    await Promise.all([loadApps(), loadStats()]);
    renderStats(); renderFunnel(); renderWeekly(); renderTable();
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
});

// ── Sync buttons ──────────────────────────────────────────────────────────────

// ── Sync progress polling ─────────────────────────────────────────────────────

let _pollInterval = null;

function startProgressPolling(btnId, originalLabel, onDone) {
  const btn = document.getElementById(btnId);
  const progressBar = document.getElementById('sync-progress');
  progressBar.style.display = 'block';

  _pollInterval = setInterval(async () => {
    const res = await fetch('/api/sync/status');
    const state = await res.json();

    if (state.total > 0) {
      const pct = Math.round(state.processed / state.total * 100);
      document.getElementById('sync-progress-bar').style.width = pct + '%';
    } else {
      // Indeterminate: animate via CSS class
      document.getElementById('sync-progress-bar').style.width = '30%';
    }
    document.getElementById('sync-progress-msg').textContent = state.message || 'Working…';

    if (!state.running) {
      clearInterval(_pollInterval);
      _pollInterval = null;
      btn.disabled = false;
      btn.innerHTML = originalLabel;
      progressBar.style.display = 'none';
      document.getElementById('sync-progress-bar').style.width = '0%';
      onDone(state);
    }
  }, 1000);
}

document.getElementById('sync-emails-btn')?.addEventListener('click', async () => {
  const btn = document.getElementById('sync-emails-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Starting…';

  const res = await fetch('/api/sync/emails', { method: 'POST' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    toast(err.detail || 'Email sync failed', 'error');
    btn.disabled = false;
    btn.innerHTML = 'Sync Emails';
    return;
  }

  startProgressPolling('sync-emails-btn', 'Sync Emails', async (state) => {
    if (state.error) {
      toast('Email sync error: ' + state.error, 'error');
    } else {
      const r = state.result || {};
      toast(`Email sync done — ${r.applications_updated || 0} updated, ${r.ghosted_flagged || 0} ghosted`, 'success');
    }
    await Promise.all([loadApps(), loadStats()]);
    renderTable(); renderStats(); renderFunnel(); renderWeekly();
  });
});

document.getElementById('sync-calendar-btn')?.addEventListener('click', async () => {
  const btn = document.getElementById('sync-calendar-btn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Starting…';

  const res = await fetch('/api/sync/calendar', { method: 'POST' });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    toast(err.detail || 'Calendar sync failed', 'error');
    btn.disabled = false;
    btn.innerHTML = 'Sync Calendar';
    return;
  }

  startProgressPolling('sync-calendar-btn', 'Sync Calendar', async (state) => {
    if (state.error) {
      toast('Calendar sync error: ' + state.error, 'error');
    } else {
      const r = state.result || {};
      toast(`Calendar sync done — ${r.applications_updated || 0} updated`, 'success');
    }
    await Promise.all([loadApps(), loadStats()]);
    renderTable(); renderStats(); renderFunnel(); renderWeekly();
  });
});

// ── Populate filter stage select ──────────────────────────────────────────────

const filterStageSelect = document.getElementById('filter-stage');
if (filterStageSelect) {
  filterStageSelect.innerHTML = '<option value="">All stages</option>';
  Object.entries(STAGE_LABELS).forEach(([value, label]) => {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = label;
    filterStageSelect.appendChild(opt);
  });
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function esc(str) {
  if (!str) return '';
  return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function fmtDate(str) {
  if (!str) return '';
  try { return new Date(str).toLocaleDateString('en-GB', { day:'numeric', month:'short', year:'numeric' }); }
  catch { return str; }
}

function fmtDateTime(str) {
  if (!str) return '';
  try { return new Date(str).toLocaleString('en-GB', { day:'numeric', month:'short', year:'numeric', hour:'2-digit', minute:'2-digit' }); }
  catch { return str; }
}

function toast(msg, type = 'success') {
  const container = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

// ── Boot ──────────────────────────────────────────────────────────────────────

init();
