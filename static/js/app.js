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
    if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
    if (res.status === 204) return null;
    return res.json();
}

// ── Toast ───────────────────────────────────────────────────────────────────

export function toast(msg) {
    const el = document.getElementById('toast');
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove('show'), 2800);
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

export function isAiBusy(error) {
    return error && error.message && error.message.includes('502');
}

export const AI_BUSY_MSG = 'Google AI is busy right now, give it a moment and try again';

export function renderMarkdown(str) {
    return escapeHtml(str)
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/^[\-\*] (.+)/gm, '<li>$1</li>')
        .replace(/\n{2,}/g, '</p><p>')
        .replace(/\n/g, '<br>')
        .replace(/(<li>.*?<\/li>)/gs, '<ul>$1</ul>');
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
