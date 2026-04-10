// PitCrew — photo pins (views, pin overlay, AI research)

import { activeCar, api, toast, escapeHtml, renderMarkdown, aiLoadingHtml, isAiBusy, AI_BUSY_MSG } from './app.js';
import { pitcrewPrompt, pitcrewConfirm } from './dialogs.js';

let carViews = {};      // { angle: [view, ...] }
let carPins = {};       // { viewId: [pin, ...] }
let activeViews = {};   // { angle: viewId | null }
let pinResearch = {};   // { pinId: { loading, summary } }

export async function loadViews() {
    if (!activeCar) return;
    try {
        const views = await api('GET', `/api/cars/${activeCar.id}/views`);
        carViews = {};
        carPins = {};
        activeViews = {};
        pinResearch = {};
        for (const v of views) {
            if (!carViews[v.angle]) carViews[v.angle] = [];
            carViews[v.angle].push(v);
        }
    } catch (e) {
        carViews = {};
        carPins = {};
        activeViews = {};
    }
    await Promise.all(
        ['front', 'sideD', 'sideP', 'rear', 'engine', 'underside', 'interior'].map(a => renderViewTab(a))
    );
}

async function renderViewTab(angle) {
    const grid = document.getElementById(`view-grid-${angle}`);
    if (!grid) return;
    grid.innerHTML = '';

    const views = carViews[angle] || [];
    const activeViewId = activeViews[angle] || null;

    // ── EXPANDED: single photo with pin overlay ─────────────────────────────
    if (activeViewId) {
        const view = views.find(v => v.id === activeViewId);
        if (view) {
            if (!carPins[view.id]) {
                try {
                    carPins[view.id] = await api('GET', `/api/views/${view.id}/pins`);
                } catch (e) {
                    carPins[view.id] = [];
                }
            }
            const pins = carPins[view.id];

            const pinsHtml = pins.map((pin, i) =>
                `<div class="pin-marker" style="left:${pin.x_pct}%;top:${pin.y_pct}%;" `
                + `title="${escapeHtml(pin.label)}" `
                + `data-action="delete-pin" data-pin-id="${pin.id}" data-view-id="${view.id}" data-angle="${angle}">`
                + `<span class="pin-num">${i + 1}</span></div>`
            ).join('');

            const pinListHtml = pins.length
                ? pins.map((pin, i) => {
                    const notesSpan = pin.notes
                        ? `<span class="pin-notes">${escapeHtml(pin.notes)}</span>`
                        : '';
                    const research = pinResearch[pin.id];
                    const isLoading = !!(research && research.loading);
                    const summary = isLoading ? null : ((research && research.summary) || pin.ai_summary || null);
                    const aiLabel = isLoading ? 'Researching...' : 'Ask AI';
                    const viewAnswerBtn = summary
                        ? `<button class="btn btn-ghost" data-action="toggle-pin-answer" data-pin-id="${pin.id}">View Answer</button>`
                        : '';
                    const answerDiv = summary
                        ? `<div class="pin-research-result" id="pin-answer-${pin.id}" style="display:none"><p>${renderMarkdown(summary)}</p></div>`
                        : '';
                    const loadingDiv = isLoading
                        ? `<div class="pin-research-result pin-research-loading">${aiLoadingHtml('Researching')}</div>`
                        : '';
                    return `<div class="pin-list-item">
                        <div class="pin-list-top">
                            <span class="pin-num">${i + 1}</span>
                            <span class="pin-label">${escapeHtml(pin.label)}</span>
                        </div>
                        <div class="pin-list-bottom">
                            ${notesSpan}
                            <div class="pin-list-actions">
                                <button class="btn btn-ghost" ${isLoading ? 'disabled ' : ''}
                                    data-action="research-pin" data-pin-id="${pin.id}" data-view-id="${view.id}" data-angle="${angle}">${aiLabel}</button>
                                ${viewAnswerBtn}
                                <button class="btn btn-ghost" data-action="delete-pin" data-pin-id="${pin.id}" data-view-id="${view.id}" data-angle="${angle}">Remove</button>
                            </div>
                        </div>
                    </div>${loadingDiv}${answerDiv}`;
                }).join('')
                : '<span class="parts-table-empty" style="padding:8px 0;font-size:12px;display:block;">No pins - click the photo to drop one</span>';

            const wrap = document.createElement('div');
            wrap.className = 'view-card-wrap';
            wrap.innerHTML = `
                <button class="btn btn-ghost view-back-btn" data-action="collapse-view" data-angle="${angle}">Back to photos</button>
                <div class="view-img-container">
                    <img src="${view.file_path}" alt="${angle}" />
                    <div class="pin-overlay" data-action="pin-drop" data-view-id="${view.id}" data-angle="${angle}">
                        ${pinsHtml}
                    </div>
                    <button class="car-photo-delete" style="z-index:20"
                            data-action="delete-view" data-angle="${angle}" data-view-id="${view.id}"
                            title="Remove photo">
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                            stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                            <line x1="4" y1="4" x2="20" y2="20" />
                            <line x1="20" y1="4" x2="4" y2="20" />
                        </svg>
                    </button>
                </div>
                <div class="pin-list" id="pin-list-${view.id}">${pinListHtml}</div>
            `;
            grid.appendChild(wrap);
            return;
        }
        activeViews[angle] = null;
    }

    // ── GRID: one card per photo ────────────────────────────────────────────
    views.forEach(view => {
        const pinCount = (carPins[view.id] || []).length;
        const date = new Date(view.created_at + 'Z').toLocaleDateString();
        const card = document.createElement('div');
        card.className = 'view-thumb-card';
        card.innerHTML = `
            <div class="manual-card-actions">
                <button data-action="delete-view" data-angle="${angle}" data-view-id="${view.id}">Remove</button>
            </div>
            <img class="view-thumb-img" src="${view.file_path}" alt="${angle}" />
            <div class="view-thumb-meta">
                <span>${date}</span>
                ${pinCount > 0 ? `<span class="view-pin-badge">${pinCount} pin${pinCount !== 1 ? 's' : ''}</span>` : ''}
            </div>
        `;
        card.addEventListener('click', e => {
            if (e.target.closest('[data-action]')) return;
            expandView(angle, view.id);
        });
        grid.appendChild(card);
    });

    // Add Photo card
    const addCard = document.createElement('div');
    addCard.className = 'add-car-card';
    addCard.innerHTML = `<div class="plus">+</div><div class="add-label">Add Photo</div>`;
    addCard.addEventListener('click', () => document.getElementById(`view-input-${angle}`).click());
    grid.appendChild(addCard);
}

async function expandView(angle, viewId) {
    if (!carPins[viewId]) {
        try {
            carPins[viewId] = await api('GET', `/api/views/${viewId}/pins`);
        } catch (e) {
            carPins[viewId] = [];
        }
    }
    activeViews[angle] = viewId;
    await renderViewTab(angle);
}

function collapseView(angle) {
    activeViews[angle] = null;
    renderViewTab(angle);
}

async function handleViewUpload(angle, event) {
    const files = Array.from(event.target.files);
    if (!files.length) return;
    for (const file of files) {
        const formData = new FormData();
        formData.append('file', file);
        try {
            const res = await fetch(`/api/cars/${activeCar.id}/views/${angle}`, { method: 'POST', body: formData });
            if (!res.ok) throw new Error(await res.text());
            const newView = await res.json();
            if (!carViews[angle]) carViews[angle] = [];
            carViews[angle].push(newView);
            toast('Photo saved');
        } catch (e) { toast('Photo upload failed'); }
    }
    event.target.value = '';
    await renderViewTab(angle);
}

async function deleteView(angle, id) {
    if (!await pitcrewConfirm('Remove this photo?')) return;
    try {
        await api('DELETE', `/api/views/${id}`);
        carViews[angle] = (carViews[angle] || []).filter(v => v.id !== id);
        delete carPins[id];
        if (activeViews[angle] === id) activeViews[angle] = null;
        await renderViewTab(angle);
        toast('Photo removed');
    } catch (e) { toast('Error removing photo'); }
}

async function handlePinDrop(event, viewId, angle) {
    const overlay = event.currentTarget;
    const rect = overlay.getBoundingClientRect();
    const x_pct = Math.round(((event.clientX - rect.left) / rect.width) * 1000) / 10;
    const y_pct = Math.round(((event.clientY - rect.top) / rect.height) * 1000) / 10;
    const result = await pitcrewPrompt({
        title: 'Add Pin',
        labelText: 'Component label',
        labelPlaceholder: 'e.g. Oil Filter, Coolant Reservoir',
        okLabel: 'Add Pin',
        showNotes: true,
    });
    if (!result) return;
    const { label, notes } = result;
    try {
        await api('POST', `/api/views/${viewId}/pins`, { label: label.trim(), notes, x_pct, y_pct });
        delete carPins[viewId];
        await renderViewTab(angle);
        toast('Pin added');
    } catch (e) { toast('Error adding pin'); }
}

async function deletePin(pinId, viewId, angle) {
    if (!await pitcrewConfirm('Remove this pin?')) return;
    try {
        await api('DELETE', `/api/pins/${pinId}`);
        delete carPins[viewId];
        await renderViewTab(angle);
        toast('Pin removed');
    } catch (e) { toast('Error removing pin'); }
}

async function researchPin(pinId, viewId, angle) {
    pinResearch[pinId] = { loading: true, summary: null };
    await renderViewTab(angle);
    try {
        const data = await api('POST', `/api/pins/${pinId}/research`);
        pinResearch[pinId] = { loading: false, summary: data.summary };
    } catch (e) {
        pinResearch[pinId] = { loading: false, summary: null };
        toast(isAiBusy(e) ? AI_BUSY_MSG : 'AI research failed');
    }
    await renderViewTab(angle);
}

function togglePinAnswer(pinId) {
    const el = document.getElementById('pin-answer-' + pinId);
    const btn = document.querySelector(`[data-action="toggle-pin-answer"][data-pin-id="${pinId}"]`);
    if (!el) return;
    const shown = el.style.display !== 'none';
    el.style.display = shown ? 'none' : 'block';
    if (btn) btn.textContent = shown ? 'View Answer' : 'Hide Answer';
}

// ── Event delegation ────────────────────────────────────────────────────────

export function initPins() {
    // Photo tab switching
    document.querySelectorAll('#section-pins .section-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.tab;
            document.querySelectorAll('#section-pins .section-tab-content').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('#section-pins .section-tab').forEach(b => b.classList.remove('active'));
            document.getElementById(`photo-${tab}`).classList.add('active');
            btn.classList.add('active');
        });
    });

    // View upload inputs
    ['front', 'sideD', 'sideP', 'rear', 'engine', 'underside', 'interior'].forEach(angle => {
        const input = document.getElementById(`view-input-${angle}`);
        if (input) input.addEventListener('change', e => handleViewUpload(angle, e));
    });

    // Delegated clicks inside pins section
    document.getElementById('section-pins').addEventListener('click', e => {
        const target = e.target.closest('[data-action]');
        if (!target) return;

        e.stopPropagation();
        const action = target.dataset.action;
        const angle = target.dataset.angle;
        const viewId = parseInt(target.dataset.viewId);
        const pinId = parseInt(target.dataset.pinId);

        if (action === 'collapse-view') collapseView(angle);
        if (action === 'delete-view') deleteView(angle, viewId);
        if (action === 'delete-pin') deletePin(pinId, viewId, angle);
        if (action === 'research-pin') researchPin(pinId, viewId, angle);
        if (action === 'toggle-pin-answer') togglePinAnswer(pinId);
        if (action === 'pin-drop') handlePinDrop(e, viewId, angle);
    });
}
