/* CTV Order Entry - Frontend */

const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');
const uploadStatus = document.getElementById('upload-status');
const queueBody = document.getElementById('queue-body');
const queueCount = document.getElementById('queue-count');

// ── Drag and drop ──────────────────────────────────────────────────────────

dropZone.addEventListener('dragenter', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragover',  e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', e => { dropZone.classList.remove('drag-over'); });
dropZone.addEventListener('drop', e => {
    e.preventDefault();
    dropZone.classList.remove('drag-over');
    const files = Array.from(e.dataTransfer.files);
    files.forEach(uploadFile);
});

dropZone.addEventListener('click', () => fileInput.click());
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
        const res = await fetch('/api/orders/upload', { method: 'POST', body: form });
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

// ── Queue ──────────────────────────────────────────────────────────────────

async function loadQueue() {
    try {
        const res = await fetch('/api/orders');
        const orders = await res.json();

        queueCount.textContent = orders.length;
        queueCount.className = 'queue-count' + (orders.length === 0 ? ' zero' : '');

        if (orders.length === 0) {
            queueBody.innerHTML = `
                <tr><td colspan="5">
                    <div class="empty-state">
                        <div class="empty-icon">📭</div>
                        <p>No orders in queue. Drop a PDF above to get started.</p>
                    </div>
                </td></tr>`;
            return;
        }

        queueBody.innerHTML = orders.map(o => `
            <tr class="clickable" onclick="showDetail('${esc(o.filename)}', '${esc(o.order_type)}')">
                <td class="filename" title="${esc(o.filename)}">${esc(o.filename)}</td>
                <td><span class="agency-badge ${o.order_type === 'Unknown' ? 'unknown' : ''}">${esc(o.order_type)}</span></td>
                <td class="meta">${esc(o.customer_name)}</td>
                <td class="meta">${o.size_kb} KB &nbsp;·&nbsp; ${esc(o.modified)}</td>
                <td><button class="delete-btn" onclick="event.stopPropagation(); deleteOrder('${esc(o.filename)}')">Delete</button></td>
            </tr>`).join('');
    } catch (err) {
        console.error('Failed to load queue:', err);
    }
}

async function deleteOrder(filename) {
    if (!confirm(`Delete "${filename}"?`)) return;
    try {
        const res = await fetch('/api/orders/' + encodeURIComponent(filename), { method: 'DELETE' });
        const data = await res.json();
        if (!res.ok) {
            alert(data.detail || 'Delete failed.');
        } else {
            await loadQueue();
        }
    } catch (err) {
        alert('Delete error: ' + err.message);
    }
}

function esc(str) {
    return String(str ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
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

async function showDetail(filename, orderType) {
    // Reset state
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
        const res = await fetch('/api/orders/' + encodeURIComponent(filename) + '/detail');
        const d = await res.json();
        detailLoading.classList.add('hidden');

        if (d.error) {
            detailError.textContent = d.error;
            detailError.classList.remove('hidden');
            return;
        }

        // Update header
        if (d.client) detailTitle.textContent = d.client;

        // Warnings
        if (d.warnings && d.warnings.length > 0) {
            detailWarnings.innerHTML = '⚠️ ' + d.warnings.join('<br>⚠️ ');
            detailWarnings.classList.remove('hidden');
        }

        // Meta grid
        const metaFields = [
            ['Agency',    orderType],
            ['Client',    d.client],
            ['Estimate',  d.estimate_number],
            ['Market(s)', (d.markets || []).join(', ')],
            ['Flight',    [d.flight_start, d.flight_end].filter(Boolean).join(' – ')],
            ['Buyer',     d.buyer],
            ['Total Spots', d.total_spots ? d.total_spots.toLocaleString() : null],
            ['Total Cost',  d.total_cost  ? '$' + d.total_cost.toLocaleString('en-US', {minimumFractionDigits:2}) : null],
        ];
        detailMeta.innerHTML = metaFields.map(([label, val]) => `
            <div class="meta-item">
                <span class="meta-label">${esc(label)}</span>
                <span class="meta-value ${val ? '' : 'empty'}">${val ? esc(String(val)) : '—'}</span>
            </div>`).join('');

        // Lines
        if (d.lines && d.lines.length > 0) {
            detailLinesBody.innerHTML = d.lines.map((ln, i) => `
                <tr>
                    <td class="meta">${i + 1}</td>
                    <td class="filename" style="max-width:220px" title="${esc(ln.description)}">${esc(ln.description)}</td>
                    <td class="meta">${esc(ln.days)}</td>
                    <td class="meta">${esc(ln.time)}</td>
                    <td class="meta">${esc(ln.duration)}</td>
                    <td class="meta">${ln.weekly_spots && ln.weekly_spots.length ? ln.weekly_spots.join(', ') : '—'}</td>
                    <td class="meta">${ln.total_spots || '—'}</td>
                    <td class="meta">${ln.rate ? '$' + ln.rate.toFixed(2) : '—'}</td>
                    <td><span class="${ln.is_bonus ? 'line-bonus' : 'line-paid'}">${ln.is_bonus ? 'BNS' : 'PAID'}</span></td>
                </tr>`).join('');
            detailLines.classList.remove('hidden');
        }

    } catch (err) {
        detailLoading.classList.add('hidden');
        detailError.textContent = 'Failed to load detail: ' + err.message;
        detailError.classList.remove('hidden');
    }
}

function closeDetailModal() {
    detailOverlay.classList.add('hidden');
}

function closeDetail(event) {
    if (event.target === detailOverlay) closeDetailModal();
}

document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeDetailModal();
});

// ── Auto-refresh every 10s ─────────────────────────────────────────────────

loadQueue();
setInterval(loadQueue, 10000);

document.getElementById('refresh-btn').addEventListener('click', loadQueue);
