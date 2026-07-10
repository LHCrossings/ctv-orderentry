/* CTV Order Entry - Frontend */

const dropZone    = document.getElementById('drop-zone');
const fileInput   = document.getElementById('file-input');
const uploadStatus= document.getElementById('upload-status');
const queueBody   = document.getElementById('queue-body');
const queueCount  = document.getElementById('queue-count');
const awaitingCount = document.getElementById('awaiting-count');
const historyCount= document.getElementById('history-count');

let currentTab = 'pending';

// ── Tab switching ──────────────────────────────────────────────────────────

function switchTab(tab) {
    currentTab = tab;
    ['pending', 'awaiting', 'history'].forEach(t =>
        document.getElementById('tab-' + t).classList.toggle('active', t === tab));
    document.getElementById('drop-zone').style.display = tab === 'pending' ? '' : 'none';
    loadQueue();
}

// ── Drag and drop ──────────────────────────────────────────────────────────

dropZone.addEventListener('dragenter', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragover',  e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', e => { dropZone.classList.remove('drag-over'); });
dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    Array.from(e.dataTransfer.files).forEach(uploadFile);
});

dropZone.addEventListener('click', e => {
    if (e.target.closest('label') || e.target === fileInput) return;
    fileInput.click();
});
fileInput.addEventListener('change', () => {
    Array.from(fileInput.files).forEach(uploadFile);
    fileInput.value = '';
});

// ── Upload ─────────────────────────────────────────────────────────────────

async function uploadFile(file) {
    setStatus(`Uploading ${file.name}...`, 'info');
    const form = new FormData();
    form.append('file', file);
    try {
        const res  = await fetch('/api/orders/upload', { method: 'POST', body: form });
        const data = await res.json();
        if (!res.ok) {
            setStatus(data.detail || 'Upload failed.', 'error');
        } else {
            setStatus(data.message, 'success');
            await loadQueue();
        }
    } catch (err) {
        setStatus('Upload error: ' + err.message, 'error');
    }
}

function setStatus(msg, type) {
    uploadStatus.textContent = msg;
    uploadStatus.className = 'upload-status ' + (type || '');
    if (type === 'success') {
        setTimeout(() => { uploadStatus.textContent = ''; uploadStatus.className = 'upload-status'; }, 4000);
    }
}

// ── Queue / History ────────────────────────────────────────────────────────

async function refreshCounts() {
    // Populate the inactive tabs' badges (the active tab gets its exact count
    // from its own list load) so nothing shows a stale 0 before first visit.
    try {
        const res    = await fetch('/api/orders/counts');
        const counts = await res.json();
        const badges = { pending: queueCount, awaiting: awaitingCount, history: historyCount };
        for (const [tab, el] of Object.entries(badges)) {
            if (tab === currentTab) continue;
            el.textContent = counts[tab];
            el.className   = 'queue-count' + (counts[tab] === 0 ? ' zero' : '');
        }
    } catch (err) { /* badges are cosmetic — never block the queue on them */ }
}

async function loadQueue() {
    const isHistory  = currentTab === 'history';
    const isAwaiting = currentTab === 'awaiting';
    const isPending  = currentTab === 'pending';
    refreshCounts();
    const url = isHistory ? '/api/history'
              : isAwaiting ? '/api/orders/awaiting-backwrite'
              : '/api/orders';

    try {
        const res    = await fetch(url);
        const orders = await res.json();

        // Run button — only active on pending tab with orders present
        const runBtn = document.getElementById('run-btn');
        runBtn.style.display = isPending ? '' : 'none';
        runBtn.disabled = !isPending || orders.length === 0;

        // Update counts
        const countEl = isHistory ? historyCount : isAwaiting ? awaitingCount : queueCount;
        countEl.textContent = orders.length;
        countEl.className   = 'queue-count' + (orders.length === 0 ? ' zero' : '');

        // Reset select-all when reloading (pending only)
        const selectAll = document.getElementById('select-all');
        if (selectAll) {
            selectAll.style.display = isPending ? '' : 'none';
            selectAll.checked = false;
        }
        document.getElementById('col-check').style.display = isPending ? '' : 'none';
        document.getElementById('col-meta').textContent =
            isAwaiting ? 'Contract(s) / Entered' : 'Size / Modified';

        if (orders.length === 0) {
            const empty = isHistory
                ? { icon: '🗂️', msg: 'No completed orders in history.' }
                : isAwaiting
                ? { icon: '⏳', msg: 'No orders awaiting backwrite. Entered orders land here automatically.' }
                : { icon: '📭', msg: 'No orders in queue. Drop a PDF above to get started.' };
            queueBody.innerHTML = `<tr><td colspan="6">
                <div class="empty-state">
                    <div class="empty-icon">${empty.icon}</div>
                    <p>${empty.msg}</p>
                </div></td></tr>`;
            return;
        }

        const detailBase = isHistory ? '/api/history/' : '/api/orders/';

        queueBody.innerHTML = orders.map(o => `
            <tr class="clickable" data-filename="${esc(o.filename)}" data-order-type="${esc(o.order_type)}" data-detail-base="${esc(detailBase)}">
                <td class="cb-cell">${isPending ? `<input type="checkbox" class="order-cb" data-filename="${esc(o.filename)}">` : ''}</td>
                <td class="filename" title="${esc(o.filename)}">${esc(o.filename)}${o.io_parse_error ? ' <span class="agency-badge unknown" title="The IO could not be re-parsed — open the manifest for details">IO?</span>' : ''}</td>
                <td><span class="agency-badge ${o.order_type === 'Unknown' ? 'unknown' : ''}">${esc(o.agency_label || o.order_type)}</span></td>
                <td class="meta">${esc(o.customer_name)}</td>
                <td class="meta">${isAwaiting ? awaitingMeta(o) : `${o.size_kb} KB &nbsp;·&nbsp; ${esc(o.modified)}`}</td>
                <td>${isHistory
                    ? `<button class="restore-btn" data-filename="${esc(o.filename)}">Restore</button>`
                    : isAwaiting
                    ? (o.order_type === 'worldlink'
                        ? `<button class="restore-btn" onclick="event.stopPropagation();window.open('/backwrite','_blank')" title="WorldLink uses its dedicated backwrite flow (revision merge, MLBF tab)">Backwrite ↗</button>`
                        : `<button class="restore-btn backwrite-btn" data-filename="${esc(o.filename)}" title="Generate the backwrite Excel from Etere + this order's manifest">Backwrite</button>`)
                      + ` <button class="delete-btn awaiting-done-btn" data-filename="${esc(o.filename)}">Done</button>`
                    : `<button class="delete-btn"  data-filename="${esc(o.filename)}">Mark Done</button>`
                }</td>
            </tr>`).join('');

        // Row click → detail modal
        queueBody.querySelectorAll('tr.clickable').forEach(row => {
            row.addEventListener('click', e => {
                if (e.target.closest('button')) return;
                showDetail(row.dataset.filename, row.dataset.orderType, row.dataset.detailBase);
            });
        });

        // Pending: checkbox click stops row-click propagation
        queueBody.querySelectorAll('.order-cb').forEach(cb => {
            cb.addEventListener('click', e => e.stopPropagation());
            cb.addEventListener('change', updateRunBtn);
        });

        // Awaiting: Done → archive IO + manifest to Used
        queueBody.querySelectorAll('.awaiting-done-btn').forEach(btn => {
            btn.addEventListener('click', e => {
                e.stopPropagation();
                awaitingDone(btn.dataset.filename);
            });
        });

        // Awaiting: Backwrite → one-click generate + download + archive
        queueBody.querySelectorAll('.backwrite-btn').forEach(btn => {
            btn.addEventListener('click', e => {
                e.stopPropagation();
                doBackwrite(btn);
            });
        });

        // Pending: Mark Done → move to Used
        queueBody.querySelectorAll('.delete-btn:not(.awaiting-done-btn)').forEach(btn => {
            btn.addEventListener('click', e => {
                e.stopPropagation();
                markDone(btn.dataset.filename);
            });
        });

        // History: Restore → move back to incoming
        queueBody.querySelectorAll('.restore-btn').forEach(btn => {
            btn.addEventListener('click', e => {
                e.stopPropagation();
                restoreOrder(btn.dataset.filename);
            });
        });

        updateRunBtn();

    } catch (err) {
        console.error('Failed to load queue:', err);
    }
}

function getSelectedFiles() {
    return [...document.querySelectorAll('.order-cb:checked')].map(cb => cb.dataset.filename);
}

function updateRunBtn() {
    const runBtn = document.getElementById('run-btn');
    if (!runBtn || currentTab !== 'pending') return;
    const checked = document.querySelectorAll('.order-cb:checked').length;
    const total   = document.querySelectorAll('.order-cb').length;
    runBtn.disabled = total === 0;
    runBtn.textContent = checked > 0 ? `▶ Run Selected (${checked})` : '▶ Run Queue';
}

async function runQueue() {
    const btn = document.getElementById('run-btn');
    btn.disabled = true;
    btn.textContent = '⏳ Starting...';
    const selected = getSelectedFiles();
    const overrides = {};
    selected.forEach(fn => {
        if (pendingOverrides[fn] && Object.keys(pendingOverrides[fn]).length > 0)
            overrides[fn] = pendingOverrides[fn];
    });
    try {
        const res  = await fetch('/api/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ files: selected, overrides }),
        });
        const data = await res.json();
        if (!res.ok) {
            alert(data.detail || 'Failed to start.');
        } else if (data.terminal === 'sse') {
            openTerminal(data.files || selected);
        } else if (data.manual) {
            setStatus(data.message, 'info');
        } else {
            setStatus(data.message, 'success');
        }
    } catch (err) {
        alert('Error: ' + err.message);
    } finally {
        btn.disabled = false;
        updateRunBtn();
    }
}

// ── Web Terminal (SSE output + POST input) ────────────────────────────────

let _termEs        = null;
let _termSessionId = null;

function openTerminal(files) {
    const overlay = document.getElementById('terminal-overlay');
    overlay.classList.remove('hidden');

    const log   = document.getElementById('terminal-log');
    const input = document.getElementById('terminal-input');
    const send  = document.getElementById('terminal-send');
    log.innerHTML  = '';
    input.value    = '';
    input.disabled = true;
    send.disabled  = true;
    _termSessionId = null;

    const filesList = (files || []).filter(Boolean);
    const query = filesList.length ? '?files=' + filesList.map(encodeURIComponent).join(',') : '';

    _termEs = new EventSource('/api/terminal/stream' + query);

    _termEs.onmessage = e => {
        const msg = JSON.parse(e.data);
        if (msg.type === 'session') {
            _termSessionId = msg.id;
            input.disabled = false;
            send.disabled  = false;
            input.focus();
        } else if (msg.type === 'output') {
            _appendLog(msg.text);
        } else if (msg.type === 'done') {
            _appendLog('\n[Process finished — you may close this window]\n');
            input.disabled = true;
            send.disabled  = true;
            if (_termEs) { _termEs.close(); _termEs = null; }
        }
    };

    _termEs.onerror = () => {
        _appendLog('\n[Connection error]\n');
        input.disabled = true;
        send.disabled  = true;
    };
}

function _appendLog(text) {
    const log = document.getElementById('terminal-log');
    const span = document.createElement('span');
    span.textContent = text;
    log.appendChild(span);
    log.scrollTop = log.scrollHeight;
}

async function _sendTerminalInput() {
    const input = document.getElementById('terminal-input');
    const text = input.value;
    if (!_termSessionId || input.disabled) return;
    input.value = '';
    _appendLog(text + '\n');
    try {
        await fetch(`/api/terminal/${_termSessionId}/input`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text}),
        });
    } catch (err) {
        _appendLog(`[Send error: ${err.message}]\n`);
    }
}

function closeTerminal() {
    if (_termEs) { _termEs.close(); _termEs = null; }
    if (_termSessionId) {
        fetch(`/api/terminal/${_termSessionId}/kill`, {method: 'POST'}).catch(() => {});
        _termSessionId = null;
    }
    document.getElementById('terminal-overlay').classList.add('hidden');
}

function awaitingMeta(o) {
    const contracts = (o.contracts || [])
        .map(c => esc(c.code) + (c.etere_id ? ` <span title="Etere contract ID">(${c.etere_id})</span>` : ''))
        .join(', ') || '—';
    const entered = o.entered_at ? esc(o.entered_at.replace('T', ' ')) : '';
    return `${contracts}${entered ? ' &nbsp;·&nbsp; ' + entered : ''}`;
}

async function doBackwrite(btn) {
    const filename = btn.dataset.filename;
    btn.disabled = true;
    const oldLabel = btn.textContent;
    btn.textContent = 'Generating…';
    try {
        const res = await fetch('/api/orders/awaiting-backwrite/' + encodeURIComponent(filename) + '/backwrite',
                                { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            alert(data.detail || `Backwrite failed (${res.status}).`);
            return;
        }
        // Download the Excel
        const blob = await res.blob();
        const cd   = res.headers.get('Content-Disposition') || '';
        const mFn  = cd.match(/filename="?([^";]+)"?/);
        const a    = document.createElement('a');
        a.href     = URL.createObjectURL(blob);
        a.download = mFn ? mFn[1] : filename.replace(/\.[^.]+$/, '') + '.xlsx';
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(a.href);

        // Surface reconciliation + archive status
        const rec = res.headers.get('X-Backwrite-Reconcile');
        if (rec) {
            try {
                const r = JSON.parse(rec);
                if (r.ok === false && (r.messages || []).length) {
                    alert('Backwrite generated, but totals need a look:\n\n' + r.messages.join('\n'));
                }
            } catch (e) { /* cosmetic */ }
        }
        const archErr = res.headers.get('X-Backwrite-Archive-Error');
        if (archErr) {
            try { alert('Excel generated, but archiving failed: ' + JSON.parse(archErr)); } catch (e) {}
        }
        loadQueue();
    } catch (err) {
        alert('Backwrite error: ' + err.message);
    } finally {
        btn.disabled = false;
        btn.textContent = oldLabel;
    }
}

async function awaitingDone(filename) {
    if (!confirm(`Archive "${filename}" and its manifest to Used?`)) return;
    try {
        const res  = await fetch('/api/orders/awaiting-backwrite/' + encodeURIComponent(filename) + '/done',
                                 { method: 'POST' });
        const data = await res.json();
        if (!res.ok) { alert(data.detail || 'Archive failed.'); return; }
        loadQueue();
    } catch (err) {
        alert('Archive error: ' + err.message);
    }
}

async function markDone(filename) {
    if (!confirm(`Move "${filename}" to history?`)) return;
    try {
        const res  = await fetch('/api/orders/' + encodeURIComponent(filename), { method: 'DELETE' });
        const data = await res.json();
        if (!res.ok) alert(data.detail || 'Failed.');
        else await loadQueue();
    } catch (err) {
        alert('Error: ' + err.message);
    }
}

async function restoreOrder(filename) {
    if (!confirm(`Restore "${filename}" to the pending queue?`)) return;
    try {
        const res  = await fetch('/api/history/' + encodeURIComponent(filename) + '/restore', { method: 'POST' });
        const data = await res.json();
        if (!res.ok) alert(data.detail || 'Failed.');
        else await loadQueue();
    } catch (err) {
        alert('Error: ' + err.message);
    }
}

// ── Helpers ────────────────────────────────────────────────────────────────

function renderLineRow(ln, i) {
    return `<tr>
        <td class="meta">${i + 1}</td>
        <td class="filename" style="max-width:220px" title="${esc(ln.description)}">${esc(ln.description)}</td>
        <td class="meta">${esc(ln.days)}</td>
        <td class="meta">${esc(ln.time)}</td>
        <td class="meta">${esc(ln.duration)}</td>
        <td class="meta">${ln.weekly_spots && ln.weekly_spots.length ? ln.weekly_spots.join(', ') : '—'}</td>
        <td class="meta">${ln.total_spots || '—'}</td>
        <td class="meta">${ln.rate ? '$' + ln.rate.toFixed(2) : '—'}</td>
        <td><span class="${ln.is_bonus ? 'line-bonus' : 'line-paid'}">${ln.is_bonus ? 'BNS' : 'PAID'}</span></td>
    </tr>`;
}

function esc(str) {
    return String(str ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

// ── Pending field overrides keyed by filename (market, etc.) ──────────────

const pendingOverrides = {};

function setOverride(filename, field, value) {
    if (!pendingOverrides[filename]) pendingOverrides[filename] = {};
    if (value) {
        pendingOverrides[filename][field] = value;
    } else {
        delete pendingOverrides[filename][field];
    }
    // Live-update the meta row so the user sees the change immediately
    if (field === 'market') {
        const labels = detailMeta.querySelectorAll('.meta-label');
        const values = detailMeta.querySelectorAll('.meta-value');
        for (let i = 0; i < labels.length; i++) {
            if (labels[i].textContent === 'Market(s)') {
                values[i].textContent = value || '—';
                values[i].className = 'meta-value' + (value ? '' : ' empty');
                break;
            }
        }
    }
}

// ── Detail Modal ───────────────────────────────────────────────────────────

const detailOverlay  = document.getElementById('detail-overlay');
const detailTitle    = document.getElementById('detail-title');
const detailSubtitle = document.getElementById('detail-subtitle');
const detailWarnings = document.getElementById('detail-warnings');
const detailError    = document.getElementById('detail-error');
const detailMeta     = document.getElementById('detail-meta');
const detailLines    = document.getElementById('detail-lines-section');
const detailLinesBody= document.getElementById('detail-lines-body');
const detailLoading  = document.getElementById('detail-loading');

async function showDetail(filename, orderType, detailBase) {
    detailTitle.textContent = filename;
    detailSubtitle.textContent = orderType;
    detailWarnings.classList.add('hidden');
    detailError.classList.add('hidden');
    detailLines.classList.add('hidden');
    detailMeta.innerHTML = '';
    detailLinesBody.innerHTML = '';
    detailLoading.classList.remove('hidden');
    detailOverlay.classList.remove('hidden');

    try {
        const res = await fetch((detailBase || '/api/orders/') + encodeURIComponent(filename) + '/detail');
        const d   = await res.json();
        detailLoading.classList.add('hidden');

        if (d.error) {
            detailError.textContent = d.error;
            detailError.classList.remove('hidden');
            return;
        }

        if (d.client) detailTitle.textContent = d.client;

        if (d.warnings && d.warnings.length > 0) {
            detailWarnings.innerHTML = '⚠️ ' + d.warnings.join('<br>⚠️ ');
            detailWarnings.classList.remove('hidden');
        }

        if (d.required_fields && d.required_fields.length > 0) {
            const existing = pendingOverrides[filename] || {};
            const fieldsHtml = d.required_fields.map(f => {
                if (f.type === 'select') {
                    const opts = f.options.map(o =>
                        `<option value="${esc(o)}"${o === existing[f.field] ? ' selected' : ''}>${esc(o)}</option>`
                    ).join('');
                    return `<div class="req-field-row">
                        <label class="req-field-label">${esc(f.label)}</label>
                        <select class="req-field-select"
                            onchange="setOverride(${JSON.stringify(filename)},${JSON.stringify(f.field)},this.value)">
                            <option value="">— Select —</option>${opts}
                        </select>
                    </div>`;
                }
                return '';
            }).join('');
            detailWarnings.insertAdjacentHTML('beforeend',
                `<div class="req-fields-block">${fieldsHtml}</div>`
            );
            detailWarnings.classList.remove('hidden');
        }

        const metaFields = [
            ['Agency',      d.agency || orderType],
            ['Client',      d.client],
            ['Estimate',    d.estimate_number],
            ['Market(s)',   (d.markets || []).join(', ')],
            ['Flight',      [d.flight_start, d.flight_end].filter(Boolean).join(' – ')],
            ['Buyer',       d.buyer],
            ['Total Spots', d.total_spots ? d.total_spots.toLocaleString() : null],
            ['Total Cost',  d.total_cost  ? '$' + d.total_cost.toLocaleString('en-US', {minimumFractionDigits:2}) : null],
        ];
        detailMeta.innerHTML = metaFields.map(([label, val]) => `
            <div class="meta-item">
                <span class="meta-label">${esc(label)}</span>
                <span class="meta-value ${val ? '' : 'empty'}">${val ? esc(String(val)) : '—'}</span>
            </div>`).join('');

        if (d.sub_orders && d.sub_orders.length > 0) {
            detailLines.classList.remove('hidden');
            detailLinesBody.innerHTML = d.sub_orders.map((sub, si) => {
                const headerRow = `<tr style="background:var(--nord1)">
                    <td colspan="9" style="padding:8px 12px;color:var(--nord4);font-size:0.78rem;font-weight:600;letter-spacing:0.04em;">
                        EST ${esc(sub.estimate_number || String(si + 1))}
                        ${sub.markets && sub.markets.length ? ' · ' + esc(sub.markets.join(', ')) : ''}
                        ${sub.flight_start ? ' · ' + esc(sub.flight_start) + ' – ' + esc(sub.flight_end) : ''}
                        &nbsp;·&nbsp; ${sub.total_spots} spots
                        ${sub.total_cost ? ' &nbsp;·&nbsp; $' + sub.total_cost.toLocaleString('en-US', {minimumFractionDigits:2}) : ''}
                    </td></tr>`;
                return headerRow + (sub.lines || []).map((ln, i) => renderLineRow(ln, i)).join('');
            }).join('');
        } else if (d.lines && d.lines.length > 0) {
            detailLinesBody.innerHTML = d.lines.map((ln, i) => renderLineRow(ln, i)).join('');
            detailLines.classList.remove('hidden');
        }

    } catch (err) {
        detailLoading.classList.add('hidden');
        detailError.textContent = 'Failed to load detail: ' + err.message;
        detailError.classList.remove('hidden');
    }
}

function closeDetailModal() { detailOverlay.classList.add('hidden'); }
function closeDetail(event) { if (event.target === detailOverlay) closeDetailModal(); }
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') { closeDetailModal(); closeTerminal(); }
});
document.getElementById('terminal-input').addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.stopPropagation(); _sendTerminalInput(); }
});
document.getElementById('terminal-send').addEventListener('click', _sendTerminalInput);

// ── Select-all checkbox ────────────────────────────────────────────────────

document.getElementById('select-all').addEventListener('change', function () {
    document.querySelectorAll('.order-cb').forEach(cb => { cb.checked = this.checked; });
    updateRunBtn();
});

// ── Auto-refresh every 10s ─────────────────────────────────────────────────

loadQueue();
setInterval(loadQueue, 10000);
document.getElementById('refresh-btn').addEventListener('click', loadQueue);
document.getElementById('run-btn').addEventListener('click', runQueue);
