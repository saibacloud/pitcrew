// PitCrew — chat threads with the mechanic, plus inventory changes they record

import { activeCar, api, toast, escapeHtml, escapeAttr, renderMarkdown, aiLoadingHtml, errorMessage, searchSuggestionsHtml } from './app.js';
import { pitcrewConfirm } from './dialogs.js';

let threads = [];
let threadId = null;
let messages = [];
let sending = false;
// Bumped on every car or thread switch so late responses for the old one are dropped
let generation = 0;

export function resetChat() {
    threads = [];
    threadId = null;
    messages = [];
    generation++;
    setSending(false);
    document.getElementById('chat-input').value = '';
    renderThreads();
    renderMessages();
}

export async function loadChat() {
    if (!activeCar) return;
    const gen = generation;
    try {
        const data = await api('GET', `/api/cars/${activeCar.id}/chats`);
        if (gen !== generation) return;
        threads = data.items;
    } catch (e) {
        toast(errorMessage(e, 'Failed to load chats'));
        return;
    }
    const keep = threads.some(t => t.id === threadId);
    renderThreads();
    if (!keep) await openThread(threads.length ? threads[0].id : null);
}

async function openThread(id) {
    const gen = ++generation;
    threadId = id;
    messages = [];
    setSending(false);
    renderThreads();
    renderMessages();
    if (id == null) return;
    try {
        const data = await api('GET', `/api/chats/${id}`);
        if (gen !== generation) return;
        messages = data.messages;
        renderMessages();
    } catch (e) { toast(errorMessage(e, 'Failed to load chat')); }
}

function threadLabel(t) {
    const date = new Date(t.updated_at + 'Z').toLocaleDateString();
    return `${t.title || 'New chat'} · ${date}`;
}

function renderThreads() {
    const select = document.getElementById('chat-thread-select');
    select.innerHTML = threads.length
        ? threads.map(t => `<option value="${t.id}"${t.id === threadId ? ' selected' : ''}>${escapeHtml(threadLabel(t))}</option>`).join('')
        : '<option value="">New chat</option>';
    select.disabled = sending || !threads.length;
    document.getElementById('chat-delete-btn').disabled = sending || threadId == null;
}

function chipHtml(change, msgIdx, changeIdx) {
    const undone = change.held !== (change.action === 'add');
    const sign = change.action === 'add' ? '+' : '−';
    const label = change.detail ? `${change.name} (${change.detail})` : change.name;
    const cls = ['inv-chip', change.basis === 'inferred' ? 'inv-chip--inferred' : '', undone ? 'inv-chip--undone' : '']
        .filter(Boolean).join(' ');
    const title = change.quote ? ` title="${escapeAttr(change.quote)}"` : '';
    return `<span class="${cls}"${title}>
        <span>${sign} ${escapeHtml(label)}</span>
        <button type="button" class="inv-chip-btn" data-msg="${msgIdx}" data-change="${changeIdx}"
            aria-label="${undone ? 'Redo' : 'Undo'} ${escapeAttr(label)}">${undone ? '↺' : '×'}</button>
    </span>`;
}

function messageHtml(m, idx) {
    if (m.role === 'user') {
        const chips = (m.inventory_changes || []).map((c, i) => chipHtml(c, idx, i)).join('');
        return `<div class="chat-msg chat-msg--user">
            <div class="chat-bubble">${escapeHtml(m.body)}</div>
            ${chips ? `<div class="inv-chips">${chips}</div>` : ''}
        </div>`;
    }
    return `<div class="chat-msg chat-msg--model">
        <div class="research-answer">${renderMarkdown(m.body)}${searchSuggestionsHtml(m.search_suggestions)}</div>
    </div>`;
}

function renderMessages(pendingText) {
    const el = document.getElementById('chat-messages');
    let html = messages.map(messageHtml).join('');
    if (pendingText != null) {
        html += `<div class="chat-msg chat-msg--user"><div class="chat-bubble">${escapeHtml(pendingText)}</div></div>
            <div class="chat-msg chat-msg--model">${aiLoadingHtml()}</div>`;
    }
    el.innerHTML = html;
    el.lastElementChild?.scrollIntoView({ block: 'nearest' });
}

function setSending(on) {
    sending = on;
    const input = document.getElementById('chat-input');
    const btn = document.getElementById('chat-send-btn');
    input.disabled = on;
    btn.disabled = on;
    document.getElementById('chat-new-btn').disabled = on;
    document.getElementById('chat-thread-select').disabled = on || !threads.length;
    document.getElementById('chat-delete-btn').disabled = on || threadId == null;
}

async function send(e) {
    e?.preventDefault();
    if (sending || !activeCar) return;
    const input = document.getElementById('chat-input');
    const text = input.value.trim();
    if (!text) { input.focus(); return; }

    setSending(true);
    renderMessages(text);
    let gen = generation;
    try {
        if (threadId == null) {
            const thread = await api('POST', `/api/cars/${activeCar.id}/chats`);
            if (gen !== generation) return;
            threads.unshift(thread);
            threadId = thread.id;
            gen = ++generation;
            renderThreads();
            setSending(true);
        }
        const data = await api('POST', `/api/chats/${threadId}/messages`, { text });
        if (gen !== generation) return;
        messages.push(data.user, data.model);
        threads = [data.thread, ...threads.filter(t => t.id !== data.thread.id)];
        input.value = '';
        setSending(false);
        renderThreads();
        renderMessages();
        input.focus();
    } catch (err) {
        if (gen !== generation) return;
        // Nothing was stored, so the typed text stays in the box for a resend
        setSending(false);
        renderMessages();
        toast(errorMessage(err, 'Message failed'));
        input.focus();
    }
}

async function newChat() {
    // The open chat is already empty, so there is nothing new to start
    if (threadId == null || !messages.length) {
        document.getElementById('chat-input').focus();
        return;
    }
    try {
        const thread = await api('POST', `/api/cars/${activeCar.id}/chats`);
        threads.unshift(thread);
        await openThread(thread.id);
        document.getElementById('chat-input').focus();
    } catch (e) { toast(errorMessage(e, 'Failed to create chat')); }
}

async function deleteChat() {
    if (threadId == null) return;
    if (!await pitcrewConfirm('Delete this chat?')) return;
    const id = threadId;
    try {
        await api('DELETE', `/api/chats/${id}`);
        threads = threads.filter(t => t.id !== id);
        await openThread(threads.length ? threads[0].id : null);
    } catch (e) { toast(errorMessage(e, 'Failed to delete chat')); }
}

// Undo flips the item's state: an add is undone by deleting it, a removal by restoring it
async function toggleChange(btn) {
    const msg = messages[Number(btn.dataset.msg)];
    const change = msg?.inventory_changes?.[Number(btn.dataset.change)];
    if (!change) return;
    btn.disabled = true;
    try {
        if (change.held) {
            await api('DELETE', `/api/inventory/${change.inventory_id}`);
            change.held = false;
        } else {
            await api('POST', `/api/inventory/${change.inventory_id}/restore`);
            change.held = true;
        }
        const chip = btn.closest('.inv-chip');
        chip.outerHTML = chipHtml(change, Number(btn.dataset.msg), Number(btn.dataset.change));
    } catch (e) {
        btn.disabled = false;
        toast(errorMessage(e, 'Inventory update failed'));
    }
}

export function initChat() {
    document.getElementById('chat-form').addEventListener('submit', send);
    document.getElementById('chat-input').addEventListener('keydown', e => {
        if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) send(e);
    });
    document.getElementById('chat-thread-select').addEventListener('change', e => {
        if (e.target.value) openThread(Number(e.target.value));
    });
    document.getElementById('chat-new-btn').addEventListener('click', newChat);
    document.getElementById('chat-delete-btn').addEventListener('click', deleteChat);
    document.getElementById('chat-messages').addEventListener('click', e => {
        const btn = e.target.closest('.inv-chip-btn');
        if (btn) toggleChange(btn);
    });
}
