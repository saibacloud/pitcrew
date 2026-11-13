// PitCrew — AI settings dialog
// The API key is write-only: the server only ever returns its last four characters.

import { api, errorMessage } from './app.js';
import { pitcrewConfirm } from './dialogs.js';

const PROVIDERS = { gemini: 'Google Gemini', openai: 'OpenAI' };
const UNREADABLE = "Stored key can't be decrypted. Enter it again.";

let current = null;

export function initSettings() {
    document.getElementById('settings-btn').addEventListener('click', openSettings);
    document.getElementById('settings-close').addEventListener('click', closeSettings);
    document.getElementById('modal-settings').addEventListener('click', e => {
        if (e.target === e.currentTarget) closeSettings();
    });
    document.getElementById('form-settings').addEventListener('submit', save);
    document.getElementById('settings-remove').addEventListener('click', remove);
}

export function closeSettings() {
    document.getElementById('modal-settings').classList.remove('open');
    document.getElementById('settings-key').value = '';
}

async function openSettings() {
    document.getElementById('settings-key').value = '';
    document.getElementById('settings-model').value = '';
    setError('');
    document.getElementById('modal-settings').classList.add('open');
    try {
        render(await api('GET', '/api/settings/ai'));
    } catch (e) {
        setError(errorMessage(e, 'Could not load settings'));
    }
    if (window.matchMedia('(hover: hover)').matches) document.getElementById('settings-key').focus();
}

function render(state) {
    current = state;
    const stored = state.state !== 'none';
    document.getElementById('settings-provider').textContent = stored ? PROVIDERS[state.provider] || state.provider : 'None';
    document.getElementById('settings-current-model').textContent = stored ? state.model : '—';
    document.getElementById('settings-key-state').textContent = stored
        ? `Ending ${state.key_last4}` + (state.state === 'env' ? ' (environment)' : '')
        : 'Not set';
    document.getElementById('settings-model').placeholder = stored ? state.model : '';
    // The env key is not stored here, so there is nothing to remove
    document.getElementById('settings-remove').hidden = !(state.state === 'ok' || state.state === 'unreadable');
    setError(state.state === 'unreadable' ? UNREADABLE : '');
}

function setError(msg) {
    const el = document.getElementById('settings-error');
    el.textContent = msg;
    el.hidden = !msg;
}

async function save(event) {
    event.preventDefault();
    const keyEl = document.getElementById('settings-key');
    const modelEl = document.getElementById('settings-model');
    const body = {};
    if (keyEl.value.trim()) body.api_key = keyEl.value.trim();
    if (modelEl.value.trim()) body.model = modelEl.value.trim();
    if (!body.api_key && !body.model) {
        setError('Enter an API key or a model');
        keyEl.focus();
        return;
    }
    const btn = document.getElementById('settings-save');
    btn.disabled = true;
    btn.textContent = 'Checking…';
    setError('');
    try {
        render(await api('PUT', '/api/settings/ai', body));
        keyEl.value = '';
        modelEl.value = '';
    } catch (e) {
        setError(errorMessage(e, 'Could not save settings'));
    } finally {
        btn.disabled = false;
        btn.textContent = 'Save';
    }
}

async function remove() {
    if (!current || !(await pitcrewConfirm('Remove the stored API key?', 'Remove'))) return;
    try {
        render(await api('DELETE', '/api/settings/ai'));
    } catch (e) {
        setError(errorMessage(e, 'Could not remove the key'));
    }
}
