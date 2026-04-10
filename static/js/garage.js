// PitCrew — garage view (car grid, add/remove cars)

import { api, toast, carSpec } from './app.js';
import { showCar } from './car.js';
import { pitcrewConfirm } from './dialogs.js';

export async function loadGarage() {
    let cars = [];
    try { cars = await api('GET', '/api/cars'); } catch (e) { console.error(e); }

    const grid = document.getElementById('car-grid');
    document.getElementById('car-count').textContent = cars.length ? `(${cars.length})` : '';
    grid.innerHTML = '';

    cars.forEach(car => {
        const card = document.createElement('div');
        card.className = 'car-card';
        card.innerHTML = `
            <div class="car-card-actions">
                <button data-action="remove">Remove</button>
            </div>
            <div class="car-nickname">${carSpec(car) || 'Unknown Car'}</div>
            <div class="car-spec">${car.options || ''}</div>
        `;
        card.querySelector('[data-action="remove"]').addEventListener('click', e => {
            e.stopPropagation();
            removeCar(car.id);
        });
        card.addEventListener('click', () => showCar(car));
        grid.appendChild(card);
    });

    const addCard = document.createElement('div');
    addCard.className = 'add-car-card';
    addCard.innerHTML = `<div class="plus">+</div><div class="add-label">Add a Car</div>`;
    addCard.addEventListener('click', openAddCarModal);
    grid.appendChild(addCard);
}

async function removeCar(id) {
    if (!await pitcrewConfirm('Remove this car from your garage?')) return;
    try {
        await api('DELETE', `/api/cars/${id}`);
        toast('Car removed');
        loadGarage();
    } catch (e) { toast('Error removing car'); }
}

function openAddCarModal() {
    document.getElementById('form-add-car').reset();
    document.getElementById('modal-add-car').classList.add('open');
    document.getElementById('add-options').focus();
}

function closeModal() {
    document.getElementById('modal-add-car').classList.remove('open');
}

async function submitAddCar(e) {
    e.preventDefault();
    const payload = {
        options: document.getElementById('add-options').value || null,
        year: parseInt(document.getElementById('add-year').value) || null,
        make: document.getElementById('add-make').value || null,
        model: document.getElementById('add-model').value || null,
        trim: document.getElementById('add-trim').value || null,
    };
    try {
        await api('POST', '/api/cars', payload);
        closeModal();
        toast('Car added to garage');
        loadGarage();
    } catch (e) { toast('Error adding car'); }
}

export function initGarage() {
    document.getElementById('form-add-car').addEventListener('submit', submitAddCar);
    document.getElementById('modal-add-car').addEventListener('click', e => {
        if (e.target === e.currentTarget) closeModal();
    });
}
