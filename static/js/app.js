// PitCrew — main entry point
// Shared state, utilities, boot sequence

import { loadGarage, initGarage } from './garage.js';
import { initCar } from './car.js';
import { initJournal } from './journal.js';
import { initPins } from './pins.js';
import { initCart } from './cart.js';
import { initManuals } from './manuals.js';
import { initDialogs } from './dialogs.js';
import { initLogin, showLogin } from './login.js';

// ── Shared state ────────────────────────────────────────────────────────────

export let activeCar = null;

export function setActiveCar(car) {
    activeCar = car;
}

// ── API helpers ─────────────────────────────────────────────────────────────
// Auth rides on the HttpOnly session cookie, so requests just need to carry
// credentials — there is no token for the frontend to hold.

export async function api(method, path, body) {
    return request(path, {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
    });
}

export async function apiUpload(method, path, formData) {
    // No Content-Type — the browser sets the multipart boundary itself
    return request(path, { method, body: formData });
}

async function request(path, opts) {
    const res = await fetch(path, { credentials: 'same-origin', ...opts });
    if (res.status === 401) { showLogin(); throw new Error('Unauthorized'); }
    if (!res.ok) {
        let detail = '';
        try { detail = (await res.json()).detail; } catch { /* non-JSON error body */ }
        const err = new Error(`${res.status} ${detail}`);
        err.status = res.status;
        // FastAPI validation errors carry a list; only plain messages are user-facing
        err.detail = typeof detail === 'string' ? detail : '';
        throw err;
    }
    if (res.status === 204) return null;
    return res.json();
}

// ── Toast ───────────────────────────────────────────────────────────────────

export function toast(msg) {
    const el = document.getElementById('toast');
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(el._t);
    // Longer messages (e.g. why a document can't be read) stay up long enough to read
    el._t = setTimeout(() => el.classList.remove('show'), Math.max(2800, msg.length * 60));
}

// ── Utilities ───────────────────────────────────────────────────────────────

export function carSpec(car) {
    return [car.year, car.make, car.model, car.trim].filter(Boolean).join(' ');
}

export function escapeHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;');
}

export function aiLoadingHtml(text = 'Thinking') {
    return `<div class="ai-loading">${text}<div class="ai-loading-dots"><span></span><span></span><span></span></div></div>`;
}

// The server's reason when it gave one, otherwise the caller's fallback
export function errorMessage(error, fallback) {
    return (error && error.detail) || fallback;
}

// Google's Search Suggestions for a grounded answer, isolated in a sandboxed frame:
// no scripts, opaque origin, links open in a new tab
export function searchSuggestionsHtml(html) {
    if (!html) return '';
    const doc = '<base target="_blank">' + html;
    const attr = doc.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
    return `<iframe class="search-suggestions" title="Google Search suggestions" `
        + `sandbox="allow-popups allow-popups-to-escape-sandbox" srcdoc="${attr}"></iframe>`;
}

// Line-based subset of Markdown used in AI answers: paragraphs, # headings,
// - and 1. lists, **bold**, *italic*, and https links. Input is HTML-escaped first.
export function renderMarkdown(str) {
    const inline = text => escapeHtml(text)
        .replace(/\[([^\]]+)\]\((https:\/\/[^\s)"]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(\S(?:.*?\S)?)\*/g, '<em>$1</em>');

    const out = [];
    let para = [];
    let list = null;
    const flushPara = () => {
        if (para.length) out.push(`<p>${para.join('<br>')}</p>`);
        para = [];
    };
    const closeList = () => {
        if (list) out.push(`</${list}>`);
        list = null;
    };
    const listItem = (type, text, start) => {
        flushPara();
        if (list !== type) {
            closeList();
            // Numbered items split by blank lines keep counting from their own number
            out.push(type === 'ol' && start !== 1 ? `<ol start="${start}">` : `<${type}>`);
            list = type;
        }
        out.push(`<li>${inline(text)}</li>`);
    };

    for (const line of String(str ?? '').split('\n')) {
        let m;
        if ((m = line.match(/^\s*[-*] (.+)/))) listItem('ul', m[1]);
        else if ((m = line.match(/^\s*(\d+)[.)] (.+)/))) listItem('ol', m[2], parseInt(m[1], 10));
        else if ((m = line.match(/^#{1,6} (.+)/))) {
            flushPara();
            closeList();
            out.push(`<p><strong>${inline(m[1])}</strong></p>`);
        } else if (!line.trim()) {
            flushPara();
            closeList();
        } else {
            closeList();
            para.push(inline(line));
        }
    }
    flushPara();
    closeList();
    return out.join('');
}

// ── Service worker ──────────────────────────────────────────────────────────

if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/static/sw.js').catch(() => {});
}

// ── Session gate ────────────────────────────────────────────────────────────

function enterApp() {
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.getElementById('view-garage').classList.add('active');
    loadGarage();
}

async function lock() {
    try {
        await api('POST', '/api/auth/logout');
    } catch {
        // Clearing the cookie is best-effort — either way the UI locks
    }
    setActiveCar(null);
    showLogin();
}

// ── Boot ────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
    initDialogs();
    initGarage();
    initCar();
    initJournal();
    initPins();
    initCart();
    initManuals();
    initLogin(enterApp);
    document.getElementById('lock-btn').addEventListener('click', lock);

    // No view is active in the markup — the session probe picks the first one
    // so an authenticated reload never flashes the login screen
    try {
        const res = await fetch('/api/auth/me', { credentials: 'same-origin' });
        if (!res.ok) throw new Error('Unauthorized');
        enterApp();
    } catch {
        showLogin();
    }
});
