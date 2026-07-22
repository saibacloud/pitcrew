// PitCrew — manuals / documents

import { activeCar, api, toast, escapeHtml, renderMarkdown, aiLoadingHtml, isAiBusy, AI_BUSY_MSG, authHeaders } from './app.js';
import { pitcrewConfirm } from './dialogs.js';
import { addSavedEntry } from './journal.js';

let manuals = [];
let selectedManualIds = new Set();

export async function loadManuals() {
    if (!activeCar) return;
    selectedManualIds = new Set();
    try {
        manuals = await api('GET', `/api/cars/${activeCar.id}/manuals`);
    } catch (e) {
        manuals = [];
    }
    ['manual', 'reference', 'other'].forEach(renderManualGrid);
    updateDocAskBtn();
}

function renderManualGrid(category) {
    const grid = document.getElementById(`manual-grid-${category}`);
    if (!grid) return;
    grid.innerHTML = '';

    const items = manuals.filter(m => (m.category || 'manual') === category);
    items.forEach(m => {
        const card = document.createElement('div');
        card.className = 'manual-card' + (selectedManualIds.has(m.id) ? ' selected' : '');
        card.dataset.id = m.id;
        const date = new Date(m.created_at + 'Z').toLocaleDateString();
        card.innerHTML = `
            <div class="manual-card-actions">
                <button data-action="delete-manual" data-id="${m.id}">Remove</button>
            </div>
            <label class="manual-card-check" data-action="toggle-manual-select" data-id="${m.id}">
                <svg class="check-icon" width="14" height="14" viewBox="0 0 14 14" fill="none">
                    <rect width="14" height="14" rx="3" fill="currentColor" opacity="0.12"/>
                    <path class="check-mark" d="M3 7l3 3 5-5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" opacity="0"/>
                </svg>
            </label>
            <div class="manual-card-title">${escapeHtml(m.title)}</div>
            <div class="manual-card-date">${date}</div>
        `;
        card.addEventListener('click', e => {
            if (e.target.closest('[data-action]')) return;
            openDocFile(m);
        });
        grid.appendChild(card);
    });

    const addCard = document.createElement('div');
    addCard.className = 'add-car-card';
    addCard.innerHTML = `<div class="plus">+</div><div class="add-label">Add File</div>`;
    addCard.addEventListener('click', () => document.getElementById(`manual-file-input-${category}`).click());
    grid.appendChild(addCard);
}

function toggleManualSelect(id) {
    if (selectedManualIds.has(id)) {
        selectedManualIds.delete(id);
    } else {
        selectedManualIds.add(id);
    }
    // Update card visual
    const card = document.querySelector(`.manual-card[data-id="${id}"]`);
    if (card) card.classList.toggle('selected', selectedManualIds.has(id));
    updateDocAskBtn();
}

function updateDocAskBtn() {
    const btn = document.getElementById('doc-ask-btn');
    if (!btn) return;
    const n = selectedManualIds.size;
    btn.disabled = n === 0;
    btn.textContent = n > 0 ? `Ask AI (${n} doc${n > 1 ? 's' : ''})` : 'Ask AI';
}

async function handleManualUpload(event, category) {
    const files = Array.from(event.target.files);
    if (!files.length) return;
    for (const file of files) {
        const formData = new FormData();
        formData.append('file', file);
        try {
            const res = await fetch(`/api/cars/${activeCar.id}/manuals?category=${category}`, {
                method: 'POST', body: formData, headers: authHeaders(),
            });
            if (!res.ok) throw new Error(await res.text());
            toast(`${file.name} uploaded`);
        } catch (e) {
            toast(`Failed: ${file.name}`);
        }
    }
    event.target.value = '';
    await loadManuals();
}

async function deleteManual(id) {
    if (!await pitcrewConfirm('Remove this file?')) return;
    try {
        await api('DELETE', `/api/manuals/${id}`);
        manuals = manuals.filter(m => m.id !== id);
        selectedManualIds.delete(id);
        ['manual', 'reference', 'other'].forEach(renderManualGrid);
        updateDocAskBtn();
        toast('File removed');
    } catch (e) { toast('Error removing file'); }
}

async function askDocuments() {
    const input = document.getElementById('doc-ask-input');
    const question = input?.value?.trim();
    if (!question) { toast('Enter a question first'); return; }
    if (selectedManualIds.size === 0) { toast('Check at least one document first'); return; }
    const btn = document.getElementById('doc-ask-btn');
    btn.disabled = true;
    btn.innerHTML = aiLoadingHtml('Searching docs');
    const resultEl = document.getElementById('doc-ask-result');
    const answerEl = document.getElementById('doc-ask-answer');
    resultEl.style.display = 'none';
    try {
        const data = await api('POST', `/api/cars/${activeCar.id}/manuals/ask`, {
            question,
            manual_ids: [...selectedManualIds],
        });
        answerEl.innerHTML = '<p>' + renderMarkdown(data.answer) + '</p>';
        if (data.sources?.length) {
            answerEl.innerHTML += `<p style="margin-top:10px;font-size:11px;color:var(--muted)">Sources: ${data.sources.map(s => escapeHtml(s)).join(', ')}</p>`;
        }
        resultEl.style.display = 'block';
        addSavedEntry(data.journal_entry);
        input.value = '';
    } catch (e) {
        toast(isAiBusy(e) ? AI_BUSY_MSG : 'AI search failed');
    } finally {
        btn.disabled = selectedManualIds.size === 0;
        updateDocAskBtn();
    }
}

function openDocFile(doc) {
    const fp = doc.file_path.toLowerCase();
    if (fp.endsWith('.pdf') || /\.(jpe?g|png|webp|gif|svg|bmp)$/.test(fp)) {
        window.open(doc.file_path, '_blank', 'noopener');
    } else {
        const a = document.createElement('a');
        a.href = doc.file_path;
        a.download = doc.title;
        a.click();
    }
}

// ── Init ────────────────────────────────────────────────────────────────────

export function initManuals() {
    // Manual tab switching
    document.querySelectorAll('#section-manual .section-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.tab;
            document.querySelectorAll('#section-manual .section-tab-content').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('#section-manual .section-tab').forEach(b => b.classList.remove('active'));
            document.getElementById(`manual-tab-${tab}`).classList.add('active');
            btn.classList.add('active');
        });
    });

    // Upload inputs
    ['manual', 'reference', 'other'].forEach(cat => {
        const input = document.getElementById(`manual-file-input-${cat}`);
        if (input) input.addEventListener('change', e => handleManualUpload(e, cat));
    });

    // Ask AI
    document.getElementById('doc-ask-input').addEventListener('keydown', e => {
        if (e.key === 'Enter') askDocuments();
    });
    document.getElementById('doc-ask-btn').addEventListener('click', askDocuments);

    // Close AI response
    document.getElementById('doc-ask-close-btn').addEventListener('click', () => {
        document.getElementById('doc-ask-result').style.display = 'none';
    });

    // Delegated clicks in manual section
    document.getElementById('section-manual').addEventListener('click', e => {
        const target = e.target.closest('[data-action]');
        if (!target) return;
        e.stopPropagation();
        const action = target.dataset.action;
        const id = parseInt(target.dataset.id);
        if (action === 'delete-manual') deleteManual(id);
        if (action === 'toggle-manual-select') toggleManualSelect(id);
    });
}
