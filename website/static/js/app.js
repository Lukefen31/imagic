// imagic — Web App  (5-step workflow + Canvas photo editor)

// ═══════════════════════════════════════════════════════════════
// STATE
// ═══════════════════════════════════════════════════════════════
let sessionId   = null;
let uploadedFiles   = [];   // [{file_id, filename, size}, ...]
let analysisResults = [];   // [{file_id, overall_score, metrics, verdict}, ...]
let photoDecisions  = {};   // file_id → 'keep' | 'trash' | 'review'
let accountEmail = '';
let googleOauthEnabled = false;

// Step state
let currentStep = 0;
const stepDone  = [false, false, false, false, false];

const thumbnailQueue = [];
let activeThumbnailLoads = 0;
const MAX_CONCURRENT_THUMBNAILS = 4;
const APP_STATE_STORAGE_KEY = 'imagic-web-state-v2';
const ANALYSIS_FETCH_RETRIES = 2;

let persistStateTimer = 0;
let editorZoom = 100;
let exportResults = {};
let editorPreviewThumbTimer = 0;

// Editor state
let editorPhotos   = [];       // files to edit (kept / review only)
let editorIndex    = -1;
let edOriginalImg  = null;     // HTMLImageElement of current photo
let edOriginalData = null;     // ImageData of proxy (for fast preview)
let edUndoStack    = [];
let edRedoStack    = [];
let edShowBefore   = false;
let edCropMode     = false;
let edGradeActive  = 'natural';
let edRafId        = 0;        // requestAnimationFrame debounce
let edPhotoEdits   = {};       // file_id → {slider_key: value, ...}

// ═══════════════════════════════════════════════════════════════
// COLOR GRADE PRESETS (matching desktop _GRADE_LUT)
// ═══════════════════════════════════════════════════════════════
const COLOR_GRADES = {
    natural:       {},
    warm_sunset:   { temperature: 35, tint: 10, saturation: 15, vibrance: 20, highlights: -10 },
    cool_blue:     { temperature: -30, tint: -5, saturation: 10, contrast: 10 },
    moody_teal:    { temperature: -20, tint: -15, contrast: 20, saturation: -10, shadows: -15 },
    golden_hour:   { temperature: 40, exposure: 5, contrast: 8, vibrance: 25, highlights: -10 },
    film_noir:     { saturation: -100, contrast: 40, blacks: -20 },
    vintage_fade:  { contrast: -15, saturation: -25, blacks: 20, temperature: 10 },
    cinematic:     { contrast: 25, saturation: -15, temperature: -5, shadows: -20, highlights: -15 },
    pastel_dream:  { exposure: 10, contrast: -20, saturation: -30, temperature: 5, blacks: 25 },
    forest_green:  { temperature: -10, tint: -10, vibrance: 30, hsl_sat_green: 40, hsl_lum_green: 15 },
    desert_sand:   { temperature: 30, tint: 15, contrast: 10, vibrance: -10, saturation: -15 },
    neon_nights:   { contrast: 30, vibrance: 40, saturation: 20, highlights: -20, blacks: -15 },
    faded_film:    { contrast: -10, saturation: -20, blacks: 15, grain_amount: 20 },
    high_contrast: { contrast: 50, clarity: 30, blacks: -15, whites: 15 },
    soft_portrait: { contrast: -10, highlights: -15, shadows: 15, vibrance: 10, clarity: -20 },
};
const GRADE_NAMES = Object.keys(COLOR_GRADES);

// ═══════════════════════════════════════════════════════════════
// STEP NAVIGATION
// ═══════════════════════════════════════════════════════════════
function goToStep(idx) {
    if (idx < 0 || idx > 4) return;
    currentStep = idx;
    // Update step buttons
    document.querySelectorAll('.wf-step').forEach(btn => {
        const s = parseInt(btn.dataset.step);
        btn.classList.remove('active', 'done');
        const dot = btn.querySelector('.wf-step-dot');
        if (s === currentStep) {
            btn.classList.add('active');
            dot.textContent = '◉';
        } else if (stepDone[s]) {
            btn.classList.add('done');
            dot.textContent = '✓';
        } else {
            dot.textContent = '○';
        }
    });
    // Show page
    document.querySelectorAll('.wf-page').forEach(p => {
        p.classList.toggle('active', parseInt(p.dataset.page) === currentStep);
    });
    // Progress bar
    const pct = Math.round((stepDone.filter(Boolean).length / 5) * 100);
    document.getElementById('sidebar-progress').style.width = pct + '%';

    // Special actions on entering a step
    if (idx === 2) renderReviewGrid();
    if (idx === 3) buildFilmstrip();
    if (idx === 4) renderSavedExports();

    queuePersistAppState();
}

function completeStep(idx) {
    stepDone[idx] = true;
    if (idx < 4) goToStep(idx + 1);
    else goToStep(idx); // refresh export page
    updateSidebarStatus();
    queuePersistAppState();
}

function updateSidebarStatus() {
    const done = stepDone.filter(Boolean).length;
    const s = document.getElementById('sidebar-status');
    if (done === 0) s.textContent = 'Ready';
    else if (done < 5) s.textContent = `Step ${done}/5 complete`;
    else s.textContent = 'All steps complete!';
}

// ═══════════════════════════════════════════════════════════════
// TOAST
// ═══════════════════════════════════════════════════════════════
function toast(message, type = 'info') {
    const c = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = 'toast ' + type;
    el.textContent = message;
    c.appendChild(el);
    setTimeout(() => el.remove(), 5000);
}

function sleep(ms) {
    return new Promise(resolve => window.setTimeout(resolve, ms));
}

async function fetchJsonWithRetry(url, options = {}, retries = 0) {
    let lastError = null;

    for (let attempt = 0; attempt <= retries; attempt += 1) {
        try {
            const res = await fetch(url, options);
            const text = await res.text();
            let data = {};

            if (text.trim()) {
                try {
                    data = JSON.parse(text);
                } catch (error) {
                    lastError = error;
                    if (attempt < retries) {
                        await sleep(350 * (attempt + 1));
                        continue;
                    }
                    throw new Error('Response parsing failed. Please retry.');
                }
            } else if (attempt < retries) {
                await sleep(350 * (attempt + 1));
                continue;
            }

            return { res, data };
        } catch (error) {
            lastError = error;
            if (attempt < retries) {
                await sleep(350 * (attempt + 1));
                continue;
            }
        }
    }

    throw lastError || new Error('Request failed.');
}

function findAnalysisResult(fileId) {
    return analysisResults.find(result => result.file_id === fileId) || null;
}

function buildThumbShellMarkup(src, alt, shellClass = 'thumb-shell', imgClass = '') {
    return `<div class="${shellClass}" data-thumb-state="loading"><img class="${imgClass}" data-thumb-src="${src}" alt="${esc(alt)}" loading="lazy"></div>`;
}

function queuePersistAppState() {
    window.clearTimeout(persistStateTimer);
    persistStateTimer = window.setTimeout(persistAppState, 180);
}

function persistAppState() {
    if (!sessionId) {
        window.localStorage.removeItem(APP_STATE_STORAGE_KEY);
        return;
    }

    const state = {
        sessionId,
        uploadedFiles,
        analysisResults,
        photoDecisions,
        stepDone,
        currentStep,
        editorIndex,
        edPhotoEdits,
        exportResults,
        editorZoom,
        reviewFilter: document.getElementById('review-filter')?.value || 'all',
        exportFormat: document.getElementById('export-format')?.value || 'jpeg',
        savedAt: Date.now(),
    };

    try {
        window.localStorage.setItem(APP_STATE_STORAGE_KEY, JSON.stringify(state));
    } catch {
        // Ignore quota or storage errors.
    }
}

function clearPersistedAppState() {
    window.localStorage.removeItem(APP_STATE_STORAGE_KEY);
}

async function restorePersistedAppState() {
    try {
        const raw = window.localStorage.getItem(APP_STATE_STORAGE_KEY);
        if (!raw) return false;

        const saved = JSON.parse(raw);
        if (!saved?.sessionId || !Array.isArray(saved.uploadedFiles)) return false;

        const { res, data } = await fetchJsonWithRetry(`/api/session/${saved.sessionId}`, {}, 1);
        if (!res.ok || !Array.isArray(data.files) || !data.files.length) {
            clearPersistedAppState();
            return false;
        }

        sessionId = saved.sessionId;
        uploadedFiles = data.files;
        const availableFileIds = new Set(uploadedFiles.map(file => file.file_id));
        analysisResults = (saved.analysisResults || []).filter(item => availableFileIds.has(item.file_id));
        photoDecisions = saved.photoDecisions || {};
        exportResults = saved.exportResults || {};
        editorZoom = saved.editorZoom || 100;
        edPhotoEdits = saved.edPhotoEdits || {};

        for (let index = 0; index < stepDone.length; index += 1) {
            stepDone[index] = Array.isArray(saved.stepDone) ? !!saved.stepDone[index] : false;
        }

        if (document.getElementById('review-filter') && saved.reviewFilter) {
            document.getElementById('review-filter').value = saved.reviewFilter;
        }
        if (document.getElementById('export-format') && saved.exportFormat) {
            document.getElementById('export-format').value = saved.exportFormat;
        }

        if (uploadedFiles.length) {
            document.getElementById('import-status').textContent = `${uploadedFiles.length} photo(s) restored.`;
            renderAnalyseGrid();
            const nextStep = Number.isInteger(saved.currentStep) ? saved.currentStep : 1;
            goToStep(Math.max(0, Math.min(4, nextStep)));
            setEditorZoom(editorZoom);

            if (saved.currentStep >= 3) {
                buildFilmstrip();
                if (typeof saved.editorIndex === 'number' && saved.editorIndex >= 0) {
                    loadEditorPhotoByIndex(Math.min(saved.editorIndex, Math.max(editorPhotos.length - 1, 0)));
                }
            }
            if (saved.currentStep >= 4) {
                renderSavedExports();
            }
            updateSidebarStatus();
            return true;
        }
    } catch {
        clearPersistedAppState();
    }

    return false;
}

function renderSavedExports() {
    const grid = document.getElementById('export-grid');
    if (!grid) return;
    const entries = Object.entries(exportResults);
    if (!entries.length) return;

    grid.innerHTML = '';
    entries.forEach(([fileId, url]) => {
        const file = uploadedFiles.find(item => item.file_id === fileId);
        if (!file || !url) return;
        const card = document.createElement('div');
        card.className = 'wf-exp-card';
        card.innerHTML = `<img src="${url}" alt="${esc(file.filename)}"><div class="wf-card-info"><div class="wf-card-name">${esc(file.filename)}</div><div class="wf-card-score" style="color:#4caf50;">✓ Exported</div></div>`;
        card.onclick = () => {
            const anchor = document.createElement('a');
            anchor.href = url;
            anchor.download = file.filename.replace(/\.[^.]+$/, '.jpg');
            anchor.click();
        };
        grid.appendChild(card);
    });
}

// ═══════════════════════════════════════════════════════════════
// USAGE & STRIPE
// ═══════════════════════════════════════════════════════════════
async function refreshUsage() {
    try {
        const res = await fetch('/api/usage');
        const data = await res.json();
        const authenticated = !!data.is_authenticated;
        const freeRemaining = data.remaining_today ?? 0;
        const freeLimit = data.daily_limit ?? 20;
        const credits = data.credit_balance ?? 0;
        accountEmail = data.email || '';
        googleOauthEnabled = !!data.google_oauth_enabled;
        document.getElementById('btn-google').style.display = googleOauthEnabled ? '' : 'none';

        if (authenticated) {
            document.getElementById('usage-counter').innerHTML = `<strong>${freeRemaining}</strong> / ${freeLimit} free today · <strong>${credits}</strong> paid credits`;
            document.getElementById('pro-badge').style.display = '';
            document.getElementById('pro-badge').textContent = accountEmail || 'Signed in';
            document.getElementById('btn-register').style.display = 'none';
            document.getElementById('btn-login').style.display = 'none';
            document.getElementById('btn-google').style.display = 'none';
            document.getElementById('btn-upgrade').style.display = '';
            document.getElementById('btn-manage-sub').style.display = '';
            document.getElementById('btn-logout').style.display = '';
        } else {
            document.getElementById('usage-counter').innerHTML = `<strong>${freeRemaining}</strong> / ${freeLimit} free today`;
            document.getElementById('pro-badge').style.display = 'none';
            document.getElementById('btn-register').style.display = '';
            document.getElementById('btn-login').style.display = '';
            document.getElementById('btn-google').style.display = googleOauthEnabled ? '' : 'none';
            document.getElementById('btn-upgrade').style.display = 'none';
            document.getElementById('btn-manage-sub').style.display = 'none';
            document.getElementById('btn-logout').style.display = 'none';
        }
    } catch { /* ignore */ }
}

async function registerAccount() {
    const email = window.prompt('Email address');
    if (!email) return;
    const password = window.prompt('Create a password (minimum 8 characters)');
    if (!password) return;
    try {
        const res = await fetch('/api/auth/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password }),
        });
        const data = await res.json();
        if (!res.ok) { toast(data.detail || 'Could not create account.', 'error'); return; }
        toast('Account created. You can now buy credit packs and redeem keys.', 'success');
        refreshUsage();
    } catch {
        toast('Could not create account.', 'error');
    }
}

async function loginAccount() {
    const email = window.prompt('Email address');
    if (!email) return;
    const password = window.prompt('Password');
    if (!password) return;
    try {
        const res = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password }),
        });
        const data = await res.json();
        if (!res.ok) { toast(data.detail || 'Sign-in failed.', 'error'); return; }
        toast('Signed in.', 'success');
        refreshUsage();
    } catch {
        toast('Sign-in failed.', 'error');
    }
}

function loginWithGoogle() {
    window.location.href = '/api/auth/google/login';
}

async function logoutAccount() {
    try {
        await fetch('/api/auth/logout', { method: 'POST' });
        accountEmail = '';
        toast('Signed out.', 'info');
        refreshUsage();
    } catch {
        toast('Could not sign out.', 'error');
    }
}

async function redeemCreditKey() {
    if (!accountEmail) {
        toast('Sign in before redeeming a key.', 'error');
        return;
    }
    const licenseKey = window.prompt('Enter your web credit key');
    if (!licenseKey) return;
    try {
        const res = await fetch('/api/licenses/redeem', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ license_key: licenseKey }),
        });
        const data = await res.json();
        if (!res.ok) { toast(data.detail || 'Could not redeem key.', 'error'); return; }
        toast(`Credits added. New balance: ${data.credit_balance}`, 'success');
        refreshUsage();
    } catch {
        toast('Could not redeem key.', 'error');
    }
}

async function startCheckout() {
    if (!accountEmail) {
        toast('Create an account or sign in before buying credits.', 'error');
        return;
    }
    try {
        const res = await fetch('/api/stripe/checkout', { method: 'POST' });
        if (!res.ok) { const e = await res.json(); toast(e.detail || 'Checkout unavailable', 'error'); return; }
        const data = await res.json();
        if (data.checkout_url) window.location.href = data.checkout_url;
    } catch { toast('Could not start checkout.', 'error'); }
}

// ═══════════════════════════════════════════════════════════════
// STEP 0 — IMPORT
// ═══════════════════════════════════════════════════════════════
function initUpload() {
    const zone = document.getElementById('upload-zone');
    const input = document.getElementById('file-input');
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', e => {
        e.preventDefault(); zone.classList.remove('drag-over');
        if (e.dataTransfer.files.length) handleFiles(e.dataTransfer.files);
    });
    input.addEventListener('change', () => { if (input.files.length) handleFiles(input.files); });
}

async function handleFiles(files) {
    const form = new FormData();
    for (const f of files) form.append('files', f);
    const bar = document.getElementById('upload-progress-bar');
    const txt = document.getElementById('upload-progress-text');
    const pct = document.getElementById('upload-progress-pct');
    const prog = document.getElementById('upload-progress');
    prog.style.display = 'block'; bar.style.width = '0%'; txt.textContent = `Uploading ${files.length} photo(s)…`; pct.textContent = '0%';

    try {
        const res = await uploadFilesWithProgress(form, progress => {
            const clamped = Math.max(0, Math.min(100, progress));
            bar.style.width = `${clamped}%`;
            pct.textContent = `${clamped}%`;
            txt.textContent = clamped < 100
                ? `Uploading ${files.length} photo(s)…`
                : 'Processing upload…';
        });
        if (!res.ok) { const e = await res.json(); toast(e.detail || 'Upload failed', 'error'); prog.style.display = 'none'; return; }
        bar.style.width = '100%'; pct.textContent = '100%'; txt.textContent = 'Upload complete!';
        const data = await res.json();
        sessionId = data.session_id;
        uploadedFiles = data.files;
        analysisResults = [];
        exportResults = {};
        edPhotoEdits = {};
        editorIndex = -1;
        // Default all to 'review'
        uploadedFiles.forEach(f => { photoDecisions[f.file_id] = 'review'; });
        document.getElementById('import-status').textContent = `${uploadedFiles.length} photo(s) uploaded.`;
        refreshUsage();
        toast(`${uploadedFiles.length} photo(s) uploaded!`, 'success');
        setTimeout(() => { prog.style.display = 'none'; }, 1200);
        // Show thumbnails immediately in the analyse grid
        renderAnalyseGrid();
        // Auto-advance
        stepDone[0] = true;
        if (document.getElementById('auto-advance-import').checked) {
            goToStep(1);
        }
        updateSidebarStatus();
        queuePersistAppState();
    } catch (err) {
        toast('Upload failed: ' + err.message, 'error'); prog.style.display = 'none';
    }
}

function uploadFilesWithProgress(formData, onProgress) {
    return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/upload', true);
        xhr.responseType = 'text';

        xhr.upload.onprogress = event => {
            if (!event.lengthComputable) return;
            onProgress?.(Math.round((event.loaded / event.total) * 100));
        };

        xhr.onload = () => {
            const text = xhr.responseText || '';
            resolve({
                ok: xhr.status >= 200 && xhr.status < 300,
                status: xhr.status,
                async json() {
                    return text ? JSON.parse(text) : {};
                },
            });
        };

        xhr.onerror = () => reject(new Error('Network error during upload.'));
        xhr.send(formData);
    });
}

// ═══════════════════════════════════════════════════════════════
// STEP 1 — ANALYSE
// ═══════════════════════════════════════════════════════════════
async function runAnalysis() {
    if (!sessionId) return toast('Upload photos first', 'error');
    const bar = document.getElementById('analyse-progress-bar');
    const txt = document.getElementById('analyse-progress-text');
    const pct = document.getElementById('analyse-progress-pct');
    const prog = document.getElementById('analyse-progress');
    prog.style.display = 'block'; bar.style.width = '30%'; txt.textContent = 'Running AI analysis…'; pct.textContent = '30%';

    try {
        const { res, data } = await fetchJsonWithRetry(`/api/analyse/${sessionId}`, {}, ANALYSIS_FETCH_RETRIES);
        if (!res.ok) { toast(data.detail || 'Analysis failed', 'error'); prog.style.display = 'none'; return; }
        bar.style.width = '100%'; pct.textContent = '100%'; txt.textContent = 'Analysis complete!';
        analysisResults = data.results;
        // Set decisions from verdicts
        analysisResults.forEach(r => { photoDecisions[r.file_id] = r.verdict || 'review'; });
        renderAnalyseGrid();
        document.getElementById('analyse-status').textContent = `${analysisResults.length} photos analysed.`;
        toast('Analysis complete!', 'success');
        setTimeout(() => { prog.style.display = 'none'; }, 1200);
        queuePersistAppState();
        // Auto-advance
        if (document.getElementById('auto-advance-analyse').checked) {
            completeStep(1);
        }
    } catch (err) {
        toast('Analysis failed: ' + err.message, 'error'); prog.style.display = 'none';
    }
}

function renderAnalyseGrid() {
    const grid = document.getElementById('analyse-grid');
    grid.innerHTML = '';
    const list = analysisResults.length ? analysisResults : uploadedFiles.map(f => ({
        file_id: f.file_id, filename: f.filename, overall_score: 0, verdict: 'review'
    }));
    list.forEach(r => {
        const card = document.createElement('div');
        card.className = 'wf-lib-card';
        card.ondblclick = () => { goToStep(3); loadEditorPhoto(r.file_id); };
        const scoreP = Math.round((r.overall_score || 0) * 100);
        const cls = r.verdict === 'keep' ? 'keep' : r.verdict === 'trash' ? 'trash' : 'review';
        card.innerHTML = `
                            ${buildThumbShellMarkup(`/api/thumbnail/${sessionId}/${r.file_id}?kind=grid`, r.filename)}
            <div class="wf-card-info">
                <div class="wf-card-name">${esc(r.filename)}</div>
                <div class="wf-card-score">${scoreP}% — <span class="wf-rev-badge ${cls}">${(r.verdict||'review').toUpperCase()}</span></div>
            </div>`;
        grid.appendChild(card);
    });
    hydrateThumbnailImages(grid);
}

// ═══════════════════════════════════════════════════════════════
// STEP 2 — REVIEW & CULL
// ═══════════════════════════════════════════════════════════════
function renderReviewGrid() {
    const grid = document.getElementById('review-grid');
    grid.innerHTML = '';
    const filter = (document.getElementById('review-filter') || {}).value || 'all';
    const list = analysisResults.length ? analysisResults : uploadedFiles.map(f => ({
        file_id: f.file_id, filename: f.filename, overall_score: 0, verdict: 'review'
    }));
    list.forEach(r => {
        const d = photoDecisions[r.file_id] || 'review';
        if (filter !== 'all' && d !== filter) return;
        const card = document.createElement('div');
        card.className = 'wf-rev-card';
        card.dataset.fileId = r.file_id;
        card.ondblclick = () => { goToStep(3); loadEditorPhoto(r.file_id); };
        const scoreP = Math.round((r.overall_score || 0) * 100);
        card.innerHTML = `
            ${buildThumbShellMarkup(`/api/thumbnail/${sessionId}/${r.file_id}?kind=grid`, r.filename)}
            <div class="wf-card-info">
                <div class="wf-card-name">${esc(r.filename)}</div>
                <div class="wf-card-score">${scoreP}%</div>
                <span class="wf-rev-badge ${d}">${d.toUpperCase()}</span>
            </div>
            <div class="wf-rev-actions">
                <button class="wf-btn-keep ${d==='keep'?'active':''}" onclick="setDecision('${r.file_id}','keep',this)">✓ KEEP</button>
                <button class="wf-btn-trash ${d==='trash'?'active':''}" onclick="setDecision('${r.file_id}','trash',this)">✗ TRASH</button>
            </div>`;
        grid.appendChild(card);
    });
    hydrateThumbnailImages(grid);
    document.getElementById('review-status').textContent =
        `${Object.values(photoDecisions).filter(v=>v==='keep').length} keep · ` +
        `${Object.values(photoDecisions).filter(v=>v==='trash').length} trash · ` +
        `${Object.values(photoDecisions).filter(v=>v==='review').length} review`;
}

function setDecision(fileId, decision, btn) {
    photoDecisions[fileId] = decision;
    // Update UI on this card
    const card = btn.closest('.wf-rev-card');
    card.querySelectorAll('.wf-btn-keep, .wf-btn-trash').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const badge = card.querySelector('.wf-rev-badge');
    if (badge) { badge.className = 'wf-rev-badge ' + decision; badge.textContent = decision.toUpperCase(); }
    renderReviewGrid();
    queuePersistAppState();
}
function filterReview() { renderReviewGrid(); }

async function findDuplicates() {
    if (!sessionId) return toast('Upload photos first', 'error');
    try {
        const res = await fetch(`/api/duplicates/${sessionId}`);
        if (!res.ok) { const e = await res.json(); toast(e.detail || 'Failed', 'error'); return; }
        const data = await res.json();
        const c = document.getElementById('duplicate-results');
        c.style.display = 'block';
        if (!data.duplicate_groups.length) {
            c.innerHTML = '<div class="dup-group"><h3>✅ No duplicates found</h3></div>';
            toast('No duplicates found', 'info'); return;
        }

        // Track keep/trash state per group: dupGroupState[groupIdx] = Set of kept file_ids
        window.dupGroupState = [];

        c.innerHTML = data.duplicate_groups.map((g, i) => {
            // Find the best photo by quality score
            let bestId = g[0];
            let bestScore = -1;
            g.forEach(fid => {
                const ar = analysisResults.find(r => r.file_id === fid);
                const s = ar ? (ar.overall_score || 0) : 0;
                if (s > bestScore) { bestScore = s; bestId = fid; }
            });
            // Init state: best is kept, rest are trashed
            window.dupGroupState[i] = new Set([bestId]);

            return `<div class="dup-group" data-group="${i}">
                <div class="dup-group-header">
                    <h3>Group ${i+1} — ${g.length} similar photos</h3>
                    <button class="dup-group-auto-btn" onclick="dupAutoPick(${i})">⚡ Auto Pick Best</button>
                </div>
                <div class="dup-group-cards">${g.map(fid => {
                    const f = uploadedFiles.find(x => x.file_id === fid);
                    const ar = analysisResults.find(r => r.file_id === fid);
                    const score = ar ? Math.round((ar.overall_score || 0) * 100) : 0;
                    const isKept = fid === bestId;
                    const cls = isKept ? 'keep' : 'trash';
                    return `<div class="dup-card ${cls}" data-fid="${fid}" data-group="${i}" onclick="dupToggleCard(this)">
                        ${buildThumbShellMarkup(`/api/thumbnail/${sessionId}/${fid}?kind=grid`, f ? f.filename : fid, 'dup-thumb-shell', 'dup-card-thumb')}
                        <div class="dup-card-name">${esc(f?f.filename:fid)}</div>
                        <div class="dup-card-score">Score: ${score}%</div>
                        <div class="dup-card-badge">${isKept ? '✓ KEEP' : '✕ TRASH'}</div>
                    </div>`;
                }).join('')}</div>
            </div>`;
        }).join('');

        hydrateThumbnailImages(c);

        // Apply initial decisions
        _applyDupDecisions();
        toast(`${data.duplicate_groups.length} duplicate group(s) found`, 'info');
    } catch (err) { toast('Duplicate detection failed: ' + err.message, 'error'); }
}

function dupToggleCard(el) {
    const fid = el.dataset.fid;
    const gi = parseInt(el.dataset.group);
    const kept = window.dupGroupState[gi];
    if (kept.has(fid)) {
        kept.delete(fid);
    } else {
        kept.add(fid);
    }
    _refreshDupGroup(gi);
    _applyDupDecisions();
}

function dupAutoPick(gi) {
    const groupEl = document.querySelector(`.dup-group[data-group="${gi}"]`);
    const cards = groupEl.querySelectorAll('.dup-card');
    let bestId = null, bestScore = -1;
    cards.forEach(card => {
        const fid = card.dataset.fid;
        const ar = analysisResults.find(r => r.file_id === fid);
        const s = ar ? (ar.overall_score || 0) : 0;
        if (s > bestScore) { bestScore = s; bestId = fid; }
    });
    window.dupGroupState[gi] = new Set([bestId]);
    _refreshDupGroup(gi);
    _applyDupDecisions();
}

function _refreshDupGroup(gi) {
    const groupEl = document.querySelector(`.dup-group[data-group="${gi}"]`);
    const kept = window.dupGroupState[gi];
    groupEl.querySelectorAll('.dup-card').forEach(card => {
        const fid = card.dataset.fid;
        const isKept = kept.has(fid);
        card.className = 'dup-card ' + (isKept ? 'keep' : 'trash');
        card.querySelector('.dup-card-badge').textContent = isKept ? '✓ KEEP' : '✕ TRASH';
    });
}

function _applyDupDecisions() {
    // Sync duplicate keep/trash into the photoDecisions map
    if (!window.dupGroupState) return;
    window.dupGroupState.forEach(kept => {
        // All cards in each group
        document.querySelectorAll('.dup-card').forEach(card => {
            const fid = card.dataset.fid;
            const gi = parseInt(card.dataset.group);
            const groupKept = window.dupGroupState[gi];
            if (groupKept && !groupKept.has(fid)) {
                photoDecisions[fid] = 'trash';
            }
        });
    });
    queuePersistAppState();
}

// ═══════════════════════════════════════════════════════════════
// STEP 3 — EDITOR
// ═══════════════════════════════════════════════════════════════

// --- Filmstrip ---
function buildFilmstrip() {
    const strip = document.getElementById('ed-filmstrip');
    strip.innerHTML = '';
    // Only edit kept + review photos
    editorPhotos = uploadedFiles.filter(f => photoDecisions[f.file_id] !== 'trash');
    if (!editorPhotos.length) {
        editorPhotos = uploadedFiles.slice(); // fallback: edit all
    }
    editorPhotos.forEach((f, i) => {
        const shell = document.createElement('button');
        shell.type = 'button';
        shell.className = 'ed-film-thumb-shell';
        shell.dataset.fileId = f.file_id;
        shell.dataset.index = String(i);
        shell.innerHTML = `<img class="ed-film-thumb" data-file-id="${f.file_id}" data-thumb-src="/api/thumbnail/${sessionId}/${f.file_id}?kind=grid" alt="${esc(f.filename)}" loading="lazy">`;
        shell.onclick = () => loadEditorPhotoByIndex(i);
        strip.appendChild(shell);
    });
    hydrateThumbnailImages(strip);
    if (editorPhotos.length && editorIndex < 0) loadEditorPhotoByIndex(0);
}

function loadEditorPhotoByIndex(idx) {
    if (idx < 0 || idx >= editorPhotos.length) return;
    syncCurrentEditorPreviewToFilmstrip();
    editorIndex = idx;
    const f = editorPhotos[idx];
    // Highlight filmstrip
    document.querySelectorAll('.ed-film-thumb-shell').forEach((t, i) => t.classList.toggle('active', i === idx));
    // Update title
    document.getElementById('ed-title').textContent = `${f.filename}  (${idx+1} / ${editorPhotos.length})`;
    setEditorImageLoading(true);
    // Load image
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
        edOriginalImg = img;
        initCanvasFromImage(img);
        loadSlidersForPhoto(f.file_id);
        edUndoStack = [];
        edRedoStack = [];
        schedulePreview();
        document.getElementById('ed-no-photo').style.display = 'none';
        setEditorImageLoading(false);
    };
    img.onerror = () => {
        setEditorImageLoading(false);
        toast(`Could not load ${f.filename} in the editor.`, 'error');
    };
    img.src = `/api/editor-source/${sessionId}/${f.file_id}`;
    // Info
    const ar = findAnalysisResult(f.file_id);
    let info = `File: ${f.filename}`;
    if (ar) info += `\nScore: ${Math.round((ar.overall_score||0)*100)}%`;
    info += `\nSize: ${formatSize(f.size)}`;
    document.getElementById('ed-info').textContent = info;
    queuePersistAppState();
}

function loadEditorPhoto(fileId) {
    const idx = editorPhotos.findIndex(f => f.file_id === fileId);
    if (idx >= 0) loadEditorPhotoByIndex(idx);
    else {
        // Maybe not in filmstrip yet, rebuild
        buildFilmstrip();
        const idx2 = editorPhotos.findIndex(f => f.file_id === fileId);
        if (idx2 >= 0) loadEditorPhotoByIndex(idx2);
    }
}

function editorPrev() { if (editorIndex > 0) loadEditorPhotoByIndex(editorIndex - 1); }
function editorNext() { if (editorIndex < editorPhotos.length - 1) loadEditorPhotoByIndex(editorIndex + 1); }

// --- Canvas Preview Engine ---
function initCanvasFromImage(img) {
    const canvas = document.getElementById('ed-canvas');
    const maxDim = 1200;
    let w = img.naturalWidth, h = img.naturalHeight;
    if (Math.max(w, h) > maxDim) {
        const s = maxDim / Math.max(w, h);
        w = Math.round(w * s); h = Math.round(h * s);
    }
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0, w, h);
    edOriginalData = ctx.getImageData(0, 0, w, h);
}

function gatherParams() {
    const p = {};
    document.querySelectorAll('#ed-right-panel input[type="range"]').forEach(sl => {
        p[sl.dataset.key] = parseFloat(sl.value);
    });
    p._grade = edGradeActive;
    return p;
}

function schedulePreview() {
    cancelAnimationFrame(edRafId);
    edRafId = requestAnimationFrame(applyPreview);
    window.clearTimeout(editorPreviewThumbTimer);
    editorPreviewThumbTimer = window.setTimeout(() => {
        syncCurrentEditorPreviewToFilmstrip();
        queuePersistAppState();
    }, 180);
}

function clampUnit(value) {
    return Math.max(0, Math.min(1, value));
}

function sampleImageMidpoint(imageData) {
    const src = imageData.data;
    let total = 0;
    let samples = 0;
    for (let i = 0; i < src.length; i += 64) {
        total += (0.2126 * src[i] + 0.7152 * src[i + 1] + 0.0722 * src[i + 2]) / 255;
        samples += 1;
    }
    return samples ? total / samples : 0.5;
}

function applyPreview() {
    if (!edOriginalData) return;
    const canvas = document.getElementById('ed-canvas');
    const ctx = canvas.getContext('2d');
    const w = canvas.width, h = canvas.height;

    if (edShowBefore) {
        ctx.putImageData(edOriginalData, 0, 0);
        drawHistogram(edOriginalData);
        return;
    }

    const params = gatherParams();
    const src = edOriginalData.data;
    const out = ctx.createImageData(w, h);
    const dst = out.data;
    const contrastMid = sampleImageMidpoint(edOriginalData);

    // Apply grade preset first (add grade slider offsets to params)
    const grade = COLOR_GRADES[params._grade] || {};
    const intensity = (params.grade_intensity ?? 100) / 100;

    for (let i = 0; i < src.length; i += 4) {
        let r = src[i], g = src[i+1], b = src[i+2];

        // Normalize to 0-1
        let rf = r / 255, gf = g / 255, bf = b / 255;

        // --- Temperature ---
        const temp = (params.temperature || 0) + (grade.temperature || 0) * intensity;
        if (temp !== 0) {
            const t = temp / 150;
            rf += t * 0.15; bf -= t * 0.15;
        }
        // --- Tint ---
        const tint = (params.tint || 0) + (grade.tint || 0) * intensity;
        if (tint !== 0) { gf += tint / 150 * 0.15; }

        // --- Exposure ---
        const exp = (params.exposure || 0) + (grade.exposure || 0) * intensity;
        if (exp !== 0) {
            const m = Math.pow(2, exp / 50);
            rf *= m; gf *= m; bf *= m;
        }
        // --- Contrast ---
        const con = (params.contrast || 0) + (grade.contrast || 0) * intensity;
        if (con !== 0) {
            const factor = 1 + con / 100;
            rf = (rf - contrastMid) * factor + contrastMid;
            gf = (gf - contrastMid) * factor + contrastMid;
            bf = (bf - contrastMid) * factor + contrastMid;
        }
        // --- Highlights / Shadows ---
        const hl = (params.highlights || 0) + (grade.highlights || 0) * intensity;
        const sh = (params.shadows || 0) + (grade.shadows || 0) * intensity;
        if (hl !== 0 || sh !== 0) {
            const lum = 0.299 * rf + 0.587 * gf + 0.114 * bf;
            const hf = -hl / 200; const sf = sh / 200;
            const hMask = Math.max(0, (lum - 0.5) * 2); // 0 in shadows, 1 in highlights
            const sMask = Math.max(0, (0.5 - lum) * 2); // 1 in shadows, 0 in highlights
            rf += hf * hMask + sf * sMask;
            gf += hf * hMask + sf * sMask;
            bf += hf * hMask + sf * sMask;
        }
        // --- Whites / Blacks ---
        const wh = (params.whites || 0) + (grade.whites || 0) * intensity;
        const bl = (params.blacks || 0) + (grade.blacks || 0) * intensity;
        if (wh !== 0) {
            const lum = 0.299 * rf + 0.587 * gf + 0.114 * bf;
            const mask = Math.max(0, Math.min(1, (lum - 0.7) * 3.33));
            const delta = (wh / 200) * mask;
            rf += delta; gf += delta; bf += delta;
        }
        if (bl !== 0) {
            const lum = 0.299 * rf + 0.587 * gf + 0.114 * bf;
            const mask = Math.max(0, Math.min(1, 1 - lum * 3.33));
            const delta = (bl / 200) * mask;
            rf += delta; gf += delta; bf += delta;
        }

        // --- Vibrance ---
        const vib = (params.vibrance || 0) + (grade.vibrance || 0) * intensity;
        if (vib !== 0) {
            const mx = Math.max(rf, gf, bf), mn = Math.min(rf, gf, bf);
            const sat0 = mx > 0 ? 1 - mn / mx : 0;
            const boost = (1 - sat0) * (vib / 100);
            const avg = (rf + gf + bf) / 3;
            rf += (rf - avg) * boost; gf += (gf - avg) * boost; bf += (bf - avg) * boost;
        }
        // --- Saturation ---
        const sat = (params.saturation || 0) + (grade.saturation || 0) * intensity;
        if (sat !== 0) {
            const s2 = sat / 100;
            const gray = 0.299 * rf + 0.587 * gf + 0.114 * bf;
            rf = gray + (rf - gray) * (1 + s2);
            gf = gray + (gf - gray) * (1 + s2);
            bf = gray + (bf - gray) * (1 + s2);
        }
        // --- Dehaze ---
        const dh = (params.dehaze || 0) + (grade.dehaze || 0) * intensity;
        if (dh !== 0) {
            const d2 = dh / 100;
            const mn2 = Math.min(rf, gf, bf);
            rf += d2 * (rf - mn2) * 0.5;
            gf += d2 * (gf - mn2) * 0.5;
            bf += d2 * (bf - mn2) * 0.5;
        }

        rf = clampUnit(rf);
        gf = clampUnit(gf);
        bf = clampUnit(bf);

        // --- Split toning (COLOR GRADING section) ---
        const shHue = params.split_shadow_hue || 0;
        const shSat = params.split_shadow_sat || 0;
        const hlHue = params.split_highlight_hue || 0;
        const hlSat = params.split_highlight_sat || 0;
        const spBal = params.split_balance || 0;
        if (shSat > 0 || hlSat > 0) {
            const lum = 0.299 * rf + 0.587 * gf + 0.114 * bf;
            const balF = (spBal + 100) / 200;
            const sMask = Math.max(0, 1 - lum / (0.5 + balF * 0.5));
            const hMask = Math.max(0, (lum - (0.5 - (1 - balF) * 0.5)) / 0.5);
            if (shSat > 0) {
                const [sr, sg, sb] = hslToRgb(shHue / 360, 1, 0.5);
                const a = (shSat / 100) * sMask * 0.3;
                rf += (sr - rf) * a; gf += (sg - gf) * a; bf += (sb - bf) * a;
            }
            if (hlSat > 0) {
                const [hr, hg, hb] = hslToRgb(hlHue / 360, 1, 0.5);
                const a = (hlSat / 100) * hMask * 0.3;
                rf += (hr - rf) * a; gf += (hg - gf) * a; bf += (hb - bf) * a;
            }
        }

        // --- Vignette ---
        const vig = params.vignette_amount || 0;
        if (vig !== 0) {
            const cx = w / 2, cy = h / 2;
            const px = (i / 4) % w, py = Math.floor((i / 4) / w);
            const maxR = Math.sqrt(cx * cx + cy * cy);
            const mid = (params.vignette_midpoint || 50) / 100;
            const dist = Math.sqrt((px - cx) ** 2 + (py - cy) ** 2) / maxR;
            const v = Math.max(0, (dist - mid) / (1 - mid));
            const darken = 1 - (vig / 100) * v * v;
            rf *= darken; gf *= darken; bf *= darken;
        }

        rf = clampUnit(rf);
        gf = clampUnit(gf);
        bf = clampUnit(bf);

        // Clamp and write
        dst[i]   = Math.max(0, Math.min(255, Math.round(rf * 255)));
        dst[i+1] = Math.max(0, Math.min(255, Math.round(gf * 255)));
        dst[i+2] = Math.max(0, Math.min(255, Math.round(bf * 255)));
        dst[i+3] = 255;
    }

    // --- Grain (applied after) ---
    const grain = params.grain_amount || 0;
    if (grain > 0) {
        const strength = grain / 100 * 40;
        for (let i = 0; i < dst.length; i += 4) {
            const n = (Math.random() - 0.5) * strength;
            dst[i]   = Math.max(0, Math.min(255, dst[i] + n));
            dst[i+1] = Math.max(0, Math.min(255, dst[i+1] + n));
            dst[i+2] = Math.max(0, Math.min(255, dst[i+2] + n));
        }
    }

    ctx.putImageData(out, 0, 0);
    drawHistogram(out);

    // Save edits for this photo
    if (editorPhotos[editorIndex]) {
        edPhotoEdits[editorPhotos[editorIndex].file_id] = gatherParams();
    }
}

// --- HSL helper ---
function hslToRgb(h, s, l) {
    let r, g, b;
    if (s === 0) { r = g = b = l; }
    else {
        const hue2rgb = (p, q, t) => {
            if (t < 0) t += 1; if (t > 1) t -= 1;
            if (t < 1/6) return p + (q - p) * 6 * t;
            if (t < 1/2) return q;
            if (t < 2/3) return p + (q - p) * (2/3 - t) * 6;
            return p;
        };
        const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
        const p = 2 * l - q;
        r = hue2rgb(p, q, h + 1/3);
        g = hue2rgb(p, q, h);
        b = hue2rgb(p, q, h - 1/3);
    }
    return [r, g, b];
}

// --- Histogram ---
function drawHistogram(imageData) {
    const hCanvas = document.getElementById('ed-histogram');
    if (!hCanvas) return;
    const hCtx = hCanvas.getContext('2d');
    const bins = 256;
    const rH = new Uint32Array(bins), gH = new Uint32Array(bins), bH = new Uint32Array(bins);
    const d = imageData.data;
    for (let i = 0; i < d.length; i += 16) { // sample every 4th pixel for speed
        rH[d[i]]++; gH[d[i+1]]++; bH[d[i+2]]++;
    }
    const maxV = Math.max(...rH, ...gH, ...bH) || 1;
    hCtx.clearRect(0, 0, hCanvas.width, hCanvas.height);
    const w = hCanvas.width, h = hCanvas.height;
    const draw = (hist, color) => {
        hCtx.beginPath();
        hCtx.moveTo(0, h);
        for (let i = 0; i < bins; i++) {
            hCtx.lineTo(i * w / bins, h - (hist[i] / maxV) * h * 0.9);
        }
        hCtx.lineTo(w, h);
        hCtx.closePath();
        hCtx.fillStyle = color;
        hCtx.fill();
    };
    draw(rH, 'rgba(255,80,80,0.35)');
    draw(gH, 'rgba(80,255,80,0.35)');
    draw(bH, 'rgba(80,80,255,0.35)');
}

// --- Slider interaction ---
function onSliderInput(el) {
    const key = el.dataset.key;
    const val = el.value;
    const span = document.querySelector(`[data-val-for="${key}"]`);
    if (span) span.textContent = val;
    schedulePreview();
}

function toggleSection(headerBtn) {
    const section = headerBtn.closest('.ed-section');
    const body = section.querySelector('.ed-section-body');
    const chevron = headerBtn.querySelector('.ed-chevron');
    if (body.style.display === 'none') {
        body.style.display = '';
        section.classList.add('ed-section-open');
        chevron.textContent = '▾';
    } else {
        body.style.display = 'none';
        section.classList.remove('ed-section-open');
        chevron.textContent = '▸';
    }
}

function resetAllSliders() {
    document.querySelectorAll('#ed-right-panel input[type="range"]').forEach(sl => {
        const def = sl.dataset.key === 'grade_intensity' ? 100 :
                    sl.dataset.key === 'sharp_radius' ? 50 :
                    sl.dataset.key === 'vignette_midpoint' ? 50 : 0;
        sl.value = def;
        const span = document.querySelector(`[data-val-for="${sl.dataset.key}"]`);
        if (span) span.textContent = def;
    });
    edGradeActive = 'natural';
    document.querySelectorAll('.ed-grade-btn').forEach(b => b.classList.toggle('selected', b.dataset.grade === 'natural'));
    commitUndo();
    schedulePreview();
}

function loadSlidersForPhoto(fileId) {
    const saved = edPhotoEdits[fileId];
    if (saved) {
        document.querySelectorAll('#ed-right-panel input[type="range"]').forEach(sl => {
            const key = sl.dataset.key;
            if (key in saved) {
                sl.value = saved[key];
                const span = document.querySelector(`[data-val-for="${key}"]`);
                if (span) span.textContent = saved[key];
            }
        });
        edGradeActive = saved._grade || 'natural';
    } else {
        resetAllSliders();
    }
    document.querySelectorAll('.ed-grade-btn').forEach(b => b.classList.toggle('selected', b.dataset.grade === edGradeActive));
}

// --- Undo / Redo ---
function commitUndo() {
    edUndoStack.push(gatherParams());
    if (edUndoStack.length > 50) edUndoStack.shift();
    edRedoStack = [];
}

function editorUndo() {
    if (!edUndoStack.length) return;
    edRedoStack.push(gatherParams());
    const state = edUndoStack.pop();
    restoreParams(state);
    schedulePreview();
}
function editorRedo() {
    if (!edRedoStack.length) return;
    edUndoStack.push(gatherParams());
    const state = edRedoStack.pop();
    restoreParams(state);
    schedulePreview();
}
function restoreParams(p) {
    document.querySelectorAll('#ed-right-panel input[type="range"]').forEach(sl => {
        const k = sl.dataset.key;
        if (k in p) {
            sl.value = p[k];
            const span = document.querySelector(`[data-val-for="${k}"]`);
            if (span) span.textContent = p[k];
        }
    });
    if (p._grade) {
        edGradeActive = p._grade;
        document.querySelectorAll('.ed-grade-btn').forEach(b => b.classList.toggle('selected', b.dataset.grade === edGradeActive));
    }
}

// --- Before / After ---
function toggleBeforeAfter() {
    edShowBefore = !edShowBefore;
    document.getElementById('btn-before-after').classList.toggle('active', edShowBefore);
    // Add/remove label
    const wrap = document.getElementById('ed-canvas-wrap');
    wrap.querySelector('.ed-before-label')?.remove();
    if (edShowBefore) {
        const lbl = document.createElement('div');
        lbl.className = 'ed-before-label';
        lbl.textContent = 'BEFORE';
        wrap.appendChild(lbl);
    }
    schedulePreview();
}

// --- Crop mode (placeholder) ---
function toggleCropMode() {
    edCropMode = !edCropMode;
    document.getElementById('btn-crop-toggle').classList.toggle('active', edCropMode);
    toast(edCropMode ? 'Crop mode: drag on canvas to crop (coming soon)' : 'Crop mode off', 'info');
}

// --- Zoom ---
function setEditorZoom(val) {
    editorZoom = Number(val);
    document.getElementById('ed-zoom-label').textContent = editorZoom + '%';
    const canvas = document.getElementById('ed-canvas');
    canvas.style.transform = `scale(${editorZoom / 100})`;
    canvas.style.transformOrigin = 'center center';
    queuePersistAppState();
}

// --- Color Grades ---
function buildGradeGrid() {
    const grid = document.getElementById('ed-grade-grid');
    grid.innerHTML = '';
    GRADE_NAMES.forEach(name => {
        const btn = document.createElement('button');
        btn.className = 'ed-grade-btn' + (name === 'natural' ? ' selected' : '');
        btn.dataset.grade = name;
        btn.onclick = () => selectGrade(name);
        // Create a mini canvas to show colored thumbnail
        const c = document.createElement('canvas');
        c.width = 60; c.height = 40;
        btn.appendChild(c);
        const label = document.createElement('div');
        label.className = 'ed-grade-name';
        label.textContent = name.replace(/_/g, ' ');
        btn.appendChild(label);
        grid.appendChild(btn);
    });
}

function selectGrade(name) {
    edGradeActive = name;
    document.querySelectorAll('.ed-grade-btn').forEach(b => b.classList.toggle('selected', b.dataset.grade === name));
    commitUndo();
    schedulePreview();
    queuePersistAppState();
}

function updateGradeThumbnails() {
    if (!edOriginalData) return;
    // Build tiny proxy
    const tw = 60, th = 40;
    const tmpCanvas = document.createElement('canvas');
    tmpCanvas.width = tw; tmpCanvas.height = th;
    const tmpCtx = tmpCanvas.getContext('2d');
    tmpCtx.drawImage(edOriginalImg, 0, 0, tw, th);
    const tinyData = tmpCtx.getImageData(0, 0, tw, th);

    GRADE_NAMES.forEach(name => {
        const btn = document.querySelector(`.ed-grade-btn[data-grade="${name}"]`);
        if (!btn) return;
        const c = btn.querySelector('canvas');
        const ctx = c.getContext('2d');
        const grade = COLOR_GRADES[name];
        const out = ctx.createImageData(tw, th);
        const src = tinyData.data, dst = out.data;
        const contrastMid = sampleImageMidpoint(tinyData);
        for (let i = 0; i < src.length; i += 4) {
            let rf = src[i]/255, gf = src[i+1]/255, bf = src[i+2]/255;
            // Apply just the grade preset
            const temp = grade.temperature || 0;
            if (temp) { rf += temp/150*0.15; bf -= temp/150*0.15; }
            const t = grade.tint || 0;
            if (t) gf += t/150*0.15;
            const exp = grade.exposure || 0;
            if (exp) { const m = Math.pow(2, exp/50); rf*=m; gf*=m; bf*=m; }
            const con = grade.contrast || 0;
            if (con) { const f2 = 1 + con / 100; rf = (rf - contrastMid) * f2 + contrastMid; gf = (gf - contrastMid) * f2 + contrastMid; bf = (bf - contrastMid) * f2 + contrastMid; }
            const sat = grade.saturation || 0;
            if (sat) { const gr=.299*rf+.587*gf+.114*bf; rf=gr+(rf-gr)*(1+sat/100); gf=gr+(gf-gr)*(1+sat/100); bf=gr+(bf-gr)*(1+sat/100); }
            dst[i]   = Math.max(0, Math.min(255, clampUnit(rf)*255|0));
            dst[i+1] = Math.max(0, Math.min(255, clampUnit(gf)*255|0));
            dst[i+2] = Math.max(0, Math.min(255, clampUnit(bf)*255|0));
            dst[i+3] = 255;
        }
        ctx.putImageData(out, 0, 0);
    });
}

// --- AI Tools ---
function aiAutoEnhance() {
    setSliderValue('exposure', 5);
    setSliderValue('contrast', 8);
    setSliderValue('vibrance', 12);
    setSliderValue('clarity', 6);
    setSliderValue('highlights', -8);
    setSliderValue('shadows', 10);
    commitUndo(); schedulePreview();
    toast('AI Auto-Enhance applied', 'success');
}
function aiAutoWB() {
    setSliderValue('temperature', 0);
    setSliderValue('tint', 0);
    commitUndo(); schedulePreview();
    toast('AI Auto White Balance applied', 'success');
}
function aiDenoise() {
    setSliderValue('nr_luminance', 40);
    setSliderValue('nr_color', 30);
    commitUndo(); schedulePreview();
    toast('AI Denoise applied', 'success');
}
function aiSharpen() {
    setSliderValue('sharp_amount', 60);
    setSliderValue('sharp_radius', 80);
    commitUndo(); schedulePreview();
    toast('AI Smart Sharpen applied', 'success');
}
function aiBW() {
    setSliderValue('saturation', -100);
    setSliderValue('contrast', 10);
    commitUndo(); schedulePreview();
    toast('AI B&W Conversion applied', 'success');
}

function setSliderValue(key, val) {
    const sl = document.querySelector(`input[data-key="${key}"]`);
    if (sl) { sl.value = val; const s = document.querySelector(`[data-val-for="${key}"]`); if (s) s.textContent = val; }
}

// ═══════════════════════════════════════════════════════════════
// STEP 4 — EXPORT
// ═══════════════════════════════════════════════════════════════
async function exportAll() {
    const keptFiles = uploadedFiles.filter(f => photoDecisions[f.file_id] !== 'trash');
    if (!keptFiles.length) return toast('No photos to export', 'error');

    const prog = document.getElementById('export-progress');
    const bar = document.getElementById('export-progress-bar');
    const txt = document.getElementById('export-progress-text');
    const pct = document.getElementById('export-progress-pct');
    prog.style.display = 'block';
    const grid = document.getElementById('export-grid');
    grid.innerHTML = '';

    exportResults = {};

    let exportedCount = 0;
    for (let i = 0; i < keptFiles.length; i++) {
        const f = keptFiles[i];
        const p = Math.round(((i + 1) / keptFiles.length) * 100);
        bar.style.width = p + '%'; pct.textContent = p + '%';
        txt.textContent = `Exporting ${i + 1} / ${keptFiles.length}…`;

        try {
            const url = await exportPhotoForFile(f.file_id);
            exportResults[f.file_id] = url;
            const card = document.createElement('div');
            card.className = 'wf-exp-card';
            card.innerHTML = `<img src="${url}" alt="${esc(f.filename)}">
                <div class="wf-card-info"><div class="wf-card-name">${esc(f.filename)}</div>
                <div class="wf-card-score" style="color:#4caf50;">✓ Exported</div></div>`;
            card.onclick = () => { const a = document.createElement('a'); a.href = url; a.download = f.filename.replace(/\.[^.]+$/, '.jpg'); a.click(); };
            grid.appendChild(card);
            exportedCount += 1;
        } catch (error) {
            toast(error.message || `Failed to export ${f.filename}`, 'error');
        }
    }

    prog.style.display = 'none';
    document.getElementById('export-status').textContent = `${exportedCount} photo(s) exported. Click any image to download.`;
    toast(`Exported ${exportedCount} photo(s)!`, exportedCount ? 'success' : 'error');
    stepDone[4] = true;
    updateSidebarStatus();
    queuePersistAppState();
}

async function exportPhotoForFile(fileId) {
    const overrides = buildServerOverrides(fileId);
    const qualityData = findAnalysisResult(fileId);
    const { res, data } = await fetchJsonWithRetry(`/api/export/${sessionId}/${fileId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            grade: overrides.color_grade || 'natural',
            quality_data: qualityData,
            overrides,
        }),
    }, 1);

    if (!res.ok || !data.url) {
        throw new Error(data.error || data.detail || 'Export failed.');
    }
    return data.url;
}

async function downloadAll() {
    const keptFiles = uploadedFiles.filter(f => photoDecisions[f.file_id] !== 'trash');
    if (!keptFiles.length) return toast('No photos to download', 'error');
    if (!Object.keys(exportResults).length) {
        await exportAll();
    }
    toast('Starting downloads…', 'info');
    for (const f of keptFiles) {
        const url = exportResults[f.file_id] || await exportPhotoForFile(f.file_id);
        exportResults[f.file_id] = url;
        const a = document.createElement('a');
        a.href = url;
        a.download = f.filename.replace(/\.[^.]+$/, '.jpg');
        a.click();
        await new Promise(r => setTimeout(r, 300));
    }
    queuePersistAppState();
}

// ═══════════════════════════════════════════════════════════════
// KEYBOARD SHORTCUTS
// ═══════════════════════════════════════════════════════════════
document.addEventListener('keydown', e => {
    // Only in edit step
    if (currentStep !== 3) return;
    if (e.ctrlKey && e.key === 'z') { e.preventDefault(); editorUndo(); }
    else if (e.ctrlKey && e.key === 'y') { e.preventDefault(); editorRedo(); }
    else if (e.key === 'ArrowLeft') editorPrev();
    else if (e.key === 'ArrowRight') editorNext();
    else if (e.key === '\\') toggleBeforeAfter();
});

// Commit undo on slider mouseup
document.addEventListener('mouseup', () => {
    if (currentStep === 3 && edOriginalData) commitUndo();
});

// ═══════════════════════════════════════════════════════════════
// UTILITIES
// ═══════════════════════════════════════════════════════════════
function esc(str) {
    const d = document.createElement('div');
    d.textContent = str || '';
    return d.innerHTML;
}
function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
}

function hydrateThumbnailImages(root = document) {
    root.querySelectorAll('img[data-thumb-src]').forEach(img => {
        if (img.dataset.thumbHydrated === 'true') return;
        img.dataset.thumbHydrated = 'true';
        img.dataset.thumbAttempts = '0';
        queueThumbnailLoad(img);
    });
}

function queueThumbnailLoad(img) {
    thumbnailQueue.push(img);
    drainThumbnailQueue();
}

function drainThumbnailQueue() {
    while (activeThumbnailLoads < MAX_CONCURRENT_THUMBNAILS && thumbnailQueue.length) {
        const img = thumbnailQueue.shift();
        if (!img?.isConnected) continue;
        activeThumbnailLoads += 1;
        loadThumbnailImage(img)
            .finally(() => {
                activeThumbnailLoads -= 1;
                drainThumbnailQueue();
            });
    }
}

function loadThumbnailImage(img) {
    return new Promise(resolve => {
        const src = img.dataset.thumbSrc;
        const attempts = parseInt(img.dataset.thumbAttempts || '0', 10);
        setThumbnailShellState(img, 'loading');

        img.onload = () => {
            setThumbnailShellState(img, 'loaded');
            resolve();
        };
        img.onerror = () => {
            if (attempts < 2) {
                img.dataset.thumbAttempts = String(attempts + 1);
                window.setTimeout(() => queueThumbnailLoad(img), 400 * (attempts + 1));
            } else {
                setThumbnailShellState(img, 'failed');
                img.removeAttribute('src');
            }
            resolve();
        };
        img.src = src;
    });
}

function setThumbnailShellState(img, state) {
    const shell = img.closest('.thumb-shell, .dup-thumb-shell, .ed-film-thumb-shell');
    if (shell) {
        shell.dataset.thumbState = state;
    }
}

function setEditorImageLoading(isLoading) {
    document.getElementById('ed-canvas-wrap')?.setAttribute('data-editor-loading', isLoading ? 'true' : 'false');
}

function syncCurrentEditorPreviewToFilmstrip() {
    if (editorIndex < 0 || !editorPhotos[editorIndex]) return;
    const currentFileId = editorPhotos[editorIndex].file_id;
    const thumb = document.querySelector(`.ed-film-thumb[data-file-id="${currentFileId}"]`);
    const canvas = document.getElementById('ed-canvas');
    if (!thumb || !canvas || !canvas.width || !canvas.height) return;

    try {
        thumb.src = canvas.toDataURL('image/jpeg', 0.7);
        setThumbnailShellState(thumb, 'loaded');
    } catch {
        // Ignore canvas export failures.
    }
}

function buildServerOverrides(fileId) {
    const overrides = { ...(edPhotoEdits[fileId] || {}) };
    overrides.color_grade = overrides._grade || 'natural';
    overrides.color_grade_intensity = overrides.grade_intensity ?? 100;
    delete overrides._grade;
    delete overrides.grade_intensity;
    return overrides;
}

function initEditorInteractions() {
    const wrap = document.getElementById('ed-canvas-wrap');
    if (!wrap) return;

    wrap.addEventListener('wheel', event => {
        if (!event.ctrlKey || currentStep !== 3) return;
        event.preventDefault();
        const slider = document.getElementById('ed-zoom');
        const next = Math.max(50, Math.min(400, editorZoom + (event.deltaY < 0 ? 10 : -10)));
        slider.value = String(next);
        setEditorZoom(next);
    }, { passive: false });
}

// ═══════════════════════════════════════════════════════════════
// INIT
// ═══════════════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
    refreshUsage();
    initUpload();
    initEditorInteractions();
    buildGradeGrid();
    restorePersistedAppState().then(restored => {
        if (!restored) {
            goToStep(0);
        }
    });
    window.addEventListener('beforeunload', persistAppState);

    // Watch for image load in editor to update grade thumbnails
    const observer = new MutationObserver(() => {
        if (edOriginalImg && currentStep === 3) updateGradeThumbnails();
    });
    observer.observe(document.getElementById('ed-canvas'), { attributes: true });
});
