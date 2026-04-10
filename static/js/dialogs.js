// PitCrew — custom prompt & confirm dialogs

let _pendingPrompt = null;
let _pendingConfirm = null;

export function pitcrewPrompt({
    title = 'Add Pin',
    labelText = 'Label',
    labelPlaceholder = '',
    okLabel = 'Add Pin',
    showNotes = true,
} = {}) {
    return new Promise(resolve => {
        _pendingPrompt = resolve;
        document.getElementById('prompt-title').textContent = title;
        document.getElementById('prompt-label-label').textContent = labelText;
        document.getElementById('prompt-label-input').placeholder = labelPlaceholder;
        document.getElementById('prompt-label-input').value = '';
        document.getElementById('prompt-notes-input').value = '';
        document.getElementById('prompt-notes-wrap').style.display = showNotes ? '' : 'none';
        document.getElementById('prompt-ok-btn').textContent = okLabel;
        document.getElementById('prompt-overlay').classList.add('open');
        setTimeout(() => document.getElementById('prompt-label-input').focus(), 60);
    });
}

export function pitcrewConfirm(message, okLabel = 'Delete') {
    return new Promise(resolve => {
        _pendingConfirm = resolve;
        document.getElementById('confirm-message').textContent = message;
        document.getElementById('confirm-ok-btn').textContent = okLabel;
        document.getElementById('confirm-overlay').classList.add('open');
    });
}

function _submitPrompt() {
    const labelEl = document.getElementById('prompt-label-input');
    const label = labelEl.value.trim();
    if (!label) { labelEl.focus(); return; }
    const notes = document.getElementById('prompt-notes-input').value.trim() || null;
    _resolvePrompt({ label, notes });
}

function _resolvePrompt(result) {
    document.getElementById('prompt-overlay').classList.remove('open');
    if (_pendingPrompt) {
        const fn = _pendingPrompt;
        _pendingPrompt = null;
        fn(result);
    }
}

function _resolveConfirm(result) {
    document.getElementById('confirm-overlay').classList.remove('open');
    if (_pendingConfirm) {
        const fn = _pendingConfirm;
        _pendingConfirm = null;
        fn(result);
    }
}

export function initDialogs() {
    // Prompt dialog buttons
    document.getElementById('prompt-ok-btn').addEventListener('click', _submitPrompt);
    document.getElementById('prompt-overlay').addEventListener('click', e => {
        if (e.target === e.currentTarget) _resolvePrompt(null);
    });
    // Wire up cancel buttons inside prompt
    document.querySelector('#prompt-overlay .btn-ghost').addEventListener('click', () => _resolvePrompt(null));

    // Enter key in prompt fields
    document.getElementById('prompt-label-input').addEventListener('keydown', e => {
        if (e.key === 'Enter') document.getElementById('prompt-notes-input').focus();
    });
    document.getElementById('prompt-notes-input').addEventListener('keydown', e => {
        if (e.key === 'Enter') _submitPrompt();
    });

    // Confirm dialog buttons
    document.getElementById('confirm-ok-btn').addEventListener('click', () => _resolveConfirm(true));
    document.getElementById('confirm-overlay').addEventListener('click', e => {
        if (e.target === e.currentTarget) _resolveConfirm(false);
    });
    document.querySelector('#confirm-overlay .btn-ghost').addEventListener('click', () => _resolveConfirm(false));

    // Escape key closes dialogs and PDF viewer
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') {
            if (document.getElementById('pdf-viewer-overlay').style.display !== 'none') {
                closePdfViewer();
            } else {
                _resolvePrompt(null);
                _resolveConfirm(false);
                // Close add-car and add-part modals
                document.getElementById('modal-add-car').classList.remove('open');
                document.getElementById('modal-add-part').classList.remove('open');
            }
        }
    });
}

export function closePdfViewer() {
    document.getElementById('pdf-viewer-overlay').style.display = 'none';
    document.getElementById('pdf-frame').src = '';
    document.body.style.overflow = '';
}
