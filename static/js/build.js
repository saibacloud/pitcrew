// PitCrew — build sheet (factory spec fields, saved one field at a time)

import { activeCar, api, toast, errorMessage } from './app.js';

let buildData = null;

export async function loadBuild() {
    const list = document.getElementById('build-sheet-list');
    if (!activeCar || !list) return;
    const carId = activeCar.id;
    buildData = null;
    list.replaceChildren();
    try {
        const data = await api('GET', `/api/cars/${carId}/build`);
        if (activeCar?.id === carId) renderBuild(data);
    } catch (e) {
        toast('Error loading build sheet');
    }
}

// Rows holding focus keep their inputs so in-progress edits survive a save
// elsewhere; `force` names a row that is rebuilt regardless.
function renderBuild(data, force) {
    buildData = data;
    const list = document.getElementById('build-sheet-list');
    const missing = new Set(data.missing_baseline || []);
    data.fields.forEach(f => {
        const old = [...list.children].find(r => r.dataset.field === f.field);
        if (!old) {
            list.append(buildRow(f, missing));
        } else if (f.field !== force && old.contains(document.activeElement)) {
            old.classList.toggle('build-row-missing', missing.has(f.field));
            old.querySelector('.build-meta').replaceWith(buildMeta(f));
        } else {
            old.replaceWith(buildRow(f, missing));
        }
    });
}

// Latest saved value; input closures can be older than the last render
function savedValue(field) {
    return buildData?.fields.find(f => f.field === field)?.value ?? null;
}

async function saveField(field, value) {
    if (!activeCar) return;
    const carId = activeCar.id;
    const path = `/api/cars/${carId}/build/${encodeURIComponent(field)}`;
    try {
        const data = value === null
            ? await api('DELETE', path)
            : await api('PUT', path, { value });
        if (activeCar?.id === carId) renderBuild(data, value === null ? field : undefined);
    } catch (e) {
        toast(errorMessage(e, 'Save failed'));
        if (buildData && activeCar?.id === carId) renderBuild(buildData, field);
    }
}

function el(tag, props = {}, children = []) {
    const node = Object.assign(document.createElement(tag), props);
    node.append(...children);
    return node;
}

function buildRow(f, missing) {
    const row = el('div', { className: 'build-row' });
    row.dataset.field = f.field;
    row.classList.toggle('build-row-missing', missing.has(f.field));

    const id = `build-${f.field}`;
    const label = f.kind === 'options'
        ? el('span', { className: 'build-label', id: `${id}-label`, textContent: f.label })
        : el('label', { className: 'build-label', htmlFor: id, textContent: f.label });

    row.append(label, buildInput(f, id), buildMeta(f));
    return row;
}

function buildMeta(f) {
    const meta = el('div', { className: 'build-meta' });
    if (f.value == null) return meta;
    if (f.source) {
        meta.append(el('span', {
            className: 'build-source',
            textContent: f.source,
            title: f.updated_at || '',
        }));
    }
    const clear = el('button', {
        type: 'button',
        className: 'build-clear',
        textContent: '×',
        title: 'Clear',
    });
    clear.setAttribute('aria-label', `Clear ${f.label}`);
    clear.addEventListener('click', () => saveField(f.field, null));
    meta.append(clear);
    return meta;
}

function buildInput(f, id) {
    if (f.kind === 'options') return buildOptionsInput(f, id);

    if (f.kind === 'bool') {
        const cb = el('input', { type: 'checkbox', id, checked: f.value === true });
        cb.indeterminate = f.value == null;
        cb.addEventListener('change', () => saveField(f.field, cb.checked));
        return el('div', { className: 'build-input' }, [cb]);
    }

    let input;
    if (f.kind === 'enum') {
        input = el('select', { id }, [
            el('option', { value: '', textContent: '' }),
            ...(f.options || []).map(o => el('option', { value: o, textContent: o })),
        ]);
    } else if (f.kind === 'month') {
        // placeholder only shows where the browser falls back to a text input
        input = el('input', { type: 'month', id, placeholder: 'YYYY-MM' });
    } else {
        input = el('input', { type: 'text', id });
    }
    input.value = f.value ?? '';
    input.addEventListener('change', () => {
        const v = input.value.trim();
        if (v === (savedValue(f.field) ?? '')) return;
        saveField(f.field, v || null);
    });
    return el('div', { className: 'build-input' }, [input]);
}

function buildOptionsInput(f, id) {
    const wrap = el('div', { className: 'build-input build-options' });
    wrap.setAttribute('role', 'group');
    wrap.setAttribute('aria-labelledby', `${id}-label`);
    const rows = el('div', { className: 'build-options-rows' });

    const save = () => {
        const value = [...rows.children]
            .map(r => ({
                code: r.querySelector('.build-option-code').value.trim(),
                description: r.querySelector('.build-option-desc').value.trim(),
            }))
            .filter(o => o.code);
        if (JSON.stringify(value) === JSON.stringify(savedValue(f.field) || [])) return;
        saveField(f.field, value.length ? value : null);
    };

    const addRow = (opt = { code: '', description: '' }) => {
        const code = el('input', {
            type: 'text', className: 'build-option-code',
            value: opt.code ?? '', placeholder: 'Code',
        });
        code.setAttribute('aria-label', 'Option code');
        const desc = el('input', {
            type: 'text', className: 'build-option-desc',
            value: opt.description ?? '', placeholder: 'Description',
        });
        desc.setAttribute('aria-label', 'Option description');
        const remove = el('button', {
            type: 'button', className: 'build-clear', textContent: '×', title: 'Remove',
        });
        remove.setAttribute('aria-label', 'Remove option');
        const row = el('div', { className: 'build-option-row' }, [code, desc, remove]);
        code.addEventListener('change', save);
        desc.addEventListener('change', save);
        remove.addEventListener('click', () => { row.remove(); save(); });
        rows.append(row);
        return code;
    };

    (Array.isArray(f.value) ? f.value : []).forEach(opt => addRow(opt));

    const add = el('button', { type: 'button', className: 'btn btn-ghost build-option-add', textContent: '+ Add' });
    add.addEventListener('click', () => addRow().focus());

    wrap.append(rows, add);
    return wrap;
}
