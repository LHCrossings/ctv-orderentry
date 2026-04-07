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
            <tr>
                <td class="filename" title="${esc(o.filename)}">${esc(o.filename)}</td>
                <td><span class="agency-badge ${o.order_type === 'Unknown' ? 'unknown' : ''}">${esc(o.order_type)}</span></td>
                <td class="meta">${esc(o.customer_name)}</td>
                <td class="meta">${o.size_kb} KB &nbsp;·&nbsp; ${esc(o.modified)}</td>
                <td><button class="delete-btn" onclick="deleteOrder('${esc(o.filename)}')">Delete</button></td>
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

// ── Auto-refresh every 10s ─────────────────────────────────────────────────

loadQueue();
setInterval(loadQueue, 10000);

document.getElementById('refresh-btn').addEventListener('click', loadQueue);
