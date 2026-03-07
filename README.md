# Imagic — Professional Photo Editing Orchestrator

A robust, cross-platform desktop application (Python + PyQt6) that **orchestrates** a professional-grade photo editing workflow. Instead of rebuilding rendering engines, Imagic acts as an intelligent "glue" layer that:

- **Scans & ingests** RAW photo directories into a local SQLite library
- **AI-powered culling** — automatically scores images for quality (sharpness, exposure, detail) and flags duplicates using perceptual hashing
- **Decision engine** — keeps the best shots, trash the rest — configured by threshold
- **Batch processing** — calls `darktable-cli` or `rawtherapee-cli` to apply styles and export finals, all in the background
- **Crash-resilient** — SQLite status tracking + automatic resume on restart

## Architecture

```
┌─────────────────────────────────────────────┐
│                  PyQt6 GUI                  │  ← View layer
├─────────────────────────────────────────────┤
│              Controllers (MVC)              │  ← Orchestration
├────────┬──────────┬──────────┬──────────────┤
│Scanner │TaskQueue │CLI Orch. │ AI Analyzers  │  ← Services
├────────┴──────────┴──────────┴──────────────┤
│      SQLAlchemy + SQLite (Models)           │  ← Data layer
└─────────────────────────────────────────────┘
```

## Quick Start

```bash
# 1. Create a virtual environment
python -m venv .venv
source .venv/bin/activate    # macOS / Linux
# .venv\Scripts\activate   # Windows

# 2. Install dependencies
pip install -e ".[dev]"

# 3. Run the GUI
imagic

# 4. Or run headless (batch mode)
imagic --headless --scan ~/Photos/2024 --analyse --export

# 5. Run tests
pytest
```

## Configuration

Edit `~/.imagic/config.yaml` (created on first run) or pass `--config path/to/config.yaml`.

Key settings:
- `cli_tools.darktable_cli` — path to your `darktable-cli` executable
- `ai.keep_threshold` — minimum quality score to auto-keep (default: 0.8)
- `ai.trash_threshold` — maximum score to auto-trash (default: 0.3)
- `processing.max_workers` — parallel worker threads (default: 4)

## Project Structure

```
src/imagic/
├── main.py              # Entry point (GUI + headless modes)
├── models/              # SQLAlchemy ORM models + enums
├── views/               # PyQt6 GUI (fully decoupled from logic)
├── controllers/         # MVC controllers (wiring layer)
├── services/            # Scanner, TaskQueue, CLI orchestrator, export
├── ai/                  # Pluggable analysers (quality, duplicates, CLIP)
├── config/              # YAML settings manager
└── utils/               # Logging, pathlib helpers, image utilities
tests/                   # Pytest unit tests (no GUI required)
config/                  # Default YAML configs
```

## The 4-Stage Automation Pipeline

| Stage | Module | What it does |
|-------|--------|-------------|
| 1. Ingestion | `LibraryScanner` | Scans directories, registers RAW files, generates thumbnails |
| 2. AI Analyst | `QualityScorer` / `DuplicateDetector` | Scores sharpness/exposure, computes perceptual hashes |
| 3. Decision | `AIController` | Auto-sorts into KEEP / TRASH based on thresholds |
| 4. Production | `CLIOrchestrator` → `ExportService` | Calls darktable-cli with XMP sidecars, exports JPEGs |

Every photo's status is tracked in SQLite. If the app crashes, `resume_pending_work()` automatically picks up where it left off.

## License

MIT
