// PitCrew — login view
// The access code is exchanged once for an HttpOnly session cookie; the code
// itself is never stored client-side.

let onUnlocked = () => {};

export function initLogin(callback) {
    onUnlocked = callback;
    document.getElementById('login-form').addEventListener('submit', submitLogin);
}

export function showLogin() {
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.getElementById('view-login').classList.add('active');
    setError('');
    const input = document.getElementById('login-code');
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
    const input = document.getElementById('login-code');
    const btn = document.getElementById('login-submit');
    const code = input.value;
    if (!code) return;

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
            body: JSON.stringify({ code }),
        });
        if (res.ok) {
            input.value = '';
            onUnlocked();
            return;
        }
        setError(res.status === 429
            ? 'Too many attempts — try again later'
            : 'Incorrect code');
        input.value = '';
        input.focus();
    } catch {
        setError('Cannot reach PitCrew — check your connection');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Unlock';
    }
}
