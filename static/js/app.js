// PitCrew — main entry point
// Shared state, utilities, boot sequence

import { loadGarage, initGarage } from './garage.js';
import { initCar } from './car.js';
import { initJournal } from './journal.js';
import { initPins } from './pins.js';
import { initCart } from './cart.js';
import { initManuals } from './manuals.js';
import { initDialogs } from './dialogs.js';

// ── Shared state ────────────────────────────────────────────────────────────

export let activeCar = null;

export function setActiveCar(car) {
    activeCar = car;
}

// ── Auth ────────────────────────────────────────────────────────────────────

export function getToken() { return localStorage.getItem('pitcrew_token') || ''; }
export function setToken(t) { localStorage.setItem('pitcrew_token', t); }

export function authHeaders(extra = {}) {
    const t = getToken();
    return t ? { Authorization: `Bearer ${t}`, ...extra } : { ...extra };
}

// ── API helper ──────────────────────────────────────────────────────────────

export async function api(method, path, body) {
    const headers = body
        ? authHeaders({ 'Content-Type': 'application/json' })
        : authHeaders();
    const opts = {
        method,
        headers,
        body: body ? JSON.stringify(body) : undefined,
    };
    const res = await fetch(path, opts);
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

// ── Login gate ──────────────────────────────────────────────────────────────

function showLogin() {
    if (document.getElementById('login-overlay')) return;
    const overlay = document.createElement('div');
    overlay.id = 'login-overlay';
    overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.85);display:flex;align-items:center;justify-content:center;z-index:9999';
    overlay.innerHTML = `
        <form id="login-form" style="background:var(--bg-card,#1e1e2e);padding:2rem;border-radius:8px;min-width:300px;text-align:center">
            <h2 style="margin:0 0 1rem">PitCrew</h2>
            <input id="login-token" type="password" placeholder="API Token" style="width:100%;padding:.5rem;margin-bottom:1rem;box-sizing:border-box">
            <button type="submit" style="width:100%;padding:.5rem">Unlock</button>
        </form>`;
    document.body.appendChild(overlay);
    const form = document.getElementById('login-form');
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const token = document.getElementById('login-token').value.trim();
        if (!token) return;
        setToken(token);
        try {
            await api('GET', '/api/cars');
            overlay.remove();
            loadGarage();
        } catch {
            toast('Invalid token');
            setToken('');
        }
    });
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
    // Check auth before loading data
    try {
        await api('GET', '/api/cars');
        loadGarage();
    } catch {
        showLogin();
    }
});
