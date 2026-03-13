# Imagic — Dev Log

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
