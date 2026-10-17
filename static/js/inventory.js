// PitCrew — garage-wide inventory (tools, consumables, fluids, parts)

import { api, toast, escapeHtml, escapeAttr, errorMessage } from './app.js';
import { pitcrewConfirm } from './dialogs.js';

const KINDS = [
    ['tool', 'Tools'],
    ['consumable', 'Consumables'],
    ['fluid', 'Fluids'],
    ['part', 'Parts'],
    ['other', 'Other'],
];

let items = [];
let editingId = null;

function kindOptions(selected) {
    return KINDS.map(([value, label]) =>
        `<option value="${value}"${value === selected ? ' selected' : ''}>${label}</option>`).join('');
}

export async function loadInventory() {
    try {
        items = (await api('GET', '/api/inventory')).items;
    } catch (e) {
        toast(errorMessage(e, 'Failed to load inventory'));
        items = [];
    }
    editingId = null;
    renderInventory();
}

function renderRow(item) {
    if (item.id === editingId) {
        return `<div class="inv-row inv-row--edit" data-id="${item.id}">
            <input type="text" class="inv-edit-name" maxlength="80" value="${escapeAttr(item.name)}" aria-label="Name" />
            <select class="inv-edit-kind" aria-label="Kind">${kindOptions(item.kind)}</select>
            <input type="text" class="inv-edit-detail" maxlength="60" value="${escapeAttr(item.detail || '')}" placeholder="Detail" aria-label="Detail" />
            <div class="inv-actions">
                <button class="btn btn-ghost" data-action="cancel">Cancel</button>
                <button class="btn btn-primary" data-action="save">Save</button>
            </div>
        </div>`;
    }
    const quote = item.quote ? ` title="${escapeAttr(item.quote)}"` : '';
    return `<div class="inv-row" data-id="${item.id}">
        <div class="inv-main">
            <span class="inv-name">${escapeHtml(item.name)}</span>
            ${item.detail ? `<span class="inv-detail">${escapeHtml(item.detail)}</span>` : ''}
        </div>
        <span class="fit-badge inv-basis inv-basis--${escapeAttr(item.basis)}"${quote}>${escapeHtml(item.basis)}</span>
        <div class="inv-actions">
            <button class="btn btn-ghost" data-action="edit">Edit</button>
            <button class="btn btn-ghost" data-action="delete">Delete</button>
        </div>
    </div>`;
}

function renderInventory() {
    const el = document.getElementById('inv-list');
    if (!items.length) {
        el.innerHTML = '<p class="parts-table-empty">No items yet</p>';
        return;
    }
    el.innerHTML = KINDS.map(([kind, label]) => {
        const group = items.filter(i => i.kind === kind);
        if (!group.length) return '';
        return `<div class="inv-group">
            <div class="photo-date-heading">${label}</div>
            ${group.map(renderRow).join('')}
        </div>`;
    }).join('');
    if (editingId != null) el.querySelector('.inv-edit-name')?.focus();
}

async function addItem(e) {
    e.preventDefault();
    const nameEl = document.getElementById('inv-name');
    const detailEl = document.getElementById('inv-detail');
    const name = nameEl.value.trim();
    if (!name) { nameEl.focus(); return; }
    try {
        const item = await api('POST', '/api/inventory', {
            name,
            kind: document.getElementById('inv-kind').value,
            detail: detailEl.value.trim() || null,
        });
        items.push(item);
        items.sort((a, b) => a.name.localeCompare(b.name));
        nameEl.value = '';
        detailEl.value = '';
        renderInventory();
        nameEl.focus();
    } catch (e) { toast(errorMessage(e, 'Failed to add item')); }
}

async function saveEdit(row) {
    const id = Number(row.dataset.id);
    const name = row.querySelector('.inv-edit-name').value.trim();
    if (!name) { row.querySelector('.inv-edit-name').focus(); return; }
    const item = items.find(i => i.id === id);
    const patch = {
        name,
        kind: row.querySelector('.inv-edit-kind').value,
        // An emptied detail is sent as '' so it clears; null would mean "unchanged"
        detail: row.querySelector('.inv-edit-detail').value.trim(),
    };
    if (patch.name === item.name && patch.kind === item.kind && patch.detail === (item.detail || '')) {
        editingId = null;
        renderInventory();
        return;
    }
    try {
        const updated = await api('PATCH', `/api/inventory/${id}`, patch);
        items[items.indexOf(item)] = updated;
        editingId = null;
        renderInventory();
    } catch (e) { toast(errorMessage(e, 'Failed to save item')); }
}

async function deleteItem(id) {
    const item = items.find(i => i.id === id);
    if (!await pitcrewConfirm(`Delete ${item.name}?`)) return;
    try {
        await api('DELETE', `/api/inventory/${id}`);
        items = items.filter(i => i.id !== id);
        renderInventory();
    } catch (e) { toast(errorMessage(e, 'Failed to delete item')); }
}

export function initInventory() {
    document.getElementById('inv-kind').innerHTML = kindOptions('tool');
    document.getElementById('inv-form').addEventListener('submit', addItem);

    const list = document.getElementById('inv-list');
    list.addEventListener('click', e => {
        const btn = e.target.closest('button[data-action]');
        if (!btn) return;
        const row = btn.closest('.inv-row');
        const id = Number(row.dataset.id);
        const action = btn.dataset.action;
        if (action === 'edit') { editingId = id; renderInventory(); }
        else if (action === 'cancel') { editingId = null; renderInventory(); }
        else if (action === 'save') saveEdit(row);
        else if (action === 'delete') deleteItem(id);
    });
    list.addEventListener('keydown', e => {
        const row = e.target.closest('.inv-row--edit');
        if (!row) return;
        if (e.key === 'Enter') { e.preventDefault(); saveEdit(row); }
        else if (e.key === 'Escape') { editingId = null; renderInventory(); }
    });
}
