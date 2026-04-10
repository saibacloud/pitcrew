// PitCrew — cart (parts list, add/edit/delete)

import { activeCar, api, toast, escapeHtml } from './app.js';
import { pitcrewConfirm } from './dialogs.js';

let cartParts = [];
let cartCategory = 'all';
let editingPartId = null;

export async function loadCartParts() {
    if (!activeCar) return;
    try {
        const data = await api('GET', `/api/cars/${activeCar.id}/parts`);
        cartParts = data.items;
    } catch (e) {
        toast('Error loading parts');
        cartParts = [];
    }
    renderCartParts();
}

function filterCartCategory(cat, btnEl) {
    cartCategory = cat;
    document.querySelectorAll('#section-cart .section-tab').forEach(b => b.classList.remove('active'));
    btnEl.classList.add('active');
    renderCartParts();
}

function renderCartParts() {
    const list = document.getElementById('cart-parts-list');
    if (!list) return;
    const visible = cartCategory === 'all'
        ? cartParts
        : cartParts.filter(p => p.category === cartCategory);

    const rows = visible.map(p => `
        <tr>
            <td>${escapeHtml(p.name)}</td>
            <td>${p.created_at ? new Date(p.created_at).toLocaleDateString() : '-'}</td>
            <td>${escapeHtml(p.part_number || '-')}</td>
            <td>${p.quantity || 1}</td>
            <td>${p.price != null ? '$' + Number(p.price).toFixed(2) : '-'}</td>
            <td>${p.url ? `<a href="${escapeHtml(p.url)}" target="_blank" rel="noopener">${escapeHtml(p.url)}</a>` : '-'}</td>
            <td>${escapeHtml(p.category || '-')}</td>
            <td>${escapeHtml(p.notes || '-')}</td>
            <td><select class="status-select status-${p.status || 'wishlist'}" data-action="update-status" data-id="${p.id}"><option value="wishlist"${(!p.status || p.status === 'wishlist') ? ' selected' : ''}>Wishlist</option><option value="ordered"${p.status === 'ordered' ? ' selected' : ''}>Ordered</option><option value="received"${p.status === 'received' ? ' selected' : ''}>Received</option><option value="installed"${p.status === 'installed' ? ' selected' : ''}>Installed</option></select></td>
            <td class="parts-table-actions">
                <button class="btn btn-ghost" data-action="edit-part" data-id="${p.id}">Edit</button>
                <button class="btn btn-ghost" data-action="delete-part" data-id="${p.id}">Delete</button>
            </td>
        </tr>`).join('');

    list.innerHTML = `
        <div class="table-scroll">
        <table class="parts-table">
            <thead>
                <tr>
                    <th>Name</th><th>Date</th><th>Part #</th><th>Qty</th>
                    <th>Price</th><th>Link</th><th>Category</th><th>Notes</th>
                    <th>Status</th><th></th>
                </tr>
            </thead>
            <tbody>${rows || '<tr><td colspan="10" class="parts-table-empty">No parts yet</td></tr>'}</tbody>
            <tfoot>
                <tr><td colspan="10">
                    <button class="btn btn-ghost" data-action="open-add-part">+ Add Part</button>
                </td></tr>
            </tfoot>
        </table>
        </div>`;
}

function openAddPartModal() {
    editingPartId = null;
    document.getElementById('form-add-part').reset();
    document.getElementById('part-quantity').value = '1';
    document.querySelector('#modal-add-part h3').textContent = 'Add Part';
    document.querySelector('#form-add-part .btn-primary').textContent = 'Add Part';
    document.getElementById('modal-add-part').classList.add('open');
    document.getElementById('part-name').focus();
}

function openEditPartModal(id) {
    const p = cartParts.find(p => p.id === id);
    if (!p) return;
    editingPartId = id;
    document.getElementById('form-add-part').reset();
    document.getElementById('part-name').value = p.name || '';
    document.getElementById('part-number-input').value = p.part_number || '';
    document.getElementById('part-category').value = p.category || 'Mechanical';
    document.getElementById('part-quantity').value = p.quantity || 1;
    document.getElementById('part-price').value = p.price != null ? p.price : '';
    document.getElementById('part-url').value = p.url || '';
    document.getElementById('part-notes').value = p.notes || '';
    document.querySelector('#modal-add-part h3').textContent = 'Edit Part';
    document.querySelector('#form-add-part .btn-primary').textContent = 'Save Changes';
    document.getElementById('modal-add-part').classList.add('open');
    document.getElementById('part-name').focus();
}

function closeAddPartModal() {
    document.getElementById('modal-add-part').classList.remove('open');
    editingPartId = null;
}

async function deleteCartPart(id) {
    if (!await pitcrewConfirm('Remove this part?')) return;
    try {
        await api('DELETE', `/api/parts/${id}`);
        cartParts = cartParts.filter(p => p.id !== id);
        renderCartParts();
        toast('Part removed');
    } catch (e) { toast('Error removing part'); }
}

async function updatePartStatus(id, status) {
    try {
        const updated = await api('PATCH', `/api/parts/${id}`, { status });
        const i = cartParts.findIndex(p => p.id === id);
        if (i >= 0) cartParts[i] = updated;
        toast('Status updated');
        renderCartParts();
    } catch (e) {
        toast('Error updating status');
        renderCartParts();
    }
}

async function submitAddPart(e) {
    e.preventDefault();
    const payload = {
        name: document.getElementById('part-name').value,
        part_number: document.getElementById('part-number-input').value || null,
        category: document.getElementById('part-category').value,
        quantity: parseInt(document.getElementById('part-quantity').value) || 1,
        price: parseFloat(document.getElementById('part-price').value) || null,
        url: document.getElementById('part-url').value || null,
        notes: document.getElementById('part-notes').value || null,
    };
    try {
        if (editingPartId) {
            const updated = await api('PATCH', `/api/parts/${editingPartId}`, payload);
            const i = cartParts.findIndex(p => p.id === editingPartId);
            if (i >= 0) cartParts[i] = updated;
            closeAddPartModal();
            renderCartParts();
            toast('Part updated');
        } else {
            await api('POST', `/api/cars/${activeCar.id}/parts`, payload);
            closeAddPartModal();
            await loadCartParts();
            toast('Part added');
        }
    } catch (e) { toast('Error saving part'); }
}

// ── Init ────────────────────────────────────────────────────────────────────

export function initCart() {
    document.getElementById('form-add-part').addEventListener('submit', submitAddPart);
    document.getElementById('modal-add-part').addEventListener('click', e => {
        if (e.target === e.currentTarget) closeAddPartModal();
    });
    // Cancel button inside modal
    document.querySelector('#modal-add-part .btn-ghost').addEventListener('click', closeAddPartModal);

    // Cart category tabs
    document.querySelectorAll('#section-cart .section-tab').forEach(btn => {
        btn.addEventListener('click', () => filterCartCategory(btn.dataset.tab, btn));
    });

    // Delegated clicks in cart parts list
    document.getElementById('cart-parts-list').addEventListener('click', e => {
        const target = e.target.closest('[data-action]');
        if (!target) return;
        const action = target.dataset.action;
        const id = parseInt(target.dataset.id);
        if (action === 'edit-part') openEditPartModal(id);
        if (action === 'delete-part') deleteCartPart(id);
        if (action === 'open-add-part') openAddPartModal();
    });

    // Status select change
    document.getElementById('cart-parts-list').addEventListener('change', e => {
        const target = e.target.closest('[data-action="update-status"]');
        if (!target) return;
        updatePartStatus(parseInt(target.dataset.id), target.value);
    });
}
