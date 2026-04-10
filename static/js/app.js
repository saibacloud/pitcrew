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

// ── API helper ──────────────────────────────────────────────────────────────

export async function api(method, path, body) {
    const opts = {
        method,
        headers: body ? { 'Content-Type': 'application/json' } : {},
        body: body ? JSON.stringify(body) : undefined,
    };
    const res = await fetch(path, opts);
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

export function renderMarkdown(str) {
    return escapeHtml(str)
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/^[\-\*] (.+)/gm, '<li>$1</li>')
        .replace(/\n{2,}/g, '</p><p>')
        .replace(/\n/g, '<br>')
        .replace(/(<li>.*?<\/li>)/gs, '<ul>$1</ul>');
}

// ── Boot ────────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    initDialogs();
    initGarage();
    initCar();
    initJournal();
    initPins();
    initCart();
    initManuals();
    loadGarage();
});
