# DEV LOG

## 2026-03-12

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