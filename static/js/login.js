// PitCrew — login view
// The PIN is exchanged once for an HttpOnly session cookie; the PIN itself is
// never stored client-side.

let onUnlocked = () => {};

export function initLogin(callback) {
    onUnlocked = callback;
    document.getElementById('login-form').addEventListener('submit', submitLogin);
    const input = document.getElementById('login-pin');
    input.addEventListener('input', () => {
        input.value = input.value.replace(/\D/g, '');
        setError('');
    });
    document.getElementById('pin-pad').addEventListener('click', e => {
        const key = e.target.closest('.pin-key');
        if (!key || key.type === 'submit') return;
        setError('');
        if (key.dataset.action === 'delete') input.value = input.value.slice(0, -1);
        else if (input.value.length < input.maxLength) input.value += key.dataset.digit;
    });
}

export function showLogin() {
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.getElementById('view-login').classList.add('active');
    setError('');
    const input = document.getElementById('login-pin');
    input.value = '';
    // Focus only on pointer devices — on mobile this would throw the keyboard
    // up before the user has even looked at the screen
    if (window.matchMedia('(hover: hover)').matches) input.focus();
}

function setError(msg) {
    const el = document.getElementById('login-error');
    el.textContent = msg;
    el.hidden = !msg;
}

async function submitLogin(event) {
    event.preventDefault();
    const input = document.getElementById('login-pin');
    const btn = document.getElementById('login-submit');
    const pin = input.value;
    if (!/^\d{6,12}$/.test(pin)) {
        setError('PIN is 6 to 12 digits');
        return;
    }

    setError('');
    btn.disabled = true;
    btn.textContent = 'Unlocking…';
    try {
        // Deliberately not routed through api() — a 401 here is an expected
        // answer, not a dropped session to bounce the user out of
        const res = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'same-origin',
            body: JSON.stringify({ pin }),
        });
        if (res.ok) {
            input.value = '';
            onUnlocked();
            return;
        }
        setError(res.status === 429
            ? 'Too many attempts — try again later'
            : 'Incorrect PIN');
        input.value = '';
        if (window.matchMedia('(hover: hover)').matches) input.focus();
    } catch {
        setError('Cannot reach PitCrew — check your connection');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Unlock';
    }
}
