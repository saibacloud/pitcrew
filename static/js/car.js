// PitCrew — car view (info form, photo, section navigation)

import { activeCar, setActiveCar, api, apiUpload, toast, carSpec } from './app.js';
import { loadGarage } from './garage.js';
import { loadJournal, resetJournal } from './journal.js';
import { loadViews } from './pins.js';
import { loadCartParts } from './cart.js';
import { loadManuals } from './manuals.js';
import { pitcrewConfirm } from './dialogs.js';

export function showGarage() {
    document.getElementById('view-garage').classList.add('active');
    document.getElementById('view-car').classList.remove('active');
    setActiveCar(null);
    loadGarage();
}

const loaders = {
    journal: loadJournal,
    views: loadViews,
    cart: loadCartParts,
    manuals: loadManuals,
};

export function showCar(car) {
    setActiveCar(car);
    resetJournal();
    document.getElementById('view-garage').classList.remove('active');
    document.getElementById('view-car').classList.add('active');
    const spec = carSpec(car);
    document.getElementById('header-car-name').textContent = spec || 'Unknown Car';
    document.getElementById('header-car-spec').textContent = '';
    document.getElementById('sidebar-car-name').textContent = spec || 'Unknown Car';
    document.getElementById('sidebar-car-spec').textContent = car.options || '';
    showSection('section-info');
    populateCarInfoForm();
    loadCarPhoto();
}

export function showSection(sectionId, navEl) {
    document.querySelectorAll('.car-section').forEach(s => s.classList.remove('active'));
    document.querySelectorAll('.car-nav-item').forEach(n => n.classList.remove('active'));
    document.getElementById(sectionId).classList.add('active');
    if (navEl) {
        navEl.classList.add('active');
    } else {
        document.querySelectorAll('.car-nav-item').forEach(n => {
            if (n.dataset.section === sectionId) n.classList.add('active');
        });
    }
}

function populateCarInfoForm() {
    const car = activeCar;
    if (!car) return;
    ['options', 'year', 'make', 'model', 'trim', 'engine', 'color', 'vin', 'notes'].forEach(f => {
        const el = document.getElementById(`info-${f}`);
        if (el) el.value = car[f] ?? '';
    });
}

async function saveCarInfo(e) {
    e.preventDefault();
    const payload = {
        options: document.getElementById('info-options').value || null,
        year: parseInt(document.getElementById('info-year').value) || null,
        make: document.getElementById('info-make').value || null,
        model: document.getElementById('info-model').value || null,
        trim: document.getElementById('info-trim').value || null,
        engine: document.getElementById('info-engine').value || null,
        color: document.getElementById('info-color').value || null,
        vin: document.getElementById('info-vin').value || null,
        notes: document.getElementById('info-notes').value || null,
    };
    try {
        const updated = await api('PATCH', `/api/cars/${activeCar.id}`, payload);
        setActiveCar(updated);
        const spec = carSpec(updated);
        document.getElementById('header-car-name').textContent = spec || 'Unknown Car';
        document.getElementById('header-car-spec').textContent = '';
        document.getElementById('sidebar-car-name').textContent = spec || 'Unknown Car';
        document.getElementById('sidebar-car-spec').textContent = updated.options || '';
        toast('Saved');
    } catch (e) { toast('Save failed'); }
}

function loadCarPhoto() {
    const car = activeCar;
    if (!car || !car.photo_url) {
        document.getElementById('car-photo-img').style.display = 'none';
        document.getElementById('photo-placeholder').style.display = 'block';
        document.getElementById('photo-delete-btn').style.display = 'none';
        return;
    }
    const img = document.getElementById('car-photo-img');
    img.src = car.photo_url;
    img.style.display = 'block';
    document.getElementById('photo-placeholder').style.display = 'none';
    document.getElementById('photo-delete-btn').style.display = 'flex';
}

async function handlePhotoUpload(event) {
    const file = event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = e => {
        const img = document.getElementById('car-photo-img');
        img.src = e.target.result;
        img.style.display = 'block';
        document.getElementById('photo-placeholder').style.display = 'none';
        document.getElementById('photo-delete-btn').style.display = 'flex';
    };
    reader.readAsDataURL(file);
    const formData = new FormData();
    formData.append('file', file);
    try {
        const data = await apiUpload('POST', `/api/cars/${activeCar.id}/photo`, formData);
        activeCar.photo_url = data.photo_url;
        toast('Photo saved');
    } catch (e) { toast('Photo upload failed'); }
}

async function deleteCarPhoto() {
    if (!await pitcrewConfirm('Delete this photo?')) return;
    try {
        await api('DELETE', `/api/cars/${activeCar.id}/photo`);
        activeCar.photo_url = null;
        document.getElementById('car-photo-img').style.display = 'none';
        document.getElementById('photo-placeholder').style.display = 'block';
        document.getElementById('photo-delete-btn').style.display = 'none';
        document.getElementById('photo-input').value = '';
        toast('Photo deleted');
    } catch (e) { toast('Photo delete failed'); }
}

export function initCar() {
    document.getElementById('form-car-info').addEventListener('submit', saveCarInfo);
    document.getElementById('photo-input').addEventListener('change', handlePhotoUpload);
    document.getElementById('photo-delete-btn').addEventListener('click', e => {
        e.stopPropagation();
        deleteCarPhoto();
    });
    document.getElementById('photo-frame').addEventListener('click', () => {
        document.getElementById('photo-input').click();
    });

    // Back button
    document.querySelector('.back-btn').addEventListener('click', showGarage);

    // Sidebar nav items
    document.querySelectorAll('.car-nav-item[data-section]').forEach(navEl => {
        navEl.addEventListener('click', () => {
            showSection(navEl.dataset.section, navEl);
            const loadKey = navEl.dataset.load;
            if (loadKey && loaders[loadKey]) loaders[loadKey]();
        });
    });
}
