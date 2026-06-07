/* ======================================================================
   AI Hub — Admin OS — JS Core
   Sections: State, API, Helpers (Toast/Modal/Copy/Icons/Format),
   DataTable, Charts, Tabs (Dashboard, GPU, Keys, Knowledge, Tenants),
   Bootstrap.
   ====================================================================== */

/* ====== State & Config ====== */
(() => {
    const params = new URLSearchParams(location.search);
    const k = params.get('key') || params.get('apiKey');
    if (k) {
        localStorage.setItem('apiKey', k);
        params.delete('key'); params.delete('apiKey');
        history.replaceState(null, '', location.pathname + (params.toString() ? '?' + params.toString() : '') + location.hash);
    }
})();

const ADMIN = {
    apiKey: localStorage.getItem('apiKey') || '',
    autoTimer: null,
    charts: { requests: null, latency: null, gpu: null, cost: null, modelUsage: null },
    gpuHistory: { labels: [], util: [], temp: [], vram: [] },
    GPU_MAX_POINTS: 60,
    tabs: ['dashboard','gpu','management','knowledge','tenants','audit','ihi','system'],
    tenant: { view: 'list', selectedTenant: null, selectedUser: null },
    theme: localStorage.getItem('admin-theme') || 'dark',
    cmdPaletteOpen: false,
};

/* ====== Icons (inline SVG paths) ====== */
const ICON = {
    copy:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>',
    trash:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 011-1h4a1 1 0 011 1v2"/></svg>',
    power:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18.36 6.64a9 9 0 11-12.73 0"/><line x1="12" y1="2" x2="12" y2="12"/></svg>',
    edit:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 113 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
    eye:     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>',
    refresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0114.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0020.49 15"/></svg>',
    search:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>',
    plus:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
    x:       '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
    check:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>',
    warn:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12" y2="17"/></svg>',
    err:     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
    info:    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>',
    database:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>',
    key:     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 11-7.778 7.778 5.5 5.5 0 017.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4"/></svg>',
    brain:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"/></svg>',
    users:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87M16 3.13a4 4 0 010 7.75"/></svg>',
    chevron: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>',
    activity:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>',
    clock:   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>',
    cpu:     '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/><line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/><line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/><line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/></svg>',
    dollar:  '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6"/></svg>',
};

/* ====== Format & escape ====== */
const fmtMs  = v => `${Math.round(v || 0)}ms`;
const fmtUsd = v => `$${Number(v || 0).toFixed(4)}`;
const fmtInt = v => Number(v || 0).toLocaleString();
const fmtTime = v => v ? new Date(v).toLocaleTimeString() : '—';
const fmtDate = v => v ? new Date(v).toLocaleDateString() : '—';
const fmtDateTime = v => v ? new Date(v).toLocaleString() : '—';
const escapeHtml = s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
const truncate = (s, n = 60) => { s = String(s ?? ''); return s.length > n ? s.slice(0,n) + '…' : s; };
const fmtRelative = v => {
    if (!v) return '—';
    const d = (Date.now() - new Date(v).getTime()) / 1000;
    if (d < 60)    return `${Math.round(d)}s ago`;
    if (d < 3600)  return `${Math.round(d/60)}m ago`;
    if (d < 86400) return `${Math.round(d/3600)}h ago`;
    return `${Math.round(d/86400)}d ago`;
};

/* ====== API Client ====== */
const headers = (extra = {}) => ({ 'X-API-KEY': ADMIN.apiKey, ...extra });
async function api(path, opts = {}) {
    const init = { headers: headers(opts.headers || {}) };
    if (opts.method) init.method = opts.method;
    if (opts.body !== undefined) {
        init.headers['Content-Type'] = 'application/json';
        init.body = typeof opts.body === 'string' ? opts.body : JSON.stringify(opts.body);
    }
    const r = await fetch(path, init);
    if (!r.ok) {
        let msg = r.statusText || `HTTP ${r.status}`;
        try { const j = await r.json(); msg = j.detail || msg; } catch(_) {}
        throw new Error(msg);
    }
    if (r.status === 204) return null;
    return r.json();
}

/* ====== Toast ====== */
function ensureToastStack() {
    let s = document.getElementById('toast-stack');
    if (!s) { s = document.createElement('div'); s.id = 'toast-stack'; s.className = 'toast-stack'; document.body.appendChild(s); }
    return s;
}
function toast(message, type = 'info') {
    const stack = ensureToastStack();
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.innerHTML = `${ICON[type] || ICON.info}<span>${escapeHtml(message)}</span>`;
    stack.appendChild(el);
    setTimeout(() => el.remove(), 4100);
}

/* ====== Modal ====== */
function openModal({ title, desc, contentHtml, confirmText = 'Confirm', cancelText = 'Cancel', danger = false, onlyClose = false, onConfirm = null }) {
    return new Promise(resolve => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        const close = (val) => {
            let captured = val;
            if (val && typeof onConfirm === 'function') {
                try { const r = onConfirm(overlay); if (r !== undefined) captured = r; } catch (e) { console.error(e); }
            }
            overlay.remove(); document.removeEventListener('keydown', onKey); resolve(captured);
        };
        const onKey = e => { if (e.key === 'Escape') close(false); else if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); close(true); } };
        overlay.innerHTML = `
            <div class="modal-card" role="dialog" aria-modal="true">
                <div class="modal-title">${escapeHtml(title || 'Confirm')}</div>
                ${desc ? `<div class="modal-desc">${escapeHtml(desc)}</div>` : ''}
                ${contentHtml ? `<div class="modal-content">${contentHtml}</div>` : ''}
                <div class="modal-actions">
                    ${onlyClose ? '' : `<button class="btn btn-ghost" data-act="cancel">${escapeHtml(cancelText)}</button>`}
                    <button class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-act="ok">${escapeHtml(onlyClose ? 'Close' : confirmText)}</button>
                </div>
            </div>`;
        overlay.addEventListener('click', e => { if (e.target === overlay) close(false); });
        overlay.querySelector('[data-act="ok"]').addEventListener('click', () => close(true));
        const cancelBtn = overlay.querySelector('[data-act="cancel"]');
        if (cancelBtn) cancelBtn.addEventListener('click', () => close(false));
        document.addEventListener('keydown', onKey);
        document.body.appendChild(overlay);
        const firstInput = overlay.querySelector('input, textarea, select');
        if (firstInput) setTimeout(() => firstInput.focus(), 0);
    });
}
const confirmDialog = (title, desc, opts = {}) => openModal({ title, desc, ...opts });
const previewDialog = (title, contentHtml) => openModal({ title, contentHtml, onlyClose: true });

/* ====== Clipboard ====== */
async function copyToClipboard(text, label = 'ID') {
    try {
        await navigator.clipboard.writeText(text);
        toast(`${label} copied`, 'ok');
    } catch (_) {
        toast('Copy failed', 'err');
    }
}

/* ====== DataTable ====== */
const DataTable = (() => {
    const STATE = new WeakMap();

    function makeIcon(name) { return ICON[name] || ICON.info; }

    function emptyHtml(empty) {
        const e = empty || {};
        return `<div class="dt-empty">${makeIcon(e.icon || 'database')}
            <div class="title">${escapeHtml(e.title || 'No records')}</div>
            ${e.hint ? `<div class="hint">${escapeHtml(e.hint)}</div>` : ''}
        </div>`;
    }

    function applyFilters(rows, state, columns) {
        let out = rows;
        const q = (state.search || '').trim().toLowerCase();
        if (q) {
            const searchKeys = columns.filter(c => c.search).map(c => c.key);
            out = out.filter(r => searchKeys.some(k => String(r[k] ?? '').toLowerCase().includes(q)));
        }
        for (const c of columns) {
            if (!c.filter) continue;
            const sel = state.filters[c.key];
            if (sel === undefined || sel === null) continue;
            out = out.filter(r => String(r[c.key] ?? '') === String(sel));
        }
        if (state.sort) {
            const { key, dir } = state.sort;
            out = [...out].sort((a, b) => {
                const va = a[key], vb = b[key];
                if (va == null && vb == null) return 0;
                if (va == null) return 1;
                if (vb == null) return -1;
                if (typeof va === 'number' && typeof vb === 'number') return dir === 'asc' ? va - vb : vb - va;
                return dir === 'asc'
                    ? String(va).localeCompare(String(vb))
                    : String(vb).localeCompare(String(va));
            });
        }
        return out;
    }

    function renderActions(actions, row) {
        return `<div class="dt-actions-row">${actions.map((a, i) => `
            <button class="btn-icon ${a.danger ? 'danger' : ''}" title="${escapeHtml(a.tooltip || '')}" data-act-i="${i}">${makeIcon(a.icon)}</button>
        `).join('')}</div>`;
    }

    function render(host) {
        const s = STATE.get(host);
        if (!s) return;
        const { columns, rows, opts } = s;
        const filtered = applyFilters(rows, s, columns);
        const pageSize = opts.pageSize || 50;
        const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
        if (s.page >= totalPages) s.page = totalPages - 1;
        const slice = filtered.slice(s.page * pageSize, (s.page + 1) * pageSize);

        const searchCols = columns.some(c => c.search);
        const filterCols = columns.filter(c => c.filter);
        const hasActions = !!opts.rowActions;

        const toolbar = `
            <div class="dt-toolbar">
                ${searchCols ? `<div class="input-with-icon dt-search">${ICON.search}<input class="input" placeholder="Search..." value="${escapeHtml(s.search || '')}" data-dt-search /></div>` : ''}
                ${filterCols.map(c => `
                    <div class="dt-filters" data-filter-col="${escapeHtml(c.key)}">
                        ${c.filter.map(opt => `<button class="dt-chip ${String(s.filters[c.key] ?? '__null__') === String(opt.v ?? '__null__') ? 'active' : ''}" data-fv="${opt.v == null ? '' : escapeHtml(String(opt.v))}" data-fnull="${opt.v == null ? '1' : '0'}">${escapeHtml(opt.label)}</button>`).join('')}
                    </div>
                `).join('')}
                <div class="dt-meta">${filtered.length}${rows.length !== filtered.length ? ` / ${rows.length}` : ''} rows</div>
            </div>`;

        let head = '<tr>';
        for (const c of columns) {
            const sortable = c.sortable;
            const sortClass = (s.sort && s.sort.key === c.key) ? `sort-${s.sort.dir}` : '';
            const align = c.align ? c.align : '';
            head += `<th class="${align} ${sortable ? 'sortable' : ''} ${sortClass}" data-sort="${sortable ? escapeHtml(c.key) : ''}">${escapeHtml(c.label)}${sortable ? `<span class="sort-arrow">${(s.sort && s.sort.key === c.key) ? (s.sort.dir === 'asc' ? '▲' : '▼') : '↕'}</span>` : ''}</th>`;
        }
        if (hasActions) head += '<th class="actions">Actions</th>';
        head += '</tr>';

        let body = '';
        if (!rows.length) {
            body = `<tr><td colspan="${columns.length + (hasActions ? 1 : 0)}">${emptyHtml(opts.emptyState)}</td></tr>`;
        } else if (!filtered.length) {
            body = `<tr><td colspan="${columns.length + (hasActions ? 1 : 0)}"><div class="dt-empty">${ICON.search}<div class="title">No matches</div><div class="hint">Try a different search</div></div></td></tr>`;
        } else {
            body = slice.map((r, i) => {
                let tr = '<tr>';
                for (const c of columns) {
                    const v = c.format ? c.format(r[c.key], r) : (r[c.key] ?? '—');
                    const align = c.align ? c.align : '';
                    tr += `<td class="${align}">${v}</td>`;
                }
                if (hasActions) {
                    const actions = opts.rowActions(r) || [];
                    tr += `<td class="actions" data-row-idx="${i}">${renderActions(actions, r)}</td>`;
                }
                tr += '</tr>';
                return tr;
            }).join('');
        }

        const pager = filtered.length > pageSize ? `
            <div class="dt-pager">
                <span>Page ${s.page + 1} / ${totalPages}</span>
                <span class="spacer"></span>
                <button data-page="prev" ${s.page === 0 ? 'disabled' : ''}>← Prev</button>
                <button data-page="next" ${s.page >= totalPages - 1 ? 'disabled' : ''}>Next →</button>
            </div>` : '';

        host.innerHTML = `
            <div class="dt-wrap">
                ${(searchCols || filterCols.length) ? toolbar : ''}
                <div class="dt-scroll"><table class="dt-table"><thead>${head}</thead><tbody>${body}</tbody></table></div>
                ${pager}
            </div>`;

        const searchEl = host.querySelector('[data-dt-search]');
        if (searchEl) searchEl.addEventListener('input', e => { s.search = e.target.value; s.page = 0; render(host); });
        host.querySelectorAll('[data-sort]').forEach(th => {
            const key = th.getAttribute('data-sort');
            if (!key) return;
            th.addEventListener('click', () => {
                if (!s.sort || s.sort.key !== key) s.sort = { key, dir: 'asc' };
                else if (s.sort.dir === 'asc') s.sort.dir = 'desc';
                else s.sort = null;
                render(host);
            });
        });
        host.querySelectorAll('[data-filter-col]').forEach(grp => {
            const col = grp.getAttribute('data-filter-col');
            grp.querySelectorAll('.dt-chip').forEach(chip => {
                chip.addEventListener('click', () => {
                    const isNull = chip.getAttribute('data-fnull') === '1';
                    s.filters[col] = isNull ? null : chip.getAttribute('data-fv');
                    s.page = 0; render(host);
                });
            });
        });
        host.querySelectorAll('[data-page]').forEach(btn => btn.addEventListener('click', () => {
            s.page += (btn.getAttribute('data-page') === 'next' ? 1 : -1);
            render(host);
        }));
        host.querySelectorAll('td.actions[data-row-idx]').forEach(td => {
            const i = parseInt(td.getAttribute('data-row-idx'));
            const r = slice[i];
            const actions = opts.rowActions(r) || [];
            td.querySelectorAll('[data-act-i]').forEach(btn => {
                const ai = parseInt(btn.getAttribute('data-act-i'));
                const a = actions[ai];
                if (!a) return;
                btn.addEventListener('click', async (e) => {
                    e.stopPropagation();
                    if (a.confirm) {
                        const ok = await confirmDialog(a.confirm.title || 'Confirm action', a.confirm.desc || '', { danger: !!a.danger, confirmText: a.confirm.ok || 'Confirm' });
                        if (!ok) return;
                    }
                    try { await a.onClick(r); } catch (err) { toast(err.message || 'Action failed', 'err'); }
                });
            });
        });
    }

    function mount(hostId, opts) {
        const host = typeof hostId === 'string' ? document.getElementById(hostId) : hostId;
        if (!host) return;
        const cols = opts.columns;
        const init = STATE.get(host) || { search: '', filters: {}, sort: null, page: 0, columns: cols, rows: [], opts };
        init.columns = cols; init.rows = opts.rows || []; init.opts = opts;
        STATE.set(host, init);
        render(host);
    }

    function setRows(hostId, rows) {
        const host = typeof hostId === 'string' ? document.getElementById(hostId) : hostId;
        const s = STATE.get(host);
        if (!s) return;
        s.rows = rows || [];
        render(host);
    }

    function setLoading(hostId) {
        const host = typeof hostId === 'string' ? document.getElementById(hostId) : hostId;
        if (!host) return;
        const skel = Array.from({length: 4}, () => `<tr class="dt-skeleton-row"><td colspan="10"><div class="dt-skel"></div></td></tr>`).join('');
        host.innerHTML = `<div class="dt-wrap"><div class="dt-scroll"><table class="dt-table"><tbody>${skel}</tbody></table></div></div>`;
    }

    return { mount, setRows, setLoading };
})();

/* ====== Status badges ====== */
const badge = (kind, text) => `<span class="badge badge-${kind}"><span class="badge-dot"></span>${escapeHtml(text)}</span>`;
const statusBadgeFromCode = code => {
    const c = Number(code) || 0;
    if (c >= 500) return badge('err', `${c}`);
    if (c >= 400) return badge('warn', `${c}`);
    if (c >= 200) return badge('ok', `${c}`);
    return badge('muted', '—');
};

/* ====== Charts ====== */
function makeLineChart(ctx, label, data, color) {
    return new Chart(ctx, {
        type: 'line',
        data: { labels: [], datasets: [{ label, data, borderColor: color, backgroundColor: color.replace('1)', '0.08)'), borderWidth: 2, tension: 0.4, fill: true, pointRadius: 0 }] },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: { legend: { display: false }, tooltip: { mode: 'index', intersect: false } },
            scales: {
                y: { grid: { color: 'rgba(255,255,255,0.03)' }, ticks: { color: '#475569', font: { size: 10 } } },
                x: { grid: { display: false }, ticks: { color: '#475569', font: { size: 10 }, maxTicksLimit: 8 } },
            },
        },
    });
}

function updateMainCharts(series) {
    const labels = (series || []).map(d => new Date(d.bucket).getHours() + ':00');
    const successData = (series || []).map(d => d.success_requests);
    const latencyData = (series || []).map(d => d.avg_latency_ms);
    const reqEl = document.getElementById('requests-chart');
    const latEl = document.getElementById('latency-chart');
    if (!ADMIN.charts.requests && reqEl) ADMIN.charts.requests = makeLineChart(reqEl.getContext('2d'), 'Requests', successData, 'rgba(99,102,241,1)');
    if (!ADMIN.charts.latency && latEl) ADMIN.charts.latency = makeLineChart(latEl.getContext('2d'), 'Latency', latencyData, 'rgba(16,185,129,1)');
    if (ADMIN.charts.requests) { ADMIN.charts.requests.data.labels = labels; ADMIN.charts.requests.data.datasets[0].data = successData; ADMIN.charts.requests.update('none'); }
    if (ADMIN.charts.latency)  { ADMIN.charts.latency.data.labels  = labels; ADMIN.charts.latency.data.datasets[0].data  = latencyData; ADMIN.charts.latency.update('none');  }
}

function updateGpuSparkline(g) {
    const now = new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    const H = ADMIN.gpuHistory;
    H.labels.push(now); H.util.push(g.utilization); H.temp.push(g.temperature); H.vram.push(Math.round(g.memory_used / g.memory_total * 100));
    while (H.labels.length > ADMIN.GPU_MAX_POINTS) { H.labels.shift(); H.util.shift(); H.temp.shift(); H.vram.shift(); }
    const canvas = document.getElementById('gpu-sparkline');
    if (!canvas) return;
    if (!ADMIN.charts.gpu) {
        ADMIN.charts.gpu = new Chart(canvas.getContext('2d'), {
            type: 'line',
            data: { labels: H.labels, datasets: [
                { label: 'Util %', data: H.util, borderColor: 'rgba(99,102,241,1)', backgroundColor: 'rgba(99,102,241,0.10)', borderWidth: 2, fill: true, tension: 0.4, pointRadius: 0 },
                { label: 'Temp °C', data: H.temp, borderColor: 'rgba(16,185,129,1)', backgroundColor: 'rgba(16,185,129,0)', borderWidth: 1.5, fill: false, tension: 0.4, pointRadius: 0 },
                { label: 'VRAM %', data: H.vram, borderColor: 'rgba(168,85,247,1)', backgroundColor: 'rgba(168,85,247,0)', borderWidth: 1.5, fill: false, tension: 0.4, pointRadius: 0 },
            ] },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                plugins: { legend: { display: false }, tooltip: { mode: 'index', intersect: false } },
                scales: {
                    y: { min: 0, max: 100, grid: { color: 'rgba(255,255,255,0.03)' }, ticks: { color: '#475569', font: { size: 9 }, callback: v => v + '%' } },
                    x: { grid: { display: false }, ticks: { color: '#475569', font: { size: 9 }, maxTicksLimit: 6 } },
                },
            },
        });
    } else {
        ADMIN.charts.gpu.data.labels = H.labels;
        ADMIN.charts.gpu.data.datasets[0].data = H.util;
        ADMIN.charts.gpu.data.datasets[1].data = H.temp;
        ADMIN.charts.gpu.data.datasets[2].data = H.vram;
        ADMIN.charts.gpu.update('none');
    }
}

/* ====== GPU Cards ====== */
function gpuRing(value, max = 100, size = 110) {
    const r = (size - 16) / 2;
    const c = 2 * Math.PI * r;
    const pct = Math.max(0, Math.min(1, value / max));
    const off = c * (1 - pct);
    const cls = value >= 85 ? 'err' : value >= 65 ? 'warn' : '';
    return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
        <circle class="ring-bg" cx="${size/2}" cy="${size/2}" r="${r}"></circle>
        <circle class="ring-fg ${cls}" cx="${size/2}" cy="${size/2}" r="${r}" stroke-dasharray="${c}" stroke-dashoffset="${off}" stroke-linecap="round"></circle>
    </svg>`;
}
function renderGpuCards(gpus) {
    const el = document.getElementById('gpu-detailed-cards');
    if (!el) return;
    el.innerHTML = gpus.map(g => {
        const vramPct = Math.round(g.memory_used / g.memory_total * 100);
        const tempCls = g.temperature >= 80 ? 'err' : g.temperature >= 60 ? 'warn' : 'ok';
        return `<div class="glass-panel" style="padding:1.5rem; border-radius:1.25rem;">
            <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:1.5rem;">
                <div>
                    <div style="font-size:0.7rem; color:var(--text-faint); font-weight:700; text-transform:uppercase; letter-spacing:0.18em;">GPU ${g.index}</div>
                    <div style="font-size:1.125rem; font-weight:800; margin-top:0.25rem;">${escapeHtml(g.name)}</div>
                </div>
                ${badge('ok', 'Online')}
            </div>
            <div style="display:flex; gap:1.5rem; align-items:center;">
                <div class="ring-wrap">${gpuRing(vramPct)}<div class="ring-label"><div class="v">${vramPct}%</div><div class="l">VRAM</div></div></div>
                <div style="flex:1; display:grid; grid-template-columns:1fr 1fr; gap:1rem;">
                    <div class="kv"><span class="k">Core Load</span><span class="v">${g.utilization}%</span></div>
                    <div class="kv"><span class="k">Temp</span><span class="v"><span class="badge badge-${tempCls}"><span class="badge-dot"></span>${g.temperature}°C</span></span></div>
                    <div class="kv"><span class="k">Used</span><span class="v">${Math.round(g.memory_used/1024)} GB</span></div>
                    <div class="kv"><span class="k">Total</span><span class="v">${Math.round(g.memory_total/1024)} GB</span></div>
                </div>
            </div>
        </div>`;
    }).join('');
}

/* ====== Tab: Dashboard ====== */
function setText(id, v) { const e = document.getElementById(id); if (e) e.textContent = v; }

async function refreshDashboard() {
    if (!ADMIN.apiKey) { setText('sync-status', 'AUTH_REQUIRED'); return; }
    setText('sync-status', 'SYNCING');
    try {
        const [stats, queue, gpu] = await Promise.all([
            api('/v1/admin/stats'),
            api('/v1/admin/queue'),
            api('/v1/admin/gpu/stats').catch(() => null),
        ]);

        const reqEl = document.getElementById('total-requests');
        const latEl = document.getElementById('latency-p95');
        const gpuEl = document.getElementById('gpu-active');
        const costEl = document.getElementById('month-cost');
        if (reqEl) countUp(reqEl, stats.total_requests || 0);
        setText('success-error', `${fmtInt(stats.success_requests)} OK · ${fmtInt(stats.error_requests)} ERR`);
        if (latEl) countUp(latEl, Math.round(stats.latency?.p95_ms || 0), '', 'ms');
        setText('latency-detail', `avg ${fmtMs(stats.latency?.avg_ms)} · max ${fmtMs(stats.latency?.max_ms)}`);
        if (gpuEl) gpuEl.textContent = `${queue.active}/${queue.capacity}`;
        setText('gpu-queue-text', `${queue.capacity - queue.active} slot${(queue.capacity - queue.active) === 1 ? '' : 's'} free`);
        if (costEl) countUp(costEl, Number(stats.month_cost_usd || 0), '$');
        setText('total-cost', `total ${fmtUsd(stats.total_cost_usd)}`);

        updateMainCharts(stats.time_series || []);

        DataTable.mount('recent-table', {
            columns: [
                { key: 'created_at', label: 'Time', format: fmtTime, sortable: true },
                { key: 'project_id', label: 'Project', search: true },
                { key: 'model', label: 'Model', search: true },
                { key: 'latency_ms', label: 'Latency', format: fmtMs, sortable: true, align: 'right' },
                { key: 'status_code', label: 'Status', format: statusBadgeFromCode, align: 'center' },
            ],
            rows: stats.recent || [],
            emptyState: { icon: 'activity', title: 'No requests yet', hint: 'Traffic will appear here in real time' },
            pageSize: 25,
        });

        if (gpu && gpu.gpus && gpu.gpus.length) {
            const g = gpu.gpus[0];
            const utilBar = document.getElementById('gpu-util-bar');
            if (utilBar) {
                utilBar.style.width = g.utilization + '%';
                utilBar.style.background = g.utilization > 80 ? 'var(--status-err)' : 'var(--accent-1)';
            }
            setText('gpu-temp-text', g.temperature + '°C');
            setText('gpu-mem-text', `${Math.round(g.memory_used/1024)}/${Math.round(g.memory_total/1024)} GB`);
            renderGpuCards(gpu.gpus);
            updateGpuSparkline(g);
        }
        setText('sync-status', 'SYSTEM_NOMINAL');
    } catch (e) {
        setText('sync-status', 'ERR_SYNC_FAILED');
        toast(e.message || 'Sync failed', 'err');
    }
}

/* ====== Tab: Keys (Management) ====== */
async function populateTenantSelector() {
    const sel = document.getElementById('key-tenant');
    if (!sel) return;
    if (sel.dataset.loaded === '1') return;  // avoid re-fetch on every tab switch
    try {
        const tenants = await api('/v1/admin/tenants');
        sel.innerHTML = '<option value="">-- Select tenant --</option>' +
            tenants.map(t => `<option value="${escapeHtml(t.project_id)}">${escapeHtml(t.project_id)} / ${escapeHtml(t.tenant_id)} (${t.total_requests || 0} req)</option>`).join('');
        sel.dataset.loaded = '1';
    } catch (e) {
        sel.innerHTML = '<option value="">-- Failed to load --</option>';
    }
}

async function refreshKeys() {
    DataTable.setLoading('keys-table');
    try {
        const keys = await api('/v1/admin/management/keys');
        const tenants = [...new Set(keys.map(k => k.tenant_id).filter(Boolean))];
        DataTable.mount('keys-table', {
            columns: [
                { key: 'name', label: 'Identity', sortable: true, search: true, format: v => `<span style="font-weight:600;color:var(--text-primary)">${escapeHtml(v || '—')}</span>` },
                { key: 'owner_name', label: 'Owner', search: true, format: v => v ? escapeHtml(v) : '<span style="color:var(--text-faint)">—</span>' },
                { key: 'tenant_id', label: 'Tenant', filter: [{label:'All',v:null}, ...tenants.map(t => ({label:t, v:t}))], format: v => `<span class="mono" style="font-size:0.75rem;color:var(--text-secondary)">${escapeHtml(v || '—')}</span>` },
                { key: 'is_admin', label: 'Role', filter: [{label:'All',v:null},{label:'Admin',v:'1'},{label:'User',v:'0'}], format: v => v ? badge('accent', '👑 Admin') : badge('muted', 'User') },
                { key: 'enabled', label: 'Status', filter: [{label:'All',v:null},{label:'Active',v:'1'},{label:'Disabled',v:'0'}], format: v => v ? badge('ok', 'Active') : badge('err', 'Disabled') },
                { key: 'rpm_limit', label: 'RPM', sortable: true, align: 'right', format: v => `<span class="mono">${v ?? '—'}</span>` },
                { key: 'current_spend', label: 'Spend', sortable: true, align: 'right', format: fmtUsd },
            ],
            rows: keys,
            rowActions: (r) => [
                { icon: 'copy', tooltip: 'Copy ID', onClick: () => copyToClipboard(r.id, 'Key ID') },
                { icon: 'edit', tooltip: 'Edit limits', onClick: () => editKey(r) },
                { icon: 'power', tooltip: r.enabled ? 'Disable' : 'Enable', danger: !!r.enabled, onClick: () => toggleKey(r) },
                { icon: 'trash', tooltip: 'Delete', danger: true,
                  confirm: { title: 'Disable this key?', desc: `"${r.name}" sẽ bị vô hiệu hóa và không thể được dùng để gọi API.`, ok: 'Disable' },
                  onClick: () => disableKey(r) },
            ],
            emptyState: { icon: 'key', title: 'No keys yet', hint: 'Mint your first token to grant API access' },
            pageSize: 20,
        });
    } catch (e) { toast(e.message, 'err'); }
}

async function refreshSessions() {
    DataTable.setLoading('sessions-table');
    try {
        const url = '/v1/admin/management/sessions?limit=500' + (ADMIN.tenant.selectedTenant ? '&project_id=' + encodeURIComponent(ADMIN.tenant.selectedTenant) : '');
        const data = await api(url);
        DataTable.mount('sessions-table', {
            columns: [
                { key: 'user_name', label: 'User', search: true, format: v => `<span style="font-weight:600">${escapeHtml(v || '—')}</span>` },
                { key: 'project_id', label: 'Project', search: true },
                { key: 'message_count', label: 'Messages', sortable: true, align: 'right', format: fmtInt },
                { key: 'last_active', label: 'Last Active', sortable: true, format: fmtRelative },
            ],
            rows: data,
            emptyState: { icon: 'users', title: 'No sessions', hint: 'Sessions appear once users start chatting' },
            pageSize: 20,
        });
    } catch (e) { toast(e.message, 'err'); }
}

async function toggleKey(row) {
    try {
        await api(`/v1/admin/keys/${encodeURIComponent(row.id)}`, { method: 'PATCH', body: { enabled: !row.enabled } });
        toast(`Key "${row.name}" ${row.enabled ? 'disabled' : 'enabled'}`, 'ok');
        refreshKeys();
    } catch (e) { toast(e.message, 'err'); }
}

async function disableKey(row) {
    try {
        await api(`/v1/admin/keys/${encodeURIComponent(row.id)}`, { method: 'DELETE' });
        toast(`Key "${row.name}" disabled`, 'ok');
        refreshKeys();
    } catch (e) { toast(e.message, 'err'); }
}

async function editKey(row) {
    const html = `
        <label style="display:block;margin-bottom:0.5rem;font-size:0.7rem;color:var(--text-faint);font-weight:700;text-transform:uppercase;letter-spacing:0.1em">RPM Limit</label>
        <input id="edit-rpm" class="input mono" type="number" value="${row.rpm_limit ?? 60}" style="margin-bottom:1rem"/>
        <label style="display:block;margin-bottom:0.5rem;font-size:0.7rem;color:var(--text-faint);font-weight:700;text-transform:uppercase;letter-spacing:0.1em">Monthly Budget (USD, blank = unlimited)</label>
        <input id="edit-budget" class="input mono" type="number" step="0.01" value="${row.monthly_budget_usd ?? ''}" placeholder="unlimited"/>
    `;
    const ok = await openModal({ title: `Edit "${row.name}"`, contentHtml: html, confirmText: 'Save' });
    if (!ok) return;
    const rpmEl = document.getElementById('edit-rpm');
    const budgetEl = document.getElementById('edit-budget');
    const patch = {};
    if (rpmEl && rpmEl.value !== '') patch.rpm_limit = parseInt(rpmEl.value);
    if (budgetEl) {
        if (budgetEl.value === '') patch.monthly_budget_usd = null;
        else patch.monthly_budget_usd = parseFloat(budgetEl.value);
    }
    try {
        await api(`/v1/admin/keys/${encodeURIComponent(row.id)}`, { method: 'PATCH', body: patch });
        toast('Key updated', 'ok');
        refreshKeys();
    } catch (e) { toast(e.message, 'err'); }
}

/* ====== Tab: Knowledge ====== */
async function refreshKnowledge() {
    DataTable.setLoading('rag-table');
    try {
        const data = await api('/v1/admin/knowledge/cards');
        const projects = [...new Set(data.map(c => c.project_id).filter(Boolean))];
        const domains  = [...new Set(data.map(c => c.knowledge_domain).filter(Boolean))];
        DataTable.mount('rag-table', {
            columns: [
                { key: 'project_id', label: 'Project', filter: [{label:'All',v:null}, ...projects.map(p => ({label:p, v:p}))], format: v => `<span class="mono" style="color:var(--text-secondary)">${escapeHtml(v)}</span>` },
                { key: 'title', label: 'Title', sortable: true, search: true, format: v => `<span style="font-weight:600">${escapeHtml(truncate(v, 60))}</span>` },
                { key: 'knowledge_domain', label: 'Domain', filter: [{label:'All',v:null}, ...domains.map(d => ({label:d, v:d}))], format: v => badge('accent', v || 'general') },
                { key: 'trust_level', label: 'Trust', align: 'center', sortable: true, format: v => `<span class="mono">${v}/5</span>` },
                { key: 'created_at', label: 'Created', sortable: true, format: fmtDate },
            ],
            rows: data,
            rowActions: (r) => [
                { icon: 'eye', tooltip: 'Preview content', onClick: () => previewDialog(r.title, escapeHtml(r.content || '(empty)')) },
                { icon: 'copy', tooltip: 'Copy ID', onClick: () => copyToClipboard(r.id, 'Card ID') },
                { icon: 'trash', tooltip: 'Delete card', danger: true,
                  confirm: { title: 'Delete this card?', desc: `"${r.title}" và tất cả chunks/embeddings sẽ bị xóa vĩnh viễn.`, ok: 'Delete' },
                  onClick: () => deleteCard(r) },
            ],
            emptyState: { icon: 'brain', title: 'No knowledge cards', hint: 'Ingest content to build the RAG index' },
            pageSize: 25,
        });
    } catch (e) { toast(e.message, 'err'); }
}

async function deleteCard(row) {
    try {
        await api(`/v1/admin/knowledge/cards/${encodeURIComponent(row.id)}`, { method: 'DELETE' });
        toast(`Card "${truncate(row.title, 30)}" deleted`, 'ok');
        refreshKnowledge();
    } catch (e) { toast(e.message, 'err'); }
}

async function reindexKnowledge() {
    try {
        const r = await api('/v1/admin/knowledge/reindex', { method: 'POST', body: {} });
        toast(`Reindex complete: ${r.processed ?? 0} embeddings`, 'ok');
        refreshKnowledge();
    } catch (e) { toast(e.message, 'err'); }
}

/* ====== Tab: Tenants ====== */
const AVATAR_COLORS = ['indigo','emerald','amber','violet','rose','sky'];
function avatarColor(str) {
    let h = 0;
    for (let i = 0; i < (str||'').length; i++) h = ((h << 5) - h + str.charCodeAt(i)) | 0;
    return AVATAR_COLORS[Math.abs(h) % AVATAR_COLORS.length];
}
function initials(str) {
    return (str||'').split(/[-_\s.]+/).filter(Boolean).slice(0,2).map(w => w[0].toUpperCase()).join('') || '?';
}
function activityRingSvg(pct) {
    const r = 8, c = 2 * Math.PI * r, off = c * (1 - Math.min(1, pct));
    return `<svg class="activity-ring" viewBox="0 0 22 22"><circle class="ring-bg" cx="11" cy="11" r="${r}"/><circle class="ring-fg" cx="11" cy="11" r="${r}" stroke-dasharray="${c}" stroke-dashoffset="${off}"/></svg>`;
}

function tenantBreadcrumb() {
    const t = ADMIN.tenant;
    const parts = [`<button data-crumb="root">TENANTS</button>`];
    if (t.selectedTenant) parts.push(`<span class="sep">/</span><button data-crumb="tenant">${escapeHtml(t.selectedTenant)}</button>`);
    if (t.selectedUser)   parts.push(`<span class="sep">/</span><button data-crumb="user">${escapeHtml(t.selectedUser.name)}</button>`);
    if (t.view === 'chat') parts.push(`<span class="sep">/</span><span style="color:var(--text-secondary)">CHAT</span>`);
    const el = document.getElementById('tenants-breadcrumb');
    if (!el) return;
    el.innerHTML = parts.join('');
    el.querySelectorAll('[data-crumb]').forEach(b => b.addEventListener('click', () => {
        const c = b.getAttribute('data-crumb');
        if (c === 'root') loadTenants();
        else if (c === 'tenant') loadTenantUsers(t.selectedTenant);
        else if (c === 'user') loadUserDetail(t.selectedUser.id, t.selectedUser.name);
    }));
}

function switchTenantView(v) {
    ['list','users','detail','chat'].forEach(x => {
        const el = document.getElementById('tenants-view-' + x);
        if (el) el.style.display = (x === v) ? '' : 'none';
    });
    ADMIN.tenant.view = v;
    tenantBreadcrumb();
}

async function loadTenants() {
    ADMIN.tenant.selectedTenant = null;
    ADMIN.tenant.selectedUser = null;
    switchTenantView('list');
    const grid = document.getElementById('tenants-grid');
    grid.innerHTML = `<div style="grid-column:1/-1;text-align:center;padding:5rem 0"><div class="dt-skel" style="height:60px;width:60%;margin:0 auto;border-radius:1rem"></div></div>`;
    try {
        const data = await api('/v1/admin/tenants');
        if (!data.length) {
            grid.innerHTML = `<div style="grid-column:1/-1">${ICON.database}<div class="dt-empty"><div class="title">No active projects</div><div class="hint">Tenants appear once they start sending traffic</div></div></div>`;
            return;
        }
        const maxReq = Math.max(1, ...data.map(t => t.total_requests || 0));
        const maxUsers = Math.max(1, ...data.map(t => t.user_count || 0));
        grid.className = 'tenant-3d-grid';
        grid.innerHTML = data.map((t, i) => {
            const color = avatarColor(t.project_id);
            const pct = Math.min(1, (t.requests_today || 0) / 50);
            const reqBar = Math.round((t.total_requests || 0) / maxReq * 100);
            const userBar = Math.round((t.user_count || 0) / maxUsers * 100);
            return `
            <div class="tenant-3d-wrap">
                <div class="tenant-card-3d ${t.is_active ? 'glow-active' : ''}" data-tenant="${escapeHtml(t.project_id)}">
                    <div class="card-orb" style="width:120px;height:120px;background:${t.is_active ? 'var(--accent-1)' : 'var(--text-faint)'};top:-30px;right:-20px"></div>
                    <div class="card-orb" style="width:80px;height:80px;background:var(--accent-2);bottom:-20px;left:-10px"></div>
                    <div style="display:flex;align-items:center;gap:1rem;margin-bottom:1.25rem">
                        <div class="tenant-avatar ${color}" style="position:relative">
                            ${initials(t.project_id)}
                            ${activityRingSvg(pct)}
                        </div>
                        <div style="flex:1;min-width:0">
                            <div style="font-size:1.2rem;font-weight:800;color:var(--text-primary);letter-spacing:-0.01em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(t.project_id)}</div>
                            <div class="mono" style="font-size:0.7rem;color:var(--text-muted);margin-top:0.2rem">${t.user_count || 0} users · ${t.requests_today || 0} req today</div>
                        </div>
                        <span style="color:var(--text-faint);transform:translateZ(15px)">${ICON.chevron}</span>
                    </div>
                    <div class="stat-mini-bars">
                        <div class="stat-mini">
                            <span class="label">Requests</span>
                            <div class="bar-track"><div class="bar-fill ${color}" style="width:${reqBar}%"></div></div>
                            <span class="val">${fmtInt(t.total_requests)}</span>
                        </div>
                        <div class="stat-mini">
                            <span class="label">Users</span>
                            <div class="bar-track"><div class="bar-fill ${color}" style="width:${userBar}%"></div></div>
                            <span class="val">${t.user_count || 0}</span>
                        </div>
                    </div>
                    <div class="card-meta-row">
                        <span>${t.is_active ? '🟢 Active' : '⚪ Idle'}</span>
                        <span>${fmtRelative(t.last_activity)}</span>
                    </div>
                </div>
            </div>`;
        }).join('');
        // 3D tilt on mouse move
        grid.querySelectorAll('.tenant-card-3d').forEach(card => {
            card.addEventListener('mousemove', e => {
                const r = card.getBoundingClientRect();
                const x = (e.clientX - r.left) / r.width - 0.5;
                const y = (e.clientY - r.top) / r.height - 0.5;
                card.style.transform = `rotateY(${x * 12}deg) rotateX(${-y * 12}deg) translateY(-4px) scale(1.02)`;
            });
            card.addEventListener('mouseleave', () => {
                card.style.transform = '';
            });
            card.addEventListener('click', () => loadTenantUsers(card.getAttribute('data-tenant')));
        });
    } catch (e) { toast(e.message, 'err'); }
}

async function loadTenantUsers(projectId) {
    ADMIN.tenant.selectedTenant = projectId;
    ADMIN.tenant.selectedUser = null;
    switchTenantView('users');
    setText('tenant-users-title', `${projectId.toUpperCase()} — Users`);
    const tableEl = document.getElementById('tenant-users-table');
    tableEl.innerHTML = `<div style="text-align:center;padding:3rem 0"><div class="dt-skel" style="height:50px;width:50%;margin:0 auto;border-radius:1rem"></div></div>`;
    try {
        const data = await api(`/v1/admin/tenants/${encodeURIComponent(projectId)}/users`);
        if (!data.length) {
            tableEl.innerHTML = `<div class="dt-empty">${ICON.users}<div class="title">No users in this project</div><div class="hint">Once users send a message, they show up here</div></div>`;
            return;
        }
        const maxMsg = Math.max(1, ...data.map(u => u.message_count || 0));
        const maxSess = Math.max(1, ...data.map(u => u.session_count || 0));
        tableEl.innerHTML = `<div class="user-3d-grid">${data.map(u => {
            const color = avatarColor(u.name);
            const msgBar = Math.round((u.message_count || 0) / maxMsg * 100);
            const sessBar = Math.round((u.session_count || 0) / maxSess * 100);
            const isRecent = u.last_message_at && (Date.now() - new Date(u.last_message_at).getTime()) < 3600000;
            return `
            <div class="user-card-3d ${isRecent ? 'glow-active' : ''}" data-user-id="${escapeHtml(u.id)}" data-user-name="${escapeHtml(u.name)}">
                <div style="display:flex;align-items:center;gap:0.85rem;margin-bottom:1rem">
                    <div class="user-avatar ${color}">${initials(u.name)}</div>
                    <div style="flex:1;min-width:0">
                        <div style="font-size:1rem;font-weight:700;color:var(--text-primary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(u.name)}</div>
                        <div style="display:flex;align-items:center;gap:0.4rem;margin-top:0.2rem">
                            <span class="status-dot" style="background:${u.is_active || isRecent ? 'var(--status-ok)' : 'var(--text-faint)'}; width:6px; height:6px; ${isRecent ? 'animation:pulse 2s infinite' : ''}"></span>
                            <span class="mono" style="font-size:0.65rem;color:var(--text-muted)">${isRecent ? 'Online' : fmtRelative(u.last_message_at)}</span>
                        </div>
                    </div>
                </div>
                <div class="stat-mini-bars">
                    <div class="stat-mini">
                        <span class="label">Messages</span>
                        <div class="bar-track"><div class="bar-fill ${color}" style="width:${msgBar}%"></div></div>
                        <span class="val">${fmtInt(u.message_count)}</span>
                    </div>
                    <div class="stat-mini">
                        <span class="label">Sessions</span>
                        <div class="bar-track"><div class="bar-fill ${color}" style="width:${sessBar}%"></div></div>
                        <span class="val">${u.session_count || 0}</span>
                    </div>
                </div>
                <div class="user-stats">
                    <div class="user-stat"><div class="val">${fmtInt(u.message_count)}</div><div class="lbl">Messages</div></div>
                    <div class="user-stat"><div class="val">${u.session_count || 0}</div><div class="lbl">Sessions</div></div>
                    <div class="user-stat"><div class="val">${fmtRelative(u.last_message_at)}</div><div class="lbl">Last Seen</div></div>
                </div>
            </div>`;
        }).join('')}</div>`;
        tableEl.querySelectorAll('.user-card-3d').forEach(card => {
            card.addEventListener('click', () => loadUserDetail(card.getAttribute('data-user-id'), card.getAttribute('data-user-name')));
        });
    } catch (e) { toast(e.message, 'err'); }
}

async function loadUserDetail(userId, userName) {
    ADMIN.tenant.selectedUser = { id: userId, name: userName };
    switchTenantView('detail');
    const el = document.getElementById('user-detail-content');
    el.innerHTML = `<div style="text-align:center;padding:5rem 0"><div class="dt-skel" style="height:60px;width:50%;margin:0 auto"></div></div>`;
    try {
        const d = await api(`/v1/admin/users/${encodeURIComponent(userId)}/detail`);
        const u = d.user, k = d.api_key;
        el.innerHTML = `
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:1.25rem;margin-bottom:1.25rem">
                <div class="glass-panel" style="padding:1.5rem;border-radius:1.25rem">
                    <div class="section-title indigo">${ICON.users}IDENTITY</div>
                    <div class="kv"><span class="k">Name</span><span class="v" style="font-weight:600">${escapeHtml(u.name)}</span></div>
                    <div class="kv"><span class="k">Tenant</span><span class="v mono">${escapeHtml(u.tenant_id)}</span></div>
                    <div class="kv"><span class="k">Joined</span><span class="v">${fmtDate(u.created_at)}</span></div>
                    <div class="kv"><span class="k">User ID</span><span class="v mono" style="font-size:0.7rem;color:var(--text-faint);word-break:break-all">${escapeHtml(u.id)}</span></div>
                </div>
                <div class="glass-panel" style="padding:1.5rem;border-radius:1.25rem">
                    <div class="section-title amber">${ICON.key}ACCESS CONTROL</div>
                    ${k ? `
                        <div class="kv"><span class="k">Key Name</span><span class="v">${escapeHtml(k.name)}</span></div>
                        <div class="kv"><span class="k">RPM Limit</span><span class="v mono">${k.rpm_limit}</span></div>
                        <div class="kv"><span class="k">Admin</span><span class="v">${k.is_admin ? badge('accent','👑 Yes') : badge('muted','No')}</span></div>
                        <div class="kv"><span class="k">External</span><span class="v">${k.allow_external ? badge('ok','Allowed') : badge('muted','Local only')}</span></div>
                        <div class="kv"><span class="k">Status</span><span class="v">${k.enabled ? badge('ok','Active') : badge('err','Disabled')}</span></div>
                    ` : `<div style="color:var(--text-faint);font-size:0.8125rem;text-align:center;padding:1rem">No API key bound</div>`}
                </div>
            </div>
            ${d.pinned_memories.length ? `
            <div class="glass-panel" style="padding:1.5rem;border-radius:1.25rem;margin-bottom:1.25rem">
                <div class="section-title violet">${ICON.brain}PINNED MEMORY (${d.pinned_memories.length})</div>
                <div id="pinned-mem-table"></div>
            </div>` : ''}
            ${d.memory_items.length ? `
            <div class="glass-panel" style="padding:1.5rem;border-radius:1.25rem;margin-bottom:1.25rem">
                <div class="section-title emerald">${ICON.activity}STRUCTURED MEMORY (${d.memory_items.length})</div>
                <div id="struct-mem-table"></div>
            </div>` : ''}
            ${d.summaries.length ? `
            <div class="glass-panel" style="padding:1.5rem;border-radius:1.25rem;margin-bottom:1.25rem">
                <div class="section-title rose">${ICON.activity}CONVERSATION SUMMARIES</div>
                <div style="display:flex;flex-direction:column;gap:0.75rem">${d.summaries.map(s => `
                    <div style="padding:1rem;background:rgba(2,6,23,0.4);border-radius:0.75rem;border:1px solid var(--border-subtle)">
                        <div style="display:flex;justify-content:space-between;margin-bottom:0.5rem"><span class="mono" style="font-size:0.7rem;color:var(--text-faint)">${escapeHtml(s.project_id)}</span><span class="mono" style="font-size:0.7rem;color:var(--text-faint)">${fmtDate(s.updated_at)}</span></div>
                        <div style="font-size:0.8125rem;color:var(--text-secondary);line-height:1.55;white-space:pre-wrap">${escapeHtml(truncate(s.content, 600))}</div>
                    </div>`).join('')}
                </div>
            </div>` : ''}
            <button class="btn btn-primary" id="open-chat-btn" style="width:100%;padding:1.5rem 2rem;font-size:1.0625rem;font-weight:700;letter-spacing:0.02em;background:linear-gradient(135deg,var(--accent-1) 0%,var(--accent-2) 100%);border:none;box-shadow:0 4px 20px rgba(99,102,241,0.25),0 0 0 1px rgba(168,85,247,0.1);transition:all 0.2s ease;display:flex;align-items:center;justify-content:center;gap:0.75rem" onmouseover="this.style.transform='translateY(-2px)';this.style.boxShadow='0 8px 30px rgba(99,102,241,0.4),0 0 0 1px rgba(168,85,247,0.2)'" onmouseout="this.style.transform='';this.style.boxShadow='0 4px 20px rgba(99,102,241,0.25),0 0 0 1px rgba(168,85,247,0.1)'">${ICON.chevron} <span>View Full Chat History</span> <span style="font-size:0.75rem;font-weight:500;opacity:0.85;background:rgba(255,255,255,0.15);padding:0.2rem 0.5rem;border-radius:0.375rem">full history →</span></button>
        `;
        if (d.pinned_memories.length) DataTable.mount('pinned-mem-table', {
            columns: [
                { key: 'key', label: 'Key', search: true, format: v => `<span class="mono" style="color:#a5b4fc">${escapeHtml(v)}</span>` },
                { key: 'value', label: 'Value', search: true, format: v => escapeHtml(truncate(v, 80)) },
                { key: 'confidence', label: 'Conf', sortable: true, align: 'right', format: v => `<span class="mono">${Number(v).toFixed(2)}</span>` },
                { key: 'project_id', label: 'Project', search: true },
            ],
            rows: d.pinned_memories, pageSize: 10,
            emptyState: { title: 'No pinned memory' },
        });
        if (d.memory_items.length) DataTable.mount('struct-mem-table', {
            columns: [
                { key: 'memory_type', label: 'Type', filter: [{label:'All',v:null}, ...[...new Set(d.memory_items.map(m => m.memory_type))].map(t => ({label:t, v:t}))], format: v => badge('accent', v) },
                { key: 'content', label: 'Content', search: true, format: (v, r) => escapeHtml(truncate(r.subject ? `${r.subject} → ${r.predicate || ''} → ${r.object || ''}` : (v || ''), 100)) },
                { key: 'salience', label: 'Salience', sortable: true, align: 'right', format: v => `<span class="mono">${Number(v||0).toFixed(2)}</span>` },
            ],
            rows: d.memory_items, pageSize: 10,
            emptyState: { title: 'No structured memory' },
        });
        document.getElementById('open-chat-btn').addEventListener('click', () => loadUserChat(userId, userName));
    } catch (e) { toast(e.message, 'err'); }
}

async function loadUserChat(userId, userName) {
    switchTenantView('chat');
    const el = document.getElementById('user-chat-content');
    el.innerHTML = '<div style="text-align:center;padding:2rem;color:var(--text-muted)">Loading…</div>';

    try {
        const data = await api(`/v1/admin/users/${encodeURIComponent(userId)}/messages?project_id=${encodeURIComponent(ADMIN.tenant.selectedTenant || '')}`);

        document.getElementById('chat-header-info').innerHTML = `${escapeHtml(userName)} • ${data.length} messages`;

        const openBtn = document.getElementById('open-chat-viewer-btn');
        openBtn.onclick = () => {
            const chatUrl = `/chat.html?user_id=${encodeURIComponent(userId)}&project_id=${encodeURIComponent(ADMIN.tenant.selectedTenant || '')}&user_name=${encodeURIComponent(userName)}`;
            window.open(chatUrl, '_blank');
        };

        if (!data.length) {
            el.innerHTML = `<div class="dt-empty" style="margin:auto">${ICON.activity}<div class="title">No messages</div><div class="hint">User hasn't chatted yet in this project</div></div>`;
            return;
        }

        const sorted = [...data].sort((a, b) => new Date(a.created_at) - new Date(b.created_at));
        const pairs = [];
        for (let i = 0; i < sorted.length; i++) {
            const m = sorted[i];
            if (m.role !== 'user') continue;
            const next = sorted[i + 1];
            pairs.push({
                idx: pairs.length + 1,
                time: m.created_at,
                request: m.content || '',
                response: (next && next.role === 'assistant') ? (next.content || '') : '',
                response_at: (next && next.role === 'assistant') ? next.created_at : null,
                session_id: m.session_id || '',
                summarized: !!m.is_summarized,
            });
        }

        // Compact timeline-style list
        const trunc = (s, n) => s.length > n ? s.slice(0, n) + '…' : s;
        const timeShort = (iso) => { try { const d = new Date(iso); return d.toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'}); } catch { return ''; } };
        const dateKey = (iso) => { try { return new Date(iso).toLocaleDateString('en-GB',{day:'2-digit',month:'short'}); } catch { return ''; } };

        let html = '';
        let lastDate = '';
        for (const p of pairs) {
            const dk = dateKey(p.time);
            if (dk !== lastDate) { html += `<div style="font-size:0.85rem;color:var(--text-muted);padding:0.8rem 0 0.3rem;font-weight:700;letter-spacing:0.04em;text-transform:uppercase">${dk}</div>`; lastDate = dk; }
            const sumBadge = p.summarized ? ' <span style="background:rgba(245,158,11,0.2);color:#fbbf24;padding:0.1rem 0.5rem;border-radius:3px;font-size:0.7rem;vertical-align:middle">SUM</span>' : '';
            const respPreview = p.response ? `<span style="color:#6ee7b7">${escapeHtml(trunc(p.response, 240))}</span>` : '<span style="color:var(--text-faint);font-style:italic">no reply</span>';
            html += `
            <div class="chat-pair-row" data-idx="${p.idx - 1}" style="display:grid;grid-template-columns:5rem 1fr;gap:0.3rem 1rem;padding:0.7rem 0.8rem;border-bottom:1px solid rgba(255,255,255,0.04);cursor:pointer;transition:background 0.15s;line-height:1.5" onmouseenter="this.style.background='rgba(255,255,255,0.03)'" onmouseleave="this.style.background='transparent'">
                <div style="grid-row:1/3;display:flex;flex-direction:column;align-items:flex-end;gap:0.2rem;padding-top:0.15rem">
                    <span class="mono" style="font-size:0.85rem;color:var(--text-muted)">${timeShort(p.time)}</span>
                    <span style="font-size:0.7rem;color:var(--text-faint)">#${p.idx}</span>
                    ${sumBadge}
                </div>
                <div style="font-size:1rem;color:var(--text-strong);white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${escapeHtml(p.request)}">▸ ${escapeHtml(trunc(p.request, 280))}</div>
                <div style="font-size:0.95rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding-left:1.2rem" title="${escapeHtml(p.response || '')}">↳ ${respPreview}</div>
            </div>`;
        }

        el.innerHTML = `<div style="font-size:0.95rem">${html}</div>`;

        // Click handler: expand pair detail
        el.querySelectorAll('.chat-pair-row').forEach(row => {
            row.addEventListener('click', () => {
                const idx = parseInt(row.dataset.idx);
                if (pairs[idx]) showAuditDetail(pairs[idx]);
            });
        });

    } catch (e) {
        el.innerHTML = `<div class="dt-empty" style="margin:auto"><div class="title">Error loading messages</div><div class="hint">${escapeHtml(e.message)}</div></div>`;
    }
}

/* ====== Command Palette ====== */
const CMD_ITEMS = [
    { section: 'Navigation' },
    { id: 'nav-dashboard', label: 'Dashboard', hint: '1', icon: 'activity', action: () => showTab('dashboard') },
    { id: 'nav-gpu', label: 'GPU Command', hint: '2', icon: 'cpu', action: () => showTab('gpu') },
    { id: 'nav-keys', label: 'Access Keys', hint: '3', icon: 'key', action: () => showTab('management') },
    { id: 'nav-knowledge', label: 'RAG Knowledge', hint: '4', icon: 'brain', action: () => showTab('knowledge') },
    { id: 'nav-tenants', label: 'Tenants & Users', hint: '5', icon: 'users', action: () => showTab('tenants') },
    { id: 'nav-system', label: 'System Health', hint: '6', icon: 'cpu', action: () => showTab('system') },
    { section: 'Actions' },
    { id: 'act-refresh', label: 'Refresh Current Tab', hint: 'R', icon: 'refresh', action: () => refreshCurrentTab() },
    { id: 'act-live', label: 'Toggle Live Mode', hint: 'L', icon: 'activity', action: () => toggleLive() },
    { id: 'act-apikey', label: 'Set API Key', icon: 'key', action: () => document.getElementById('api-key-btn').click() },
    { id: 'act-theme', label: 'Toggle Theme', hint: 'T', icon: 'info', action: () => toggleTheme() },
];

function openCmdPalette() {
    if (ADMIN.cmdPaletteOpen) return;
    ADMIN.cmdPaletteOpen = true;
    const overlay = document.createElement('div');
    overlay.className = 'cmd-overlay';
    overlay.id = 'cmd-palette';
    overlay.innerHTML = `
        <div class="cmd-box">
            <div class="cmd-input-wrap">
                ${ICON.search}
                <input class="cmd-input" placeholder="Type a command or search..." autofocus />
            </div>
            <div class="cmd-results" id="cmd-results"></div>
            <div class="cmd-footer">
                <span><kbd>↑↓</kbd> Navigate</span>
                <span><kbd>↵</kbd> Select</span>
                <span><kbd>Esc</kbd> Close</span>
            </div>
        </div>`;
    document.body.appendChild(overlay);

    const input = overlay.querySelector('.cmd-input');
    const resultsEl = overlay.querySelector('#cmd-results');
    let activeIdx = 0;

    function renderResults(q = '') {
        const ql = q.toLowerCase().trim();
        const filtered = CMD_ITEMS.filter(item => {
            if (item.section) return !ql; // show sections only when no query
            if (!ql) return true;
            return item.label.toLowerCase().includes(ql) || (item.hint || '').toLowerCase().includes(ql);
        });

        let html = '', itemIdx = 0;
        for (const item of filtered) {
            if (item.section) {
                html += `<div class="cmd-section">${item.section}</div>`;
            } else {
                const isActive = itemIdx === activeIdx;
                html += `<div class="cmd-item ${isActive ? 'active' : ''}" data-cmd-idx="${itemIdx}">
                    ${ICON[item.icon] || ICON.info}
                    <span class="cmd-label">${escapeHtml(item.label)}</span>
                    ${item.hint ? `<span class="cmd-hint">${escapeHtml(item.hint)}</span>` : ''}
                </div>`;
                itemIdx++;
            }
        }
        resultsEl.innerHTML = html || '<div style="padding:1.5rem;text-align:center;color:var(--text-faint);font-size:0.8125rem">No results</div>';

        resultsEl.querySelectorAll('.cmd-item').forEach(el => {
            el.addEventListener('click', () => {
                const idx = parseInt(el.getAttribute('data-cmd-idx'));
                const actions = CMD_ITEMS.filter(i => !i.section);
                if (actions[idx]) { actions[idx].action(); closeCmdPalette(); }
            });
            el.addEventListener('mouseenter', () => {
                activeIdx = parseInt(el.getAttribute('data-cmd-idx'));
                renderResults(input.value);
            });
        });
    }

    function closeCmdPalette() {
        ADMIN.cmdPaletteOpen = false;
        overlay.remove();
    }

    input.addEventListener('input', () => { activeIdx = 0; renderResults(input.value); });
    overlay.addEventListener('click', e => { if (e.target === overlay) closeCmdPalette(); });
    input.addEventListener('keydown', e => {
        const actions = CMD_ITEMS.filter(i => !i.section);
        if (e.key === 'Escape') { closeCmdPalette(); }
        else if (e.key === 'ArrowDown') { e.preventDefault(); activeIdx = Math.min(activeIdx + 1, actions.length - 1); renderResults(input.value); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); activeIdx = Math.max(activeIdx - 1, 0); renderResults(input.value); }
        else if (e.key === 'Enter') { if (actions[activeIdx]) { actions[activeIdx].action(); closeCmdPalette(); } }
    });

    renderResults();
    setTimeout(() => input.focus(), 50);
}

/* ====== Keyboard Shortcuts ====== */
document.addEventListener('keydown', e => {
    // Don't trigger in inputs
    const tag = (e.target.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || tag === 'select') {
        if (e.key === 'Escape') e.target.blur();
        return;
    }

    // Ctrl/Cmd + K → command palette
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
        e.preventDefault();
        if (ADMIN.cmdPaletteOpen) { document.getElementById('cmd-palette')?.remove(); ADMIN.cmdPaletteOpen = false; }
        else openCmdPalette();
        return;
    }

    // Single key shortcuts (only when no modal open)
    if (document.querySelector('.modal-overlay, .cmd-overlay')) return;

    const key = e.key.toLowerCase();
    if (key === '1') showTab('dashboard');
    else if (key === '2') showTab('gpu');
    else if (key === '3') showTab('management');
    else if (key === '4') showTab('knowledge');
    else if (key === '5') showTab('tenants');
    else if (key === '6') showTab('system');
    else if (key === 'r') refreshCurrentTab();
    else if (key === 'l') toggleLive();
    else if (key === 't') toggleTheme();
});

function refreshCurrentTab() {
    const active = document.querySelector('.tab-link.active');
    if (active) showTab(active.getAttribute('data-tab'));
}

function toggleLive() {
    document.getElementById('auto-refresh-btn')?.click();
}

function startAutoRefresh(intervalMs = 3000) {
    if (ADMIN.autoTimer) clearInterval(ADMIN.autoTimer);
    ADMIN.autoTimer = setInterval(refreshDashboard, intervalMs);
    const btn = document.getElementById('auto-refresh-btn');
    if (btn) btn.innerHTML = '🟢 LIVE: 3s';
}

function stopAutoRefresh() {
    if (ADMIN.autoTimer) {
        clearInterval(ADMIN.autoTimer);
        ADMIN.autoTimer = null;
    }
    const btn = document.getElementById('auto-refresh-btn');
    if (btn) btn.innerHTML = '⚪ LIVE: OFF';
}

/* ====== Theme Toggle ====== */
function applyTheme(theme) {
    ADMIN.theme = theme;
    localStorage.setItem('admin-theme', theme);
    const btn = document.getElementById('theme-toggle-btn');
    if (btn) {
        btn.innerHTML = theme === 'dark'
            ? '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>'
            : '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/></svg>';
    }
    // Light theme overrides
    if (theme === 'light') {
        document.documentElement.style.setProperty('--bg-base', '#f8fafc');
        document.documentElement.style.setProperty('--bg-panel', 'rgba(255,255,255,0.7)');
        document.documentElement.style.setProperty('--bg-elevated', 'rgba(255,255,255,0.85)');
        document.documentElement.style.setProperty('--bg-input', '#ffffff');
        document.documentElement.style.setProperty('--border-subtle', 'rgba(0,0,0,0.08)');
        document.documentElement.style.setProperty('--border-strong', 'rgba(0,0,0,0.15)');
        document.documentElement.style.setProperty('--text-primary', '#0f172a');
        document.documentElement.style.setProperty('--text-secondary', '#475569');
        document.documentElement.style.setProperty('--text-muted', '#94a3b8');
        document.documentElement.style.setProperty('--text-faint', '#cbd5e1');
        document.documentElement.style.setProperty('--shadow-soft', '0 2px 18px rgba(0,0,0,0.06)');
        document.documentElement.style.setProperty('--shadow-glow', '0 8px 32px rgba(99,102,241,0.12)');
        document.body.style.backgroundImage = 'none';
        document.body.style.backgroundColor = 'var(--bg-base)';
    } else {
        // Reset to dark defaults
        document.documentElement.style.setProperty('--bg-base', '#020617');
        document.documentElement.style.setProperty('--bg-panel', 'rgba(30, 41, 59, 0.35)');
        document.documentElement.style.setProperty('--bg-elevated', 'rgba(15, 23, 42, 0.65)');
        document.documentElement.style.setProperty('--bg-input', '#0b1224');
        document.documentElement.style.setProperty('--border-subtle', 'rgba(255,255,255,0.06)');
        document.documentElement.style.setProperty('--border-strong', 'rgba(255,255,255,0.14)');
        document.documentElement.style.setProperty('--text-primary', '#f8fafc');
        document.documentElement.style.setProperty('--text-secondary', '#94a3b8');
        document.documentElement.style.setProperty('--text-muted', '#64748b');
        document.documentElement.style.setProperty('--text-faint', '#475569');
        document.documentElement.style.setProperty('--shadow-soft', '0 2px 18px rgba(2,6,23,0.4)');
        document.documentElement.style.setProperty('--shadow-glow', '0 8px 32px rgba(99,102,241,0.18)');
        document.body.style.backgroundImage = '';
        document.body.style.backgroundColor = '';
    }
}

function toggleTheme() {
    applyTheme(ADMIN.theme === 'dark' ? 'light' : 'dark');
}

/* ====== Skeleton Loading for Stat Cards ====== */
function showStatSkeletons() {
    const grid = document.querySelector('.grid-stat-cards');
    if (!grid) return;
    grid.innerHTML = Array.from({length: 4}, () => `
        <div class="skel-stat-card">
            <div class="dt-skel skel-line" style="width:40%;height:10px"></div>
            <div class="dt-skel skel-line" style="width:70%;height:28px;margin-top:0.75rem"></div>
            <div class="dt-skel skel-line" style="width:50%;height:10px;margin-top:0.75rem"></div>
        </div>
    `).join('');
}

function restoreStatCards() {
    const grid = document.querySelector('.grid-stat-cards');
    if (!grid) return;
    grid.innerHTML = `
        <div class="stat-card indigo">
            <div class="stat-icon"><svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg></div>
            <div class="stat-label">Total Pulse</div>
            <div id="total-requests" class="stat-value">0</div>
            <div id="success-error" class="stat-sub mono">0 OK · 0 ERR</div>
        </div>
        <div class="stat-card emerald">
            <div class="stat-icon"><svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg></div>
            <div class="stat-label">Latency p95</div>
            <div id="latency-p95" class="stat-value" style="color:#6ee7b7">0ms</div>
            <div id="latency-detail" class="stat-sub mono">avg 0ms · max 0ms</div>
        </div>
        <div class="stat-card amber">
            <div class="stat-icon"><svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/></svg></div>
            <div class="stat-label">GPU Slots</div>
            <div id="gpu-active" class="stat-value">0/0</div>
            <div id="gpu-queue-text" class="stat-sub mono">0 free</div>
        </div>
        <div class="stat-card violet">
            <div class="stat-icon"><svg width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 000 7h5a3.5 3.5 0 010 7H6"/></svg></div>
            <div class="stat-label">Cost This Month</div>
            <div id="month-cost" class="stat-value">$0.00</div>
            <div id="total-cost" class="stat-sub mono">total $0.00</div>
        </div>`;
}

/* ====== CountUp Effect ====== */
function countUp(el, target, prefix = '', suffix = '') {
    const start = parseFloat(el.textContent.replace(/[^0-9.-]/g, '')) || 0;
    const diff = target - start;
    if (Math.abs(diff) < 0.01) { el.textContent = prefix + target.toLocaleString(undefined, {maximumFractionDigits:2}) + suffix; return; }
    const duration = 600;
    const startTime = performance.now();
    function tick(now) {
        const elapsed = now - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
        const current = start + diff * eased;
        el.textContent = prefix + current.toLocaleString(undefined, {maximumFractionDigits: target % 1 === 0 ? 0 : 2}) + suffix;
        if (progress < 1) requestAnimationFrame(tick);
        else el.textContent = prefix + target.toLocaleString(undefined, {maximumFractionDigits: target % 1 === 0 ? 0 : 2}) + suffix;
    }
    requestAnimationFrame(tick);
}

/* ====== File Drop Zone ====== */
function initDropZone() {
    const zone = document.getElementById('rag-drop-zone');
    const fileInput = document.getElementById('rag-file-input');
    const filenameEl = document.getElementById('rag-filename');
    const contentEl = document.getElementById('rag-content');
    if (!zone || !fileInput) return;

    zone.addEventListener('click', () => fileInput.click());
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('dragover'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
    zone.addEventListener('drop', e => {
        e.preventDefault(); zone.classList.remove('dragover');
        const file = e.dataTransfer.files[0];
        if (file) readFileIntoTextarea(file);
    });
    fileInput.addEventListener('change', () => {
        if (fileInput.files[0]) readFileIntoTextarea(fileInput.files[0]);
    });

    function readFileIntoTextarea(file) {
        if (file.size > 512000) { toast('File too large (max 500KB)', 'warn'); return; }
        const reader = new FileReader();
        reader.onload = e => {
            contentEl.value = e.target.result;
            filenameEl.textContent = `${file.name} (${(file.size/1024).toFixed(1)}KB)`;
            if (!document.getElementById('rag-title').value) {
                document.getElementById('rag-title').value = file.name.replace(/\.[^.]+$/, '');
            }
            toast(`File loaded: ${file.name}`, 'ok');
        };
        reader.readAsText(file);
    }
}

/* ====== System Health Tab ====== */
async function refreshSystemHealth() {
    const grid = document.getElementById('health-grid');
    if (!grid) return;
    grid.innerHTML = Array.from({length:3}, () => `<div class="health-card"><div class="dt-skel" style="width:40px;height:40px;border-radius:0.75rem"></div><div style="flex:1"><div class="dt-skel" style="width:60%;height:12px;margin-bottom:0.5rem"></div><div class="dt-skel" style="width:40%;height:10px"></div></div></div>`).join('');

    try {
        const providers = await api('/v1/admin/health/providers');
        grid.innerHTML = (providers || []).map(p => {
            const status = p.status === 'ok' ? 'ok' : p.status === 'degraded' ? 'warn' : 'err';
            const icon = status === 'ok' ? ICON.check : status === 'warn' ? ICON.warn : ICON.err;
            return `<div class="health-card ${status}">
                <div class="health-icon ${status}">${icon}</div>
                <div class="health-info">
                    <div class="health-name">${escapeHtml(p.name || p.provider || 'Unknown')}</div>
                    <div class="health-detail">${escapeHtml(p.detail || p.status || '')}</div>
                </div>
                ${p.latency_ms != null ? `<div class="health-latency">${Math.round(p.latency_ms)}ms</div>` : ''}
            </div>`;
        }).join('');
    } catch (e) {
        grid.innerHTML = `<div style="grid-column:1/-1;color:var(--text-faint);text-align:center;padding:2rem">Could not load provider health: ${escapeHtml(e.message)}</div>`;
    }
}

async function refreshSecurityLogs() {
    const el = document.getElementById('log-viewer');
    if (!el) return;
    el.textContent = 'Loading...';
    try {
        const stats = await api('/v1/admin/stats');
        const recent = stats.recent || [];
        if (!recent.length) { el.textContent = 'No recent activity'; return; }
        const errors = recent.filter(r => r.status_code >= 400);
        el.innerHTML = recent.slice(0, 50).map(r => {
            const cls = r.status_code >= 500 ? 'error' : r.status_code >= 400 ? 'warn' : 'info';
            return `<div class="log-line ${cls}">[${fmtTime(r.created_at)}] ${r.status_code} ${escapeHtml(r.project_id || '')} — ${escapeHtml(r.model || '')} ${Math.round(r.latency_ms || 0)}ms</div>`;
        }).join('');
    } catch (e) { el.textContent = 'Error: ' + e.message; }
}

function updateSystemCharts(stats) {
    const costEl = document.getElementById('cost-chart');
    const modelEl = document.getElementById('model-usage-chart');
    const series = stats.cost_series_7d || [];
    const byModel = stats.by_model || [];

    if (costEl) {
        const labels = series.map(d => d.day);
        const data = series.map(d => d.cost_usd);
        if (!ADMIN.charts.cost) {
            ADMIN.charts.cost = new Chart(costEl.getContext('2d'), {
                type: 'bar',
                data: { labels, datasets: [{ label: 'Cost USD', data, backgroundColor: 'rgba(99,102,241,0.55)', borderColor: 'rgba(99,102,241,1)', borderWidth: 1.5, borderRadius: 4 }] },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => '$' + ctx.raw.toFixed(4) } } },
                    scales: {
                        y: { grid: { color: 'rgba(255,255,255,0.03)' }, ticks: { color: '#475569', font: { size: 10 }, callback: v => '$' + v.toFixed(4) } },
                        x: { grid: { display: false }, ticks: { color: '#475569', font: { size: 10 } } },
                    },
                },
            });
        } else {
            ADMIN.charts.cost.data.labels = labels;
            ADMIN.charts.cost.data.datasets[0].data = data;
            ADMIN.charts.cost.update('none');
        }
    }

    if (modelEl && byModel.length) {
        const labels = byModel.map(m => m.model || 'unknown');
        const data = byModel.map(m => m.requests);
        const palette = ['rgba(99,102,241,0.8)', 'rgba(16,185,129,0.8)', 'rgba(245,158,11,0.8)', 'rgba(168,85,247,0.8)', 'rgba(239,68,68,0.8)'];
        if (!ADMIN.charts.modelUsage) {
            ADMIN.charts.modelUsage = new Chart(modelEl.getContext('2d'), {
                type: 'doughnut',
                data: { labels, datasets: [{ data, backgroundColor: palette, borderColor: 'rgba(0,0,0,0.3)', borderWidth: 2 }] },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    plugins: { legend: { position: 'right', labels: { color: '#94a3b8', font: { size: 11 }, padding: 12, boxWidth: 12 } }, tooltip: { callbacks: { label: ctx => `${ctx.label}: ${ctx.raw} req` } } },
                    cutout: '62%',
                },
            });
        } else {
            ADMIN.charts.modelUsage.data.labels = labels;
            ADMIN.charts.modelUsage.data.datasets[0].data = data;
            ADMIN.charts.modelUsage.update('none');
        }
    }
}

async function refreshSystemCharts() {
    try {
        const stats = await api('/v1/admin/stats');
        updateSystemCharts(stats);
    } catch (_) {}
}

function initSystemTab() {
    const healthBtn = document.getElementById('refresh-health-btn');
    const logsBtn = document.getElementById('refresh-logs-btn');
    if (healthBtn) healthBtn.addEventListener('click', refreshSystemHealth);
    if (logsBtn) logsBtn.addEventListener('click', refreshSecurityLogs);
}

/* ====== Tabs ====== */
function showTab(tabId) {
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.querySelectorAll('.tab-link').forEach(l => l.classList.remove('active'));
    const tab = document.getElementById('tab-' + tabId);
    if (tab) tab.classList.add('active');
    const link = document.querySelector(`.tab-link[data-tab="${tabId}"]`);
    if (link) link.classList.add('active');
    const titleMap = { dashboard: 'System Overview', gpu: 'GPU Command Center', management: 'Access Keys', knowledge: 'RAG Knowledge', tenants: 'Tenants & Users', audit: 'Chat Audit', ihi: 'IHI Monitor', database: 'Database Explorer', system: 'System Health', skills: 'Skill Registry' };
    setText('current-tab-title', titleMap[tabId] || tabId);
    if (tabId === 'dashboard') { showStatSkeletons(); restoreStatCards(); refreshDashboard(); }
    if (tabId === 'gpu') refreshDashboard();
    if (tabId === 'management') { populateTenantSelector(); refreshKeys(); refreshSessions(); }
    if (tabId === 'knowledge') refreshKnowledge();
    if (tabId === 'tenants') loadTenants();
    if (tabId === 'audit') initAuditTab();
    if (tabId === 'database') initDatabaseTab();
    if (tabId === 'skills') initSkillsTab();
    if (tabId === 'ihi') initIHITab();
    if (tabId === 'system') { refreshSystemHealth(); refreshSecurityLogs(); refreshSystemCharts(); }
}

/* ====== IHI Tab ====== */
let ihiHistory = [];

function escHtml(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function ihiAddHistory(entry) {
    ihiHistory.unshift(entry);
    if (ihiHistory.length > 50) ihiHistory.pop();
    const el = document.getElementById('ihi-history-log');
    if (!el) return;
    el.textContent = ihiHistory.map(h =>
        '[' + h.time + '] ' + h.alert + ' | devices: ' + (h.devices || []).join(',') +
        (h.case_id ? ' | case=' + h.case_id : '') +
        (h.confidence ? ' conf=' + h.confidence.toFixed(2) : '')
    ).join('\n');
    el.scrollTop = 0;
}

function renderRagTable(cases) {
    const tbody = document.getElementById('rag-table-body');
    const empty = document.getElementById('rag-table-empty');
    if (!tbody) return;
    const filter = (document.getElementById('rag-filter-input')?.value || '').toLowerCase();
    const sevFilter = document.getElementById('rag-severity-filter')?.value || '';
    const filtered = cases.filter(c => {
        if (sevFilter && c.severity !== sevFilter) return false;
        if (filter) {
            const s = (c.case_id || '') + (c.symptom || '') + (c.description || '') + JSON.stringify(c.pattern || {});
            return s.toLowerCase().includes(filter);
        }
        return true;
    });
    if (!filtered.length) {
        tbody.innerHTML = '';
        if (empty) empty.style.display = '';
        return;
    }
    if (empty) empty.style.display = 'none';
    tbody.innerHTML = filtered.map(c => {
        const sevCls = c.severity === 'CRITICAL' ? 'badge-critical' : c.severity === 'WARNING' ? 'badge-warning' : 'badge-info';
        const sevBadge = '<span class="badge ' + sevCls + '">' + escHtml(c.severity || '') + '</span>';
        const statusCls = c.status === 'active' ? 'badge-ok' : 'badge-muted';
        const statusBadge = '<span class="badge ' + statusCls + '">' + escHtml(c.status || '') + '</span>';
        const p = c.pattern || {};
        const patternStr = Object.keys(p).filter(k => p[k] != null).map(k => k.replace('_', '') + '=' + p[k]).join(', ') || '-';
        return '<tr>' +
            '<td class="mono" style="font-size:0.7rem">' + escHtml(c.case_id || '') + '</td>' +
            '<td>' + sevBadge + '</td>' +
            '<td style="font-weight:600">' + escHtml(c.symptom || '') + '</td>' +
            '<td class="mono" style="font-size:0.65rem; color:var(--text-muted)">' + escHtml(patternStr) + '</td>' +
            '<td style="max-width:180px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap" title="' + escHtml(c.description || '') + '">' + escHtml((c.description || '').slice(0, 50)) + '</td>' +
            '<td class="mono" style="text-align:right">' + (c.match_count || 0) + '</td>' +
            '<td>' + statusBadge + '</td>' +
            '</tr>';
    }).join('');
}

async function loadRagCases() {
    try {
        const res = await api('/v1/ihi/rag');
        return res || [];
    } catch (e) { return []; }
}

async function refreshRagTable() {
    const cases = await loadRagCases();
    renderRagTable(cases);
}

function initIHITab() {
    // Wire analyze button
    document.getElementById('ihi-analyze-btn')?.addEventListener('click', async () => {
        const ts = document.getElementById('ihi-ts')?.value || '';
        const deviceId = document.getElementById('ihi-device-id')?.value || '';
        const temp = parseFloat(document.getElementById('ihi-temp')?.value);
        const vib = parseFloat(document.getElementById('ihi-vib')?.value);
        const current = parseFloat(document.getElementById('ihi-current')?.value);
        if (!deviceId) { toast('Device ID required', 'warn'); return; }
        const payload = { ts, data: [{ id: deviceId, t: isNaN(temp) ? null : temp, v: isNaN(vib) ? null : vib, c: isNaN(current) ? null : current }] };
        try {
            toast('Sending analyze request...', 'info');
            const res = await api('/v1/ihi/analyze', { method: 'POST', body: payload });
            const resultEl = document.getElementById('ihi-analyze-result');
            const outputEl = document.getElementById('ihi-analyze-output');
            if (resultEl) resultEl.style.display = '';
            if (outputEl) outputEl.textContent = JSON.stringify(res, null, 2);
            ihiAddHistory({ time: new Date().toLocaleTimeString(), alert: res.alert || '?', devices: res.devices || [], case_id: res.case_id, confidence: res.confidence });
            toast('Analyze complete: ' + (res.alert || '?'), res.alert === 'DANGER' ? 'err' : 'ok');
        } catch (e) { toast('Analyze failed: ' + e.message, 'err'); }
    });

    // Wire rag create button
    document.getElementById('rag-create-btn')?.addEventListener('click', async () => {
        const ts = document.getElementById('rag-fb-ts')?.value || '';
        const deviceId = document.getElementById('rag-fb-device')?.value || '';
        const severity = document.getElementById('rag-fb-severity')?.value || 'CRITICAL';
        const description = document.getElementById('rag-fb-desc')?.value || '';
        const resolution = document.getElementById('rag-fb-resolution')?.value || '';
        if (!deviceId || !description) { toast('Device ID and Description required', 'warn'); return; }
        const payload = { ts, device_id: deviceId, severity, description, ...(resolution ? { resolution } : {}) };
        try {
            toast('Creating RAG case...', 'info');
            const res = await api('/v1/ihi/rag/feedback', { method: 'POST', body: payload });
            const resultEl = document.getElementById('rag-create-result');
            const outputEl = document.getElementById('rag-create-output');
            if (resultEl) resultEl.style.display = '';
            if (outputEl) outputEl.textContent = JSON.stringify(res, null, 2);
            toast('RAG case created: ' + (res.case_id || '?'), 'ok');
            await refreshRagTable();
        } catch (e) { toast('Create failed: ' + e.message, 'err'); }
    });

    // Wire filters and refresh
    document.getElementById('ihi-refresh-rag-btn')?.addEventListener('click', refreshRagTable);
    document.getElementById('rag-filter-input')?.addEventListener('input', refreshRagTable);
    document.getElementById('rag-severity-filter')?.addEventListener('change', refreshRagTable);
    document.getElementById('ihi-clear-history-btn')?.addEventListener('click', () => {
        ihiHistory = [];
        const el = document.getElementById('ihi-history-log');
        if (el) el.textContent = 'No requests sent yet.';
    });

    refreshRagTable();
}

/* ====== Chat Audit Tab ====== */
function initAuditTab() {
    const loadBtn = document.getElementById('audit-load-btn');
    if (loadBtn) {
        loadBtn.addEventListener('click', loadAuditMessages);
    }
    populateAuditSelectors();
}

async function populateAuditSelectors() {
    try {
        // Tenants → audit-project-id
        const tenants = await api('/v1/admin/tenants');
        const psel = document.getElementById('audit-project-id');
        if (psel) {
            psel.innerHTML = '<option value="">-- All projects --</option>' +
                tenants.map(t => `<option value="${escapeHtml(t.project_id)}">${escapeHtml(t.project_id)} (${t.total_requests || 0} req)</option>`).join('');
        }
        // Recent users via management sessions
        const sessions = await api('/v1/admin/management/sessions?limit=200');
        const usel = document.getElementById('audit-user-id');
        if (usel) {
            const seen = new Set();
            const users = [];
            for (const s of sessions) {
                if (s.user_id && !seen.has(s.user_id)) {
                    seen.add(s.user_id);
                    users.push(s);
                }
            }
            usel.innerHTML = '<option value="">-- Select user (200 most recent) --</option>' +
                users.map(u => {
                    const label = u.user_name || (u.user_id ? u.user_id.slice(0, 8) : '?');
                    const uid = u.user_id || '';
                    return `<option value="${escapeHtml(uid)}">${escapeHtml(label)} • ${escapeHtml(u.tenant_id || '?')} (${escapeHtml(uid.slice(0, 8))}…)</option>`;
                }).join('');
        }
    } catch (e) {
        console.error('populateAuditSelectors failed', e);
    }
}

async function loadAuditMessages() {
    const userId = document.getElementById('audit-user-id').value.trim();
    const projectId = document.getElementById('audit-project-id').value.trim();
    const container = document.getElementById('audit-messages-container');

    if (!userId) {
        toast('User ID required', 'warn');
        return;
    }

    container.innerHTML = '<div id="audit-table"></div>';
    DataTable.setLoading('audit-table');

    try {
        const params = new URLSearchParams();
        if (projectId) params.append('project_id', projectId);
        params.append('limit', '200');

        const messages = await api(`/v1/admin/users/${encodeURIComponent(userId)}/messages?${params.toString()}`);

        if (!messages || messages.length === 0) {
            container.innerHTML = '<div style="text-align:center;color:var(--text-muted);padding:2rem">No messages found</div>';
            return;
        }

        const pairs = [];
        for (let i = 0; i < messages.length; i += 2) {
            const userMsg = messages[i];
            const assistantMsg = messages[i + 1];
            if (userMsg && userMsg.role === 'user') {
                pairs.push({
                    idx: pairs.length + 1,
                    time: userMsg.created_at,
                    request: userMsg.content || '',
                    response: assistantMsg ? (assistantMsg.content || '') : '',
                    response_at: assistantMsg ? assistantMsg.created_at : null,
                    session_id: userMsg.session_id || '',
                    request_obj: userMsg,
                    response_obj: assistantMsg,
                });
            }
        }

        AUDIT_PAIRS = pairs;

        DataTable.mount('audit-table', {
            columns: [
                { key: 'idx', label: '#', sortable: true, align: 'right' },
                { key: 'time', label: 'Time', sortable: true, format: (_, r) => `<span class="mono" style="font-size:0.7rem">${fmtDateTime(r.time)}</span>` },
                { key: 'session_id', label: 'Session', search: true, format: (v) => `<span class="mono" style="font-size:0.65rem;color:var(--text-faint)" title="${escapeHtml(v || '')}">${escapeHtml((v || '').slice(0, 8))}</span>` },
                { key: 'request', label: 'Request', search: true, format: (v) => `<span title="${escapeHtml(v || '')}">${escapeHtml((v || '').slice(0, 100))}${(v || '').length > 100 ? '…' : ''}</span>` },
                { key: 'response', label: 'Response', search: true, format: (v) => v ? `<span style="color:#6ee7b7" title="${escapeHtml(v)}">${escapeHtml(v.slice(0, 100))}${v.length > 100 ? '…' : ''}</span>` : '<span style="color:var(--text-faint);font-style:italic">no reply</span>' },
            ],
            rows: pairs,
            pageSize: 30,
            rowActions: (r) => [
                { icon: 'eye', tooltip: 'View full', onClick: () => showAuditDetail(r) },
            ],
            emptyState: { icon: 'database', title: 'No messages' },
        });

        toast(`Loaded ${pairs.length} request/response pairs`, 'ok');
    } catch (e) {
        container.innerHTML = `<div style="color:var(--status-err)">${escapeHtml(e.message)}</div>`;
        toast(e.message, 'err');
    }
}

let AUDIT_PAIRS = [];

function showAuditDetail(pair) {
    const html = `
        <div style="display:flex;flex-direction:column;gap:0.75rem;max-height:70vh;overflow-y:auto">
            <div>
                <div style="display:flex;justify-content:space-between;font-size:0.7rem;color:var(--text-muted);margin-bottom:0.3rem">
                    <span style="font-weight:700;color:var(--accent-1)">REQUEST #${pair.idx}</span>
                    <span class="mono">${fmtDateTime(pair.time)}</span>
                </div>
                <div style="background:rgba(0,0,0,0.3);padding:0.75rem;border-radius:0.375rem;font-size:0.85rem;color:var(--text-strong);font-family:'JetBrains Mono',monospace;white-space:pre-wrap;word-break:break-word;border-left:3px solid var(--accent-1)">${escapeHtml(pair.request)}</div>
            </div>
            ${pair.response ? `
                <div>
                    <div style="display:flex;justify-content:space-between;font-size:0.7rem;color:var(--text-muted);margin-bottom:0.3rem">
                        <span style="font-weight:700;color:#6ee7b7">RESPONSE</span>
                        <span class="mono">${fmtDateTime(pair.response_at)}</span>
                    </div>
                    <div style="background:rgba(110,231,183,0.08);padding:0.75rem;border-radius:0.375rem;font-size:0.85rem;color:var(--text-strong);font-family:'JetBrains Mono',monospace;white-space:pre-wrap;word-break:break-word;border-left:3px solid #6ee7b7">${escapeHtml(pair.response)}</div>
                </div>
            ` : '<div style="color:var(--text-muted);font-style:italic">No response yet</div>'}
            ${pair.session_id ? `<div style="font-size:0.65rem;color:var(--text-faint)" class="mono">session: ${escapeHtml(pair.session_id)}</div>` : ''}
        </div>
    `;
    previewDialog(`Message detail`, html);
}

/* ====== Database Tab ====== */
const DB_TAB = { tables: [], selected: null, sqlMode: false, lastResult: null };

const DB_RELATIONS = {
    users: [
        { table: 'sessions', fk: 'user_id' },
        { table: 'messages', fk: 'user_id' },
        { table: 'api_keys', fk: 'owner_user_id' },
        { table: 'usage_events', fk: 'user_id' },
        { table: 'memory_episodes', fk: 'user_id' },
        { table: 'pinned_memories', fk: 'user_id' },
        { table: 'memory_boundaries', fk: 'user_id' },
        { table: 'failure_risk_events', fk: 'user_id' },
    ],
    sessions: [
        { table: 'messages', fk: 'session_id' },
        { table: 'summaries', fk: 'session_id' },
    ],
    knowledge_cards: [
        { table: 'knowledge_card_chunks', fk: 'card_id' },
    ],
};

const DB_SAVED_KEY = 'db_saved_queries_v1';
const DB_SAVED_PRESETS = [
    { name: 'Recent messages (50)', query: "SELECT id, user_id, role, LEFT(content, 80) AS preview, created_at FROM messages ORDER BY created_at DESC LIMIT 50" },
    { name: 'Top 10 users by message count', query: "SELECT user_id, COUNT(*) AS msgs FROM messages GROUP BY user_id ORDER BY msgs DESC LIMIT 10" },
    { name: 'Active API keys', query: "SELECT id, name, owner_user_id, tenant_id, rpm, monthly_budget_usd, is_admin, enabled FROM api_keys WHERE enabled = true" },
    { name: 'Tenants summary', query: "SELECT tenant_id, project_id, COUNT(DISTINCT user_id) AS users, COUNT(*) AS sessions FROM sessions GROUP BY tenant_id, project_id ORDER BY sessions DESC" },
    { name: 'Usage last 24h', query: "SELECT provider, model, COUNT(*) AS calls, SUM(prompt_tokens) AS in_tok, SUM(completion_tokens) AS out_tok FROM usage_events WHERE created_at > NOW() - INTERVAL '24 hours' GROUP BY provider, model" },
];

async function initDatabaseTab() {
    const refreshBtn = document.getElementById('db-refresh-tables-btn');
    const toggleBtn = document.getElementById('db-toggle-sql-btn');
    const runBtn = document.getElementById('db-run-sql-btn');
    const sqlInput = document.getElementById('db-sql-input');
    const exportBtn = document.getElementById('db-export-csv-btn');
    const savedSel = document.getElementById('db-saved-queries');
    const saveBtn = document.getElementById('db-save-query-btn');
    const delBtn = document.getElementById('db-delete-query-btn');

    if (!refreshBtn._wired) {
        refreshBtn.addEventListener('click', loadDbTables);
        toggleBtn.addEventListener('click', toggleDbSqlPanel);
        runBtn.addEventListener('click', runDbSql);
        exportBtn.addEventListener('click', exportDbCsv);
        saveBtn.addEventListener('click', saveDbQuery);
        delBtn.addEventListener('click', deleteDbQuery);
        savedSel.addEventListener('change', loadSavedQuery);
        sqlInput.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); runDbSql(); }
        });
        refreshBtn._wired = true;
    }
    renderSavedQueries();
    await loadDbTables();
}

async function loadDbTables() {
    const list = document.getElementById('db-tables-list');
    list.innerHTML = '<div class="mono" style="color:var(--text-muted); padding:0.5rem; font-size:0.7rem">Loading...</div>';
    try {
        const data = await api('/v1/admin/db/tables');
        DB_TAB.tables = data.tables || [];
        renderDbTablesList();
    } catch (e) {
        list.innerHTML = `<div style="color:var(--status-err); padding:0.5rem; font-size:0.7rem">${escapeHtml(e.message)}</div>`;
    }
}

function renderDbTablesList() {
    const list = document.getElementById('db-tables-list');
    if (!DB_TAB.tables.length) {
        list.innerHTML = '<div class="mono" style="color:var(--text-muted); padding:0.5rem; font-size:0.7rem">No tables found</div>';
        return;
    }
    list.innerHTML = DB_TAB.tables.map(t => {
        const active = DB_TAB.selected === t.name;
        return `<button class="db-table-row ${active ? 'active' : ''}" data-table="${escapeHtml(t.name)}" style="display:flex; justify-content:space-between; align-items:center; width:100%; padding:0.5rem 0.7rem; background:${active ? 'rgba(56,189,248,0.12)' : 'transparent'}; border:none; border-radius:0.375rem; color:${active ? 'var(--accent-1)' : 'var(--text-strong)'}; cursor:pointer; font-size:0.75rem; text-align:left; margin-bottom:0.15rem">
            <span class="mono">${escapeHtml(t.name)}</span>
            <span class="mono" style="font-size:0.65rem; color:var(--text-faint)">${t.row_count}</span>
        </button>`;
    }).join('');
    list.querySelectorAll('.db-table-row').forEach(b => {
        b.addEventListener('click', () => previewDbTable(b.getAttribute('data-table')));
    });
}

async function previewDbTable(name) {
    DB_TAB.selected = name;
    renderDbTablesList();
    setText('db-current-table', `Preview: ${name}`);
    DataTable.setLoading('db-result-table');
    try {
        const data = await api(`/v1/admin/db/preview/${encodeURIComponent(name)}?limit=100`);
        renderDbResult(data);
    } catch (e) {
        document.getElementById('db-result-table').innerHTML = `<div style="color:var(--status-err); padding:1rem">${escapeHtml(e.message)}</div>`;
    }
}

function renderDbResult(data) {
    DB_TAB.lastResult = data;
    const cols = (data.columns || []).map(c => ({
        key: c, label: c, search: true, sortable: true,
        format: (v) => formatDbCell(v, c, data.table),
    }));
    DataTable.mount('db-result-table', {
        columns: cols,
        rows: data.rows || [],
        pageSize: 25,
        emptyState: { icon: 'database', title: 'No rows', hint: data.table ? `Table ${data.table} is empty` : '' },
    });
    const meta = data.elapsed_ms !== undefined
        ? `${data.row_count || (data.rows || []).length} rows · ${data.elapsed_ms} ms${data.capped ? ' · capped' : ''}`
        : `${(data.rows || []).length}/${data.total ?? '?'} rows`;
    setText('db-sql-status', meta);
    wireRelationChips();
}

function formatDbCell(v, colKey, currentTable) {
    if (v === null || v === undefined) return '<span style="color:var(--text-faint); font-style:italic">null</span>';
    if (typeof v === 'object') {
        const s = JSON.stringify(v);
        return `<span class="mono" title="${escapeHtml(s)}">${escapeHtml(s.length > 80 ? s.slice(0, 80) + '…' : s)}</span>`;
    }
    const s = String(v);
    if (currentTable && colKey === 'id' && DB_RELATIONS[currentTable]) {
        const chips = DB_RELATIONS[currentTable].map(rel =>
            `<a class="db-rel-chip mono" data-table="${escapeHtml(rel.table)}" data-fk="${escapeHtml(rel.fk)}" data-val="${escapeHtml(s)}" style="font-size:0.6rem; padding:0.1rem 0.4rem; border-radius:3px; background:rgba(56,189,248,0.1); color:var(--accent-1); cursor:pointer; margin-left:0.3rem; border:1px solid rgba(56,189,248,0.2)" title="Show ${escapeHtml(rel.table)}.${escapeHtml(rel.fk)} = ${escapeHtml(s.slice(0,8))}">→${escapeHtml(rel.table)}</a>`
        ).join('');
        const truncated = s.length > 36 ? s.slice(0, 8) + '…' : s;
        return `<span class="mono" title="${escapeHtml(s)}">${escapeHtml(truncated)}</span>${chips}`;
    }
    if (s.length > 120) return `<span title="${escapeHtml(s)}">${escapeHtml(s.slice(0, 120))}…</span>`;
    return escapeHtml(s);
}

function wireRelationChips() {
    document.querySelectorAll('#db-result-table .db-rel-chip').forEach(el => {
        if (el._wired) return;
        el._wired = true;
        el.addEventListener('click', (e) => {
            e.preventDefault();
            const tbl = el.getAttribute('data-table');
            const fk = el.getAttribute('data-fk');
            const val = el.getAttribute('data-val');
            DB_TAB.sqlMode = true;
            document.getElementById('db-sql-panel').style.display = 'block';
            const sql = `SELECT * FROM "${tbl}" WHERE "${fk}" = '${val.replace(/'/g, "''")}' ORDER BY 1 DESC LIMIT 100`;
            document.getElementById('db-sql-input').value = sql;
            runDbSql();
        });
    });
}

function exportDbCsv() {
    const data = DB_TAB.lastResult;
    if (!data || !(data.rows || []).length) { toast('No rows to export', 'warn'); return; }
    const cols = data.columns || Object.keys(data.rows[0] || {});
    const escape = (v) => {
        if (v === null || v === undefined) return '';
        const s = typeof v === 'object' ? JSON.stringify(v) : String(v);
        return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const lines = [cols.join(',')].concat(data.rows.map(r => cols.map(c => escape(r[c])).join(',')));
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const name = data.table || 'query';
    a.href = url; a.download = `${name}_${Date.now()}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(url);
    toast(`Exported ${data.rows.length} rows`, 'ok');
}

function loadSavedList() {
    try { return JSON.parse(localStorage.getItem(DB_SAVED_KEY) || '[]'); } catch { return []; }
}
function persistSavedList(list) {
    localStorage.setItem(DB_SAVED_KEY, JSON.stringify(list));
}

function renderSavedQueries() {
    const sel = document.getElementById('db-saved-queries');
    if (!sel) return;
    const user = loadSavedList();
    const opts = ['<option value="">— Saved queries —</option>'];
    if (DB_SAVED_PRESETS.length) {
        opts.push('<optgroup label="Presets">');
        DB_SAVED_PRESETS.forEach((p, i) => opts.push(`<option value="preset:${i}">${escapeHtml(p.name)}</option>`));
        opts.push('</optgroup>');
    }
    if (user.length) {
        opts.push('<optgroup label="My queries">');
        user.forEach((p, i) => opts.push(`<option value="user:${i}">${escapeHtml(p.name)}</option>`));
        opts.push('</optgroup>');
    }
    sel.innerHTML = opts.join('');
}

function loadSavedQuery() {
    const sel = document.getElementById('db-saved-queries');
    const v = sel.value;
    if (!v) return;
    const [src, idx] = v.split(':');
    const list = src === 'preset' ? DB_SAVED_PRESETS : loadSavedList();
    const item = list[+idx];
    if (item) document.getElementById('db-sql-input').value = item.query;
}

async function saveDbQuery() {
    const sql = document.getElementById('db-sql-input').value.trim();
    if (!sql) { toast('Nothing to save', 'warn'); return; }
    const html = `<input id="save-name-input" class="input" placeholder="Query name" autofocus />`;
    const ok = await openModal({ title: 'Save query', desc: 'Stored in browser localStorage', contentHtml: html, confirmText: 'Save' });
    if (!ok) return;
    const name = document.getElementById('save-name-input').value.trim();
    if (!name) return;
    const list = loadSavedList();
    list.push({ name, query: sql });
    persistSavedList(list);
    renderSavedQueries();
    toast('Query saved', 'ok');
}

function deleteDbQuery() {
    const sel = document.getElementById('db-saved-queries');
    const v = sel.value;
    if (!v.startsWith('user:')) { toast('Select one of your saved queries', 'warn'); return; }
    const idx = +v.split(':')[1];
    const list = loadSavedList();
    if (!list[idx]) return;
    list.splice(idx, 1);
    persistSavedList(list);
    renderSavedQueries();
    toast('Deleted', 'ok');
}

function toggleDbSqlPanel() {
    DB_TAB.sqlMode = !DB_TAB.sqlMode;
    document.getElementById('db-sql-panel').style.display = DB_TAB.sqlMode ? 'block' : 'none';
}

async function runDbSql() {
    const sql = document.getElementById('db-sql-input').value.trim();
    if (!sql) { toast('Enter a SELECT query', 'warn'); return; }
    setText('db-sql-status', 'Running...');
    DataTable.setLoading('db-result-table');
    setText('db-current-table', 'Custom SQL result');
    DB_TAB.selected = null;
    renderDbTablesList();
    try {
        const data = await api('/v1/admin/db/query', { method: 'POST', body: JSON.stringify({ query: sql }) });
        renderDbResult(data);
        toast(`${data.row_count} rows · ${data.elapsed_ms}ms`, 'ok');
    } catch (e) {
        document.getElementById('db-result-table').innerHTML = `<div style="color:var(--status-err); padding:1rem">${escapeHtml(e.message)}</div>`;
        setText('db-sql-status', 'Error');
        toast(e.message, 'err');
    }
}

/* ====== Bootstrap ====== */
function bindStaticEvents() {
    document.querySelectorAll('.tab-link').forEach(b => b.addEventListener('click', () => showTab(b.getAttribute('data-tab'))));

    // Forward API key to cross-page links (e.g. /ihi-charts-v2.html, /ihi-feed-v3.html).
    // Those pages accept ?key=... and persist to localStorage on load.
    document.querySelectorAll('a.needs-key').forEach(a => {
        a.addEventListener('click', (e) => {
            if (!ADMIN.apiKey) { /* let browser navigate normally; target page will prompt */ return; }
            e.preventDefault();
            const url = new URL(a.getAttribute('data-href') || a.getAttribute('href'), location.origin);
            url.searchParams.set('key', ADMIN.apiKey);
            window.open(url.toString(), '_blank');
        });
    });

    document.getElementById('api-key-btn').addEventListener('click', async () => {
        const html = `<input id="apikey-input" class="input mono" type="password" placeholder="Paste your master/admin key" value="${escapeHtml(ADMIN.apiKey)}"/>`;
        const v = await openModal({
            title: 'Set Admin Key',
            desc: 'API key được lưu trong localStorage của trình duyệt.',
            contentHtml: html,
            confirmText: 'Save',
            onConfirm: (overlay) => overlay.querySelector('#apikey-input')?.value || '',
        });
        if (v) { ADMIN.apiKey = v; localStorage.setItem('apiKey', v); toast('API key saved', 'ok'); refreshDashboard(); }
    });

    document.getElementById('auto-refresh-btn').addEventListener('click', () => {
        const btn = document.getElementById('auto-refresh-btn');
        if (ADMIN.autoTimer) {
            clearInterval(ADMIN.autoTimer); ADMIN.autoTimer = null;
            btn.innerHTML = '⚪ LIVE: OFF';
        } else {
            ADMIN.autoTimer = setInterval(refreshDashboard, 2000);
            btn.innerHTML = '🟢 LIVE: 2s';
            refreshDashboard();
        }
    });

    document.getElementById('switch-lite').addEventListener('click', () => switchModel('lite'));
    document.getElementById('switch-thinking').addEventListener('click', () => switchModel('thinking'));

    const createKeyBtn = document.getElementById('create-key-btn');
    if (createKeyBtn) createKeyBtn.addEventListener('click', async () => {
        const name = document.getElementById('key-name').value.trim();
        if (!name) { toast('Key label required', 'warn'); return; }
        try {
            const res = await api('/v1/admin/keys', { method: 'POST', body: {
                name, tenant_id: document.getElementById('key-tenant').value || 'default',
                is_admin: document.getElementById('key-admin').checked,
                rpm_limit: parseInt(document.getElementById('key-rpm').value) || 60,
            } });
            await previewDialog('🎉 Token Minted', `<div style="font-size:0.7rem;color:var(--text-faint);margin-bottom:0.5rem;text-transform:uppercase;letter-spacing:0.1em">SAVE THIS KEY — IT WON'T BE SHOWN AGAIN</div><div class="mono" style="font-size:0.875rem;color:#a5b4fc;word-break:break-all">${escapeHtml(res.key)}</div>`);
            document.getElementById('key-name').value = '';
            refreshKeys();
        } catch (e) { toast(e.message, 'err'); }
    });

    const uploadBtn = document.getElementById('upload-rag-btn');
    if (uploadBtn) uploadBtn.addEventListener('click', async () => {
        const project = document.getElementById('rag-project').value.trim();
        const title = document.getElementById('rag-title').value.trim();
        const content = document.getElementById('rag-content').value.trim();
        const domain = document.getElementById('rag-domain').value || 'general';
        if (!project || !title || !content) { toast('Project, title & content required', 'warn'); return; }
        try {
            await api('/v1/admin/knowledge/upload', { method: 'POST', body: { project_id: project, title, content, domain } });
            toast('Card vectorized', 'ok');
            document.getElementById('rag-content').value = '';
            document.getElementById('rag-title').value = '';
            refreshKnowledge();
        } catch (e) { toast(e.message, 'err'); }
    });

    const reindexBtn = document.getElementById('reindex-btn');
    if (reindexBtn) reindexBtn.addEventListener('click', reindexKnowledge);

    const cmdBtn = document.getElementById('cmd-palette-btn');
    if (cmdBtn) cmdBtn.addEventListener('click', openCmdPalette);

    const themeBtn = document.getElementById('theme-toggle-btn');
    if (themeBtn) themeBtn.addEventListener('click', toggleTheme);
}

async function switchModel(mode) {
    const ok = await confirmDialog(`Switch to ${mode.toUpperCase()} model?`, 'Việc này sẽ stop llama.cpp instance hiện tại và start lại với model mới. Có thể mất 30–60s.', { confirmText: 'Switch' });
    if (!ok) return;
    document.getElementById(`switch-${mode}`).style.opacity = '0.5';
    try {
        await api('/v1/admin/model/switch', { method: 'POST', body: { mode } });
        toast(`Switched to ${mode}`, 'ok');
    } catch (e) { toast(e.message, 'err'); }
    finally { document.getElementById(`switch-${mode}`).style.opacity = '1'; }
}

document.addEventListener('DOMContentLoaded', () => {
    bindStaticEvents();
    initDropZone();
    initSystemTab();
    applyTheme(ADMIN.theme);
    showTab('dashboard');
    // Start auto-refresh by default so dashboard shows live data without
    // requiring the user to click anything. Refresh every 3s.
    if (ADMIN.apiKey) {
        startAutoRefresh(3000);
    }
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('/admin-sw.js').catch(() => {});
    }
});

/* ====== Skills Tab (MUSE-Autoskill) ====== */
function initSkillsTab() {
    const loadBtn = document.getElementById('btn-load-skills');
    if (loadBtn) loadBtn.addEventListener('click', loadSkills);
    const filterInput = document.getElementById('skills-filter');
    if (filterInput) filterInput.addEventListener('input', () => loadSkills());
    loadSkills();
}

async function loadSkills() {
    const projectId = document.getElementById('skills-project-id').value.trim() || 'fanpage';
    const filter = document.getElementById('skills-filter').value.trim().toLowerCase();
    const includeInactive = document.getElementById('skills-show-inactive').checked;
    DataTable.setLoading('skills-table-container');
    const container = document.getElementById('skills-table-container');
    try {
        const data = await api(`/v1/projects/${encodeURIComponent(projectId)}/skills?include_inactive=${includeInactive}`);
        const skills = (data.skills || []).filter(s => !filter || s.name.toLowerCase().includes(filter));
        DataTable.mount('skills-table-container', {
            columns: [
                { key: 'name', label: 'Name' },
                { key: 'description', label: 'Description', render: v => v || '—' },
                { key: 'version', label: 'Ver', render: v => `v${v}` },
                { key: 'eval_score', label: 'Score', render: v => v != null ? v.toFixed(2) : '—' },
                {
                    key: 'is_active',
                    label: 'Active',
                    render: v => `<span class="chip ${v ? 'chip-ok' : 'chip-fail'}">${v ? 'Yes' : 'No'}</span>`
                },
                {
                    key: 'last_evaluated_at',
                    label: 'Last Eval',
                    render: v => v ? new Date(v).toLocaleString() : 'Never'
                },
                {
                    key: 'actions',
                    label: 'Actions',
                    sortable: false,
                    render: (_, row) => `
                        <div style="display:flex;gap:0.4rem;align-items:center">
                            <button class="btn btn-sm btn-secondary" onclick="evaluateSkill('${row.id}', '${projectId}')">Test</button>
                            <button class="btn btn-sm btn-secondary" onclick="openSkillEdit('${row.id}', '${projectId}')">Edit</button>
                            <button class="btn btn-sm btn-danger" onclick="deleteSkill('${row.id}', '${projectId}')">Del</button>
                        </div>`
                }
            ],
            rows: skills,
            key: 'id',
        });
    } catch (e) {
        container.innerHTML = `<div class="callout callout-err">${e.message}</div>`;
    }
}

async function evaluateSkill(skillId, projectId) {
    toast('Evaluating skill…', 'info');
    try {
        const r = await api(`/v1/projects/${encodeURIComponent(projectId)}/skills/${skillId}/evaluate`, { method: 'POST' });
        toast(`Eval: ${(r.score * 100).toFixed(0)}% — ${r.passed}/${r.total} passed`, 'ok');
        loadSkills();
    } catch (e) { toast('Eval failed: ' + e.message, 'err'); }
}

function openSkillEdit(skillId, projectId) {
    const modal = document.getElementById('skill-edit-modal');
    if (!skillId || skillId === 'new') {
        document.getElementById('skill-edit-id').value = '';
        document.getElementById('skill-edit-name').value = '';
        document.getElementById('skill-edit-desc').value = '';
        document.getElementById('skill-edit-patterns').value = '[]';
        document.getElementById('skill-edit-template').value = '';
        document.getElementById('skill-edit-expected').value = '';
        document.getElementById('skill-edit-tests').value = '[]';
    }
    modal.style.display = 'flex';
}

function closeSkillModal() {
    document.getElementById('skill-edit-modal').style.display = 'none';
}

async function saveSkill() {
    const projectId = document.getElementById('skills-project-id').value.trim() || 'fanpage';
    const skillId = document.getElementById('skill-edit-id').value;
    const payload = {
        name: document.getElementById('skill-edit-name').value.trim(),
        description: document.getElementById('skill-edit-desc').value.trim(),
    };
    let patterns, tests;
    try { patterns = JSON.parse(document.getElementById('skill-edit-patterns').value); } catch { patterns = []; }
    try { tests = JSON.parse(document.getElementById('skill-edit-tests').value); } catch { tests = []; }
    payload.trigger_patterns = patterns;
    payload.prompt_template = document.getElementById('skill-edit-template').value;
    payload.expected_behavior = document.getElementById('skill-edit-expected').value;
    payload.test_cases = tests;
    try {
        if (skillId) {
            await api(`/v1/projects/${encodeURIComponent(projectId)}/skills/${skillId}`, { method: 'PATCH', body: payload });
            toast('Skill updated', 'ok');
        } else {
            await api(`/v1/projects/${encodeURIComponent(projectId)}/skills`, { method: 'POST', body: payload });
            toast('Skill created', 'ok');
        }
        closeSkillModal();
        loadSkills();
    } catch (e) { toast('Save failed: ' + e.message, 'err'); }
}

async function deleteSkill(skillId, projectId) {
    const ok = await confirmDialog('Delete Skill', 'Delete this skill permanently?');
    if (!ok) return;
    try {
        await api(`/v1/projects/${encodeURIComponent(projectId)}/skills/${skillId}`, { method: 'DELETE' });
        toast('Skill deleted', 'ok');
        loadSkills();
    } catch (e) { toast('Delete failed: ' + e.message, 'err'); }
}
