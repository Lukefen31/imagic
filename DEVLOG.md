# Imagic — Dev Log

## 2026-03-23 — v0.2.0: Performance, AI pipeline, and UX polish

**Branch:** `main`

---

### What was done this session

#### 1. AI Analysis Pipeline
- Integrated **pyiqa PerceptualScorer** (MUSIQ model) for image quality scoring.
- Integrated **Florence-2 ImageDescriptionAnalyzer** for AI-generated captions.
- Blended scoring: 70% perceptual + 30% penalty-based quality score.
- Added `perceptual_score` and `ai_caption` columns to the database with automatic migration.
- Fixed model reload-per-photo bug — models now load once and are reused.
- Added `AILoadingModal` overlay shown during AI analysis.
- Added `rank_burst_group()` for burst-group ranking.

#### 2. Auto-Trash Rules
- Photos with **"severe blur"** or **"significant blur"** penalties are now hard-rejected (auto-trashed).
- Photos with **"extremely dark"** or **"very dark"** penalties are now hard-rejected.
- Duplicate sort now uses quality score instead of timestamp.

#### 3. Edit Page Performance Optimizations
- **Edit proxy**: preview rendering now uses a 1800px proxy (`_raw_rgb_preview`) instead of the full 2500×1650 buffer — significant speedup for slider adjustments.
- **Shared luminance**: single luminance computation reused across highlights, shadows, whites, blacks, and clarity — eliminates redundant work.
- **OpenCV blurs**: replaced all `scipy.ndimage.gaussian_filter` / `uniform_filter` calls with `cv2.GaussianBlur` / `cv2.blur` — ~3–5× faster.
- **Faster demosaic**: switched preview decoding from AHD to DHT — faster with negligible quality difference at preview size.
- **Zero-copy QImage**: eliminated `.tobytes()` copy; `QImage` now references the NumPy buffer directly via `qimg._numpy_ref`.
- Navigation cache increased from 3→5 entries with adjacent-photo prefetching.
- Half-size raw decode for navigation previews.

#### 4. Culling Gallery UX
- Shortcut hint bar now shows: **K** Keep · **L** Trash · **H** Review · **← →** Navigate · **Esc** Close.
- Added **"⏳ Decoding RAW…"** loading overlay displayed during raw decode, hidden on completion or cache hit.

#### 5. Settings Dialog Fix
- Replaced broken monkey-patch approach with a proper `settings_requested = pyqtSignal()` on `MainWindow`.
- Menu action emits signal; `main.py` connects to handler. Settings dialog now opens reliably.

#### 6. Export Counter & Refresh
- Status bar now shows exported count alongside keep/trash/review (e.g., "Keep: 12 (5 exported)").
- Library automatically refreshes after export completes.
- Export progress overlay with 300ms polling timer.

#### 7. Version Bump & Windows Build
- Version bumped from 0.1.0 → **0.2.0** across `pyproject.toml`, `__init__.py`, `imagic.spec`, and `imagic-installer.iss`.
- Windows installer rebuilt with PyInstaller 6.19.0 + Inno Setup 6.

---

### Files changed (24 files)

| File | Change |
|------|--------|
| `pyproject.toml` | Version → 0.2.0 |
| `src/imagic/__init__.py` | Version → 0.2.0 |
| `src/imagic/ai/base_analyzer.py` | Base analyzer updates for pipeline |
| `src/imagic/ai/image_describer.py` | **NEW** — Florence-2 image description analyzer |
| `src/imagic/ai/perceptual_scorer.py` | **NEW** — pyiqa MUSIQ perceptual scorer |
| `src/imagic/ai/duplicate_detector.py` | Quality-based duplicate sort |
| `src/imagic/ai/quality_scorer.py` | Rewritten quality scorer with burst ranking |
| `src/imagic/controllers/ai_controller.py` | 70/30 blended scoring, hard-reject rules for blur & dark |
| `src/imagic/controllers/app_controller.py` | AI loading modal integration |
| `src/imagic/controllers/processing_controller.py` | Thread-safe export progress tracking |
| `src/imagic/main.py` | Settings signal handler, export poll timer, library refresh |
| `src/imagic/models/database.py` | Added `perceptual_score`, `ai_caption` columns + migration |
| `src/imagic/models/photo.py` | New model fields |
| `src/imagic/services/preview_engine.py` | OpenCV blurs, shared luminance, cv2 helpers |
| `src/imagic/services/feedback_worker.py` | **NEW** — Background feedback worker |
| `src/imagic/views/culling_preview.py` | Shortcut hints, RAW decode loading overlay |
| `src/imagic/views/export_gallery.py` | Export progress overlay updates |
| `src/imagic/views/main_window.py` | `settings_requested` signal, menu wiring |
| `src/imagic/views/photo_editor.py` | Edit proxy, zero-copy QImage, DHT demosaic, prefetch |
| `src/imagic/views/widgets/image_viewer.py` | Viewer updates |
| `src/imagic/views/widgets/status_bar.py` | Exported count display |
| `src/imagic/views/style_chooser.py` | Style chooser updates |
| `packaging/windows/imagic-installer.iss` | Version → 0.2.0 |
| `packaging/windows/imagic.spec` | Version → 0.2.0 |

---

### macOS rebuild required

The macOS DMG must be rebuilt from this commit to pick up all v0.2.0 changes. Run `packaging/macos/build-desktop.sh` from the MacBook.

---

## 2026-03-19 — Session: Admin CRM Dashboard

**Branch:** `main`

---

### What was done this session

#### 1. Admin CRM Dashboard (NEW)
- Created a secure admin dashboard at `/admin` for managing key sales and analytics.
- **Authentication:** Protected by `IMAGIC_ADMIN_API_KEY`. Uses a dedicated login page and session cookie (`imagic_admin_token`).
- **Features:**
  - **Key Sales Listing:** View all desktop purchases with customer email, license key, and fulfillment status.
  - **Email Resend:** One-click option to resend fulfillment emails to customers.
  - **Manual Key Issuance:** Ability to generate new desktop or web credit keys from the UI.
  - **Analytics:** Basic stats including total sales, daily sales volume (30-day window), and download variant distribution.

#### 2. Backend Enhancements
- `account_store.py` — Added `get_all_desktop_purchases()` and `get_sales_analytics()` methods.
- `main.py` (website) — Added new admin API endpoints:
  - `GET /admin` — Renders login or dashboard.
  - `POST /api/admin/login` — Authenticates with API key.
  - `GET /api/admin/data` — Fetches sales and analytics.
  - `POST /api/admin/resend-email` — Triggers email resend for a specific purchase.

---

### Files changed (5 files)

| File | Change |
|------|--------|
| `website/api/account_store.py` | Added data retrieval methods for admin stats |
| `website/api/main.py` | Added admin routes and API logic |
| `website/templates/admin_login.html` | **NEW** — Admin login page |
| `website/templates/admin.html` | **NEW** — Admin CRM dashboard |
| `DEVLOG.md` | This entry |

---

## 2026-03-18/19 — Session: Launch readiness, licensing, email, installer hosting

**Branch:** `main`

---

### What was done this session

#### 1. Linux Marked "Coming Soon"
- `index.html` — Linux download button replaced with disabled "Coming Soon" badge.
- `desktop.html` — Added platform availability pills (Windows ✓, macOS ✓, Linux — Coming Soon). Checkout note updated.
- `blog_post.html` — Changed "Windows, Mac, Linux" to "Windows & Mac (Linux coming soon)".

#### 2. macOS Delivery Variants
- `desktop_delivery.py` — Added `standard_macos` and `rawtherapee_macos` variants with env vars `IMAGIC_DESKTOP_MACOS_INSTALLER_PATH/_URL` and `IMAGIC_DESKTOP_MACOS_BUNDLE_PATH/_URL`. Added `VALID_VARIANTS` set.
- `account_store.py` — Expanded variant whitelist to include macOS variants.
- `stripe_integration.py` — Fulfillment now issues macOS download tokens and passes macOS links to email.
- `main.py` (website) — `/api/desktop/order-status` returns `macos_download_url` and `macos_bundle_download_url`.
- `desktop.js` — Platform-labeled download buttons with OS icons (Windows/macOS).

#### 3. Activation Gate Enforced
- `defaults.py` — Changed `require_activation` from `False` to `True`, set `license_api_base_url` to `https://imagic.ink`.
- `default_config.yaml` — Mirrored same changes.
- Fresh installs now require product key activation before the app is usable.

#### 4. EULA
- **NEW:** `packaging/EULA.txt` — 10-section end-user license agreement.
- `imagic-installer.iss` — Added `LicenseFile=..\..\packaging\EULA.txt` so Inno Setup shows EULA acceptance during install.

#### 5. Update Checker
- `license_client.py` — Added `check_for_update(current_version)` method calling `GET /api/desktop/latest-version`. Added `_version_tuple()` for semantic version comparison.
- `main.py` (desktop) — Added `_check_for_updates_async()` background thread after activation. 2-second delayed status bar notification if update available.
- `main.py` (website) — New endpoint `GET /api/desktop/latest-version` driven by `IMAGIC_DESKTOP_LATEST_VERSION` env var (defaults to "0.1.0").

#### 6. Email Delivery (Resend SMTP)
- Configured Resend as SMTP provider (`smtp.resend.com:465` SSL, from `snap@imagic.ink`).
- DNS records on Namecheap: DKIM, SPF (`send` subdomain), DMARC, MX for Resend.
- All SMTP secrets set on Fly.io.

#### 7. Email Receiving (ImprovMX)
- Set up ImprovMX for `imagic.ink` forwarding to personal inbox.
- Aliases: `snap@imagic.ink` (sales), `help@imagic.ink` (support).
- Both aliases configured as Gmail "Send mail as" via Resend SMTP.

#### 8. Installer Hosting
- Windows installers (`imagic-desktop-setup.exe`, `imagic-desktop-recommended-rawtherapee-setup.exe`) uploaded to GitHub Releases at `Lukefen31/imagic-releases v0.1.0`.
- macOS installer (`imagic-desktop.dmg`) uploaded from MacBook to same release.
- Fly.io env vars set: `IMAGIC_DESKTOP_INSTALLER_URL`, `IMAGIC_DESKTOP_BUNDLE_URL`, `IMAGIC_DESKTOP_MACOS_INSTALLER_URL`.

#### 9. Stripe Webhook Verified
- Webhook endpoint: `https://imagic.ink/api/stripe/webhook` (custom domain confirmed on Fly.io with SSL).
- Listening for `checkout.session.completed`. Signing secret updated on Fly.io.

#### 10. Custom Domain
- `imagic.ink` and `www.imagic.ink` confirmed as custom domains on Fly.io with issued SSL certs.
- License API base URL updated from `imagic-ink.fly.dev` to `imagic.ink`.

---

### Files changed (20 files)

| File | Change |
|------|--------|
| `config/default_config.yaml` | `require_activation: true`, `license_api_base_url` → `https://imagic.ink` |
| `packaging/EULA.txt` | **NEW** — End-user license agreement |
| `packaging/windows/imagic-installer.iss` | Added `LicenseFile` for EULA |
| `packaging/windows/README.md` | Added macOS env var docs |
| `src/imagic/config/defaults.py` | `require_activation: True`, `license_api_base_url` → `https://imagic.ink` |
| `src/imagic/main.py` | Update checker (`_check_for_updates_async`), status bar notification |
| `src/imagic/services/license_client.py` | `check_for_update()`, `_version_tuple()` |
| `website/api/account_store.py` | macOS variant whitelist |
| `website/api/desktop_delivery.py` | macOS variants, `VALID_VARIANTS`, email with platform sections |
| `website/api/main.py` | macOS order-status links, `/api/desktop/latest-version` endpoint |
| `website/api/stripe_integration.py` | macOS download token fulfillment |
| `website/static/css/editor.css` | Styling updates |
| `website/static/css/style.css` | Styling updates |
| `website/static/js/desktop.js` | Platform-labeled download buttons |
| `website/templates/app.html` | Template updates |
| `website/templates/blog_post.html` | "Linux coming soon" text |
| `website/templates/desktop.html` | Platform pills, checkout note |
| `website/templates/desktop_thanks.html` | Template updates |
| `website/templates/index.html` | Linux "Coming Soon" badge |
| `DEVLOG.md` | This entry |

---

### macOS rebuild required — IMPORTANT

The current macOS DMG on GitHub Releases was built **before** this session's changes.
It is missing critical functionality. The DMG **must be rebuilt** on the MacBook after pulling.

#### What the current DMG is missing

1. **Activation gate is OFF** — the app launches without asking for a product key. Users could use the app for free.
   - Fix is in: `src/imagic/config/defaults.py` → `require_activation: True`
   - Fix is in: `config/default_config.yaml` → `require_activation: true`

2. **License API URL is blank** — even if activation were on, it wouldn't know where to call.
   - Fix is in: `src/imagic/config/defaults.py` → `license_api_base_url: "https://imagic.ink"`
   - Fix is in: `config/default_config.yaml` → `license_api_base_url: "https://imagic.ink"`

3. **No update checker** — the app won't notify users when a new version is available.
   - Fix is in: `src/imagic/main.py` → `_check_for_updates_async()` function
   - Fix is in: `src/imagic/services/license_client.py` → `check_for_update()` method

4. **No EULA** — The EULA file exists at `packaging/EULA.txt` but the macOS build script doesn't currently reference it. Consider adding it to the DMG or showing it on first launch (optional, lower priority).

#### Rebuild steps on Mac

```bash
# 1. Pull the latest code
cd ~/path/to/imagic
git pull origin main

# 2. Verify the critical changes are present
grep -n "require_activation" src/imagic/config/defaults.py
#   Expected output: "require_activation": True

grep -n "license_api_base_url" src/imagic/config/defaults.py
#   Expected output: "license_api_base_url": "https://imagic.ink"

grep -n "_check_for_updates_async" src/imagic/main.py
#   Expected: should find the function definition

grep -n "check_for_update" src/imagic/services/license_client.py
#   Expected: should find the method definition

# 3. Make sure venv is set up and deps installed
source .venv/bin/activate  # or: python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# 4. Run the macOS build script
chmod +x packaging/macos/build-desktop.sh
./packaging/macos/build-desktop.sh

# 5. The output DMG will be at dist/macos/imagic-desktop.dmg (or similar)
# Verify it exists:
ls -lh dist/macos/*.dmg

# 6. Upload the new DMG to replace the old one on GitHub Releases
gh release upload v0.1.0 dist/macos/imagic-desktop.dmg --repo Lukefen31/imagic-releases --clobber
```

#### How to verify the rebuild worked

1. Mount the new DMG, drag imagic to Applications, launch it
2. It **should prompt for a product key** before showing the main UI
3. Enter a test key or dismiss — the gate should block access until activated
4. After activation, check the status bar after ~2 seconds — no "Update available" message should appear (since version matches `0.1.0`)

---

### Infrastructure summary

| Service | Detail |
|---------|--------|
| Hosting | Fly.io (`imagic-ink`, LHR region) |
| Domain | `imagic.ink` + `www.imagic.ink` (SSL issued) |
| Payments | Stripe (live, €10 one-time, `price_1TCROrQtDHMLMuXb4aBbuFYa`) |
| Email out | Resend (`smtp.resend.com:465`, from `snap@imagic.ink`) |
| Email in | ImprovMX → personal inbox (`snap@`, `help@`) |
| Installers | GitHub Releases (`Lukefen31/imagic-releases`) |
| Webhook | `https://imagic.ink/api/stripe/webhook` |

---

## 2026-03-13 — Session: AI UX polish, speech bubble & tutorial overlay

**Commit:** `ac20459` (pushed to `origin/main`)  
**Branch:** `main`  
**Python:** 3.13.5, venv at `.venv`  
**App entry:** `imagic` (or `.venv/bin/imagic`)

---

### What was done this session

#### 1. Clear Library Bug Fix
- `_on_clear_library()` in `main.py` had a `NameError` (`count` undefined on exception) silently swallowed by PyQt6.
- Fixed: initialised `count=0`, added `return` on error, added `synchronize_session=False`, clears review grid on success, shows error status on failure.

#### 2. AI Loading Modal (NEW FILE)
**`src/imagic/views/widgets/ai_loading_modal.py`**
- Custom QPainter-based animated overlay: spinning arc (conical gradient), pulsing glow ring, leading dot, progress bar with shimmer highlight, animated trailing dots.
- API: `show_message(title, subtitle)`, `set_progress(current, total)`, `hide_modal()`.
- 60fps animation via QTimer.
- **Integrated into:**
  - `photo_editor.py` — batch optimize, individual AI tools, RAW decode.
  - `main.py` — AI analysis, re-analyse all, duplicate detection (QTimer polling pattern).

#### 3. AI Variation Engine
**`photo_editor.py`** — `vary_suggestions()` + `_ai_run_counter`
- 6 curated flavour profiles (punchier, softer/lifted, cooler/vivid, warmer/muted, dreamy/film, crisp/bold) for runs 1–6.
- Seeded random jitter for runs 7+ (deterministic per run number).
- Applied to: Auto-Enhance, White Balance, Denoise, Sharpen, B&W, Optimize All.
- Counter resets when a new photo set is loaded.

#### 4. Progressive Strictness on Re-Analyse
**`ai_controller.py`** — `strictness` parameter added to `analyse_pending()` and `_analyse_and_decide()`.
- Each press of Re-Analyse All increments `_reanalyse_count` in `main.py`.
- Formula: `strictness = _reanalyse_count * 0.05` (+5% per press).
- Thresholds: `effective_keep = min(0.98, base_keep + strictness)`, always ≥0.05 gap from trash.

#### 5. Speech Bubble & Tutorial Overlay (NEW FILE)
**`src/imagic/views/widgets/speech_bubble.py`**
- **`SpeechBubble`** — floating callout with custom-painted pointer triangle (any edge), title + body, action button, auto-dismiss timer, drop shadow.
- **`TutorialOverlay`** — full-window dim overlay with widget cutout highlight, Back/Next/Skip/Finish nav, step counter. Resolves target widgets by dotted attribute path (supports list indexing: `_step_buttons.0`).

**`main_window.py` changes:**
- `analyse_btn` / `reanalyse_btn` promoted to `self._analyse_btn` / `self._reanalyse_btn`.
- Added `tutorial_requested` signal.
- Added **Help** menu → **Start Tutorial** (Ctrl+T).

**`main.py` wiring:**
- Post-analysis speech bubble: appears once after first analysis near Re-Analyse button. Auto-hides after 12s or "Got it!" click.
- Tutorial: 7 steps (Welcome → Import → Analyse → Re-Analyse → Review → Edit → Export), launched from Help menu.

#### 6. Thumbnail Generator Enhancement
- Added CLI fallback strategies: `rawtherapee-cli` and `darktable-cli` when `rawpy` is unavailable.
- Batch commits every 10 thumbnails to avoid losing progress.

#### 7. Test Fix
- `test_cli_orchestrator.py`: replaced hardcoded `"python"` with `sys.executable`.

---

### Files changed (9 files)

| File | Change |
|------|--------|
| `src/imagic/views/widgets/ai_loading_modal.py` | **NEW** — animated AI processing overlay |
| `src/imagic/views/widgets/speech_bubble.py` | **NEW** — speech bubble + tutorial overlay |
| `src/imagic/main.py` | AI modal integration, speech bubble tip, tutorial wiring, clear library fix, strictness system |
| `src/imagic/views/main_window.py` | Instance attrs for analyse/reanalyse btns, tutorial_requested signal, Help menu |
| `src/imagic/views/photo_editor.py` | AI modal integration, variation engine, run counter |
| `src/imagic/controllers/ai_controller.py` | `strictness` parameter for progressive re-analysis |
| `src/imagic/controllers/library_controller.py` | Thumbnail batch commits, error handling |
| `src/imagic/services/thumbnail_generator.py` | CLI fallback (rawtherapee/darktable), strategy refactor |
| `tests/test_cli_orchestrator.py` | `sys.executable` fix |

---

### Known issues / TODO

- **GitHub Actions deploy broken** — `FLY_API_TOKEN` secret missing. Use `flyctl deploy` directly for now.
- **Web app removed** — Site redirects to desktop downloads only (desktop-first pivot).
- **Tutorial polish** — may need position tweaks depending on window size; step targets assume widgets are visible.
- **Speech bubble positioning** — relies on `mapTo(parent)` which works but could drift if the analyse page isn't active when bubble fires (guarded by `isVisible()` check).

---

### How to continue

```bash
cd /Users/Luke/imagic
git pull origin main
source .venv/bin/activate
# If venv doesn't exist: python3.13 -m venv .venv && pip install -e .
imagic
```

- **Ctrl+T** or **Help → Start Tutorial** to test the tutorial overlay.
- Import photos → Run AI Analysis → speech bubble should appear near Re-Analyse button.
- Press Re-Analyse multiple times to test progressive strictness.
- Open editor → use any AI tool twice on same photo to see variation.

---

## 2026-03-12 — Session: Web app iteration, production deploy

**Branch:** `main`

---

### Current state
- Desktop app and web app live in the same repository.
- Production web app is deployed on Fly as `imagic-ink`.
- Current production topology is a single Fly machine because uploads and generated assets are still stored on local disk.

### Recent work completed
- Built and iterated on the browser workflow in `website/`.
- Added web upload, analysis, review, edit, export, persistence, and account/payment support.
- Added production deployment files including `Dockerfile`, `.dockerignore`, and `fly.toml`.
- Added/improved branding assets and switched footer branding to the wide logo.
- Reduced thumbnail request pressure and hardened thumbnail generation.
- Added server-backed export for the web flow.
- Added editor-source routing for browser-native image formats.
- Added local restore validation and invalidated stale browser restore state.
- Reduced Fly to one machine to avoid cross-machine session loss with local upload storage.

### Important known constraints
- Upload/session/thumbnail/export files are still stored on machine-local disk under `uploads/`.
- Because of that, multi-machine production is unsafe without shared storage.
- Large RAW uploads are still slow mainly due to network transfer and server-proxied uploads.
- Large analysis batches can still be expensive even after recent optimization work.

### Recommended next steps
- Move uploads, thumbnails, exports, and session assets to shared object storage.
- Switch web uploads to direct browser-to-storage uploads.
- Move heavier post-upload work to background jobs with progress polling.
- Re-enable safe horizontal scaling only after shared storage is in place.

### Useful operational notes
- Git remote: `origin = https://github.com/Lukefen31/imagic.git`
- Main branch: `main`
- Fly app: `imagic-ink`

### Handoff note
- If continuing web performance work, start with storage architecture before further UI optimization.
- If continuing product work, both the desktop licensing flow and the web workflow are active areas in this repo.
