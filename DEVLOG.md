# Imagic — Dev Log

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

### macOS rebuild required

The macOS DMG (`v0.1.0-macos`) was built before this session's changes. After pulling on Mac, rebuild the DMG to include:
- Activation gate (`require_activation: True`)
- License API URL (`https://imagic.ink`)
- Update checker in `main.py` and `license_client.py`

```bash
cd ~/path/to/imagic
git pull origin main
# Rebuild DMG using packaging/macos/build-desktop.sh
```

Then re-upload to `Lukefen31/imagic-releases` as an updated asset on the `v0.1.0` release.

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
