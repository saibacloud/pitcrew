// PitCrew — journal (research, notes, converse, doc search, photos)

import { activeCar, api, authHeaders, toast, escapeHtml, renderMarkdown, aiLoadingHtml, isAiBusy, AI_BUSY_MSG } from './app.js';
import { pitcrewConfirm } from './dialogs.js';

let journalEntries = [];
let journalTotal = 0;
let journalOffset = 0;
const JOURNAL_LIMIT = 50;

export function resetJournal() {
    journalEntries = [];
    journalTotal = 0;
    journalOffset = 0;
}

export async function loadJournal(append = false) {
    if (!activeCar) return;
    try {
        const data = await api('GET', `/api/cars/${activeCar.id}/journal?limit=${JOURNAL_LIMIT}&offset=${journalOffset}`);
        if (append) {
            journalEntries = journalEntries.concat(data.items);
        } else {
            journalEntries = data.items;
        }
        journalTotal = data.total;
    } catch (e) {
        journalEntries = [];
        journalTotal = 0;
    }
    renderJournalTab('research');
    renderJournalTab('converse');
    renderJournalTab('note');
    renderJournalTab('docsearch');
    renderPhotoTab();
}

function renderJournalTab(type) {
    const tbody = document.getElementById(`${type}-tbody`);
    if (!tbody) return;
    const entries = journalEntries.filter(e => e.type === type);
    if (!entries.length) {
        tbody.innerHTML = `<tr><td colspan="3" class="parts-table-empty">No entries yet</td></tr>`;
        return;
    }
    tbody.innerHTML = entries.map(e => {
        const date = new Date(e.created_at + 'Z').toLocaleDateString();

        if (type === 'research' || type === 'docsearch') {
            const hasAnswer = !!e.body;
            const answerBtn = hasAnswer
                ? `<button class="btn btn-ghost" data-action="toggle-answer" data-id="${e.id}">View Answer</button>`
                : `<button class="btn btn-ghost" disabled title="No answer stored">No Answer</button>`;
            const answerRow = hasAnswer
                ? `<tr class="research-answer-row" id="answer-${e.id}" style="display:none">
                    <td colspan="3"><div class="research-answer"><p>${renderMarkdown(e.body)}</p></div></td>
                   </tr>`
                : '';
            return `<tr>
                <td class="journal-date">${date}</td>
                <td class="journal-text">${escapeHtml(e.title || '')}</td>
                <td class="parts-table-actions">
                    ${answerBtn}
                    <button class="btn btn-ghost" data-action="delete-journal" data-id="${e.id}">Delete</button>
                </td>
            </tr>${answerRow}`;
        }

        const aiBtn = type === 'converse'
            ? `<button class="btn btn-ghost" disabled title="Coming soon">Start</button>`
            : '';
        return `<tr>
            <td class="journal-date">${date}</td>
            <td class="journal-text">${escapeHtml(e.title || '')}</td>
            <td class="parts-table-actions">
                ${aiBtn}
                <button class="btn btn-ghost" data-action="delete-journal" data-id="${e.id}">Delete</button>
            </td>
        </tr>`;
    }).join('');

    // "Load More" if there are more entries (show on first tab that has entries)
    if (type === 'research' && journalEntries.length < journalTotal) {
        tbody.innerHTML += `<tr><td colspan="3" style="text-align:center;padding:12px">
            <button class="btn btn-ghost" data-action="load-more-journal">Load More</button>
        </td></tr>`;
    }
}

function toggleResearchAnswer(id) {
    const row = document.getElementById(`answer-${id}`);
    if (!row) return;
    const shown = row.style.display !== 'none';
    row.style.display = shown ? 'none' : 'table-row';
    const btn = row.previousElementSibling.querySelector('[data-action="toggle-answer"]');
    if (btn) btn.textContent = shown ? 'View Answer' : 'Hide Answer';
}

async function runResearch() {
    const inputEl = document.getElementById('research-input');
    const query = inputEl?.value?.trim();
    if (!query) { toast('Enter a question first'); return; }
    const btn = document.getElementById('research-btn');
    btn.disabled = true;
    btn.innerHTML = aiLoadingHtml('Searching');
    try {
        const entry = await api('POST', `/api/cars/${activeCar.id}/research`, { query });
        journalEntries.unshift(entry);
        inputEl.value = '';
        renderJournalTab('research');
        toast('Done');
    } catch (e) {
        toast(isAiBusy(e) ? AI_BUSY_MSG : 'Search failed');
        console.error(e);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Search';
    }
}

async function addJournalEntry(type) {
    const inputEl = document.getElementById(`${type}-input`);
    const title = inputEl?.value?.trim();
    if (!title) { toast('Enter something first'); return; }
    try {
        const entry = await api('POST', `/api/cars/${activeCar.id}/journal`, { type, title });
        journalEntries.unshift(entry);
        inputEl.value = '';
        renderJournalTab(type);
        toast('Saved');
    } catch (e) { toast('Failed to save'); }
}

async function deleteJournalEntry(id) {
    if (!await pitcrewConfirm('Delete this journal entry?')) return;
    try {
        await api('DELETE', `/api/journal/${id}`);
        journalEntries = journalEntries.filter(e => e.id !== id);
        ['research', 'converse', 'note', 'docsearch'].forEach(renderJournalTab);
        renderPhotoTab();
        toast('Entry deleted');
    } catch (e) { toast('Error deleting entry'); }
}

export function showJournalTab(tabId, btnEl) {
    document.querySelectorAll('#section-journal .section-tab-content').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('#section-journal .section-tab').forEach(b => b.classList.remove('active'));
    document.getElementById(`journal-${tabId}`).classList.add('active');
    btnEl.classList.add('active');
}

// ── Photo journal ───────────────────────────────────────────────────────────

function renderPhotoTab() {
    const grid = document.getElementById('journal-photo-grid');
    if (!grid) return;
    const photos = journalEntries.filter(e => e.type === 'photo' && e.photo_url);
    if (!photos.length) {
        grid.innerHTML = '<p class="parts-table-empty">No photos yet</p>';
        return;
    }
    // Group by date
    const groups = {};
    for (const p of photos) {
        const date = new Date(p.created_at + 'Z').toLocaleDateString(undefined, {
            weekday: 'short', year: 'numeric', month: 'short', day: 'numeric'
        });
        if (!groups[date]) groups[date] = [];
        groups[date].push(p);
    }
    grid.innerHTML = Object.entries(groups).map(([date, items]) => `
        <div class="photo-date-group">
            <div class="photo-date-heading">${date}</div>
            <div class="photo-date-grid">
                ${items.map(p => `
                    <div class="photo-card" data-id="${p.id}">
                        <img src="${escapeHtml(p.photo_url)}" alt="" loading="lazy" />
                        <div class="photo-card-meta">
                            <span class="photo-card-comment">${escapeHtml(p.title !== 'Photo' ? p.title : '')}</span>
                            <button class="btn btn-ghost" data-action="delete-journal" data-id="${p.id}">Delete</button>
                        </div>
                    </div>
                `).join('')}
            </div>
        </div>
    `).join('');
}

async function uploadJournalPhoto(file) {
    if (!activeCar || !file) return;
    const fd = new FormData();
    fd.append('file', file);
    fd.append('comment', '');
    const btn = document.getElementById('photo-snap-btn');
    btn.disabled = true;
    btn.textContent = 'Uploading…';
    try {
        const res = await fetch(`/api/cars/${activeCar.id}/journal/photo`, {
            method: 'POST',
            headers: authHeaders(),
            body: fd,
        });
        if (res.status === 401) { toast('Unauthorized'); return; }
        if (!res.ok) throw new Error(await res.text());
        const entry = await res.json();
        journalEntries.unshift(entry);
        renderPhotoTab();
        toast('Photo saved');
    } catch (e) {
        toast('Upload failed');
        console.error(e);
    } finally {
        btn.disabled = false;
        btn.textContent = 'Add Photo';
    }
}

// ── Last doc answer (shared with manuals) ───────────────────────────────────

export let lastDocAnswer = null;

export function setLastDocAnswer(val) {
    lastDocAnswer = val;
}

export async function saveDocSearch() {
    if (!lastDocAnswer) return;
    const saveBtn = document.getElementById('doc-ask-save-btn');
    saveBtn.disabled = true;
    try {
        const sources = lastDocAnswer.sources.length ? ` [${lastDocAnswer.sources.join(', ')}]` : '';
        const entry = await api('POST', `/api/cars/${activeCar.id}/journal`, {
            type: 'docsearch',
            title: lastDocAnswer.question,
            body: lastDocAnswer.answer + (sources ? `\n\nSources: ${lastDocAnswer.sources.join(', ')}` : ''),
        });
        journalEntries.unshift(entry);
        renderJournalTab('docsearch');
        saveBtn.textContent = 'Saved';
        toast('Saved to Journal');
    } catch (e) {
        toast('Failed to save');
        saveBtn.disabled = false;
    }
}

// ── Event delegation ────────────────────────────────────────────────────────

export function initJournal() {
    // Photo upload
    const photoInput = document.getElementById('journal-photo-input');
    document.getElementById('photo-snap-btn').addEventListener('click', () => photoInput.click());
    photoInput.addEventListener('change', () => {
        if (photoInput.files.length) uploadJournalPhoto(photoInput.files[0]);
        photoInput.value = '';
    });

    // Research search
    document.getElementById('research-input').addEventListener('keydown', e => {
        if (e.key === 'Enter') runResearch();
    });
    document.getElementById('research-btn').addEventListener('click', runResearch);

    // Converse add
    document.getElementById('converse-input').addEventListener('keydown', e => {
        if (e.key === 'Enter') addJournalEntry('converse');
    });
    document.querySelector('#journal-converse .btn-primary').addEventListener('click', () => addJournalEntry('converse'));

    // Note add
    document.querySelector('#journal-note .btn-primary').addEventListener('click', () => addJournalEntry('note'));

    // Save doc search
    document.getElementById('doc-ask-save-btn').addEventListener('click', saveDocSearch);

    // Journal tab switching
    document.querySelectorAll('#section-journal .section-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.tab;
            showJournalTab(tab, btn);
        });
    });

    // Delegated clicks for toggle/delete in journal tables
    document.getElementById('section-journal').addEventListener('click', e => {
        const target = e.target.closest('[data-action]');
        if (!target) return;
        const action = target.dataset.action;
        const id = parseInt(target.dataset.id);
        if (action === 'toggle-answer') toggleResearchAnswer(id);
        if (action === 'delete-journal') deleteJournalEntry(id);
        if (action === 'load-more-journal') {
            journalOffset += JOURNAL_LIMIT;
            loadJournal(true);
        }
    });
}
