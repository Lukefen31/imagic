"""imagic web — FastAPI backend for the browser-based photo editor."""

from __future__ import annotations

import importlib
import os
import shutil
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from .account_store import account_store
from .blog_posts import get_published_posts, get_post_by_slug, get_related_posts
from .desktop_delivery import resolve_download_target
from .rate_limit import RateLimiter
from .processing import (
    RAW_EXTENSIONS,
    analyse_quality,
    detect_duplicates,
    suggest_crop,
    generate_grade_previews,
    generate_display_thumbnail,
    load_cached_analysis_result,
    native_export,
    prepare_analysis_source,
    save_cached_analysis_result,
)
from .stripe_integration import (
    create_checkout_session,
    create_desktop_checkout_session,
    desktop_is_configured,
    handle_webhook,
    is_configured as stripe_configured,
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

UPLOAD_DIR = Path(__file__).resolve().parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp",
    # RAW formats
    ".cr2", ".cr3", ".nef", ".arw", ".dng", ".orf",
    ".rw2", ".raf", ".pef", ".srw", ".raw", ".3fr",
    ".iiq", ".rwl", ".mrw", ".x3f",
}
MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB (RAW files can be large)
UPLOAD_CHUNK_SIZE = 8 * 1024 * 1024
BASE_URL = os.environ.get("IMAGIC_BASE_URL", "http://localhost:8000").rstrip("/")
COOKIE_SECURE = os.environ.get(
    "IMAGIC_COOKIE_SECURE",
    "true" if BASE_URL.startswith("https://") else "false",
).lower() in {"1", "true", "yes", "on"}
GOOGLE_REDIRECT_URL = os.environ.get(
    "GOOGLE_REDIRECT_URL",
    f"{BASE_URL}/api/auth/google/callback",
)

app = FastAPI(title="imagic web", version="1.0.0")

app.add_middleware(ProxyHeadersMiddleware, trusted_hosts="*")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("IMAGIC_SESSION_SECRET", "change-me-in-production"),
    same_site="lax",
    https_only=COOKIE_SECURE,
)

# Static files and templates
WEBSITE_DIR = Path(__file__).resolve().parent.parent
app.mount("/static", StaticFiles(directory=WEBSITE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=WEBSITE_DIR / "templates")

rate_limiter = RateLimiter(free_limit=20)
SESSION_COOKIE = "imagic_session"
DEFAULT_CREDIT_PACK_SIZE = 500
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_OAUTH_ENABLED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
_THUMBNAIL_LOCKS: dict[str, threading.Lock] = {}
_THUMBNAIL_LOCKS_GUARD = threading.Lock()

# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/favicon.ico")
async def favicon():
    ico = WEBSITE_DIR / "static" / "favicon.ico"
    if ico.is_file():
        return FileResponse(ico, media_type="image/x-icon")
    return Response(status_code=204)


@app.get("/app")
async def web_app(request: Request):
    """Web app is paused — redirect visitors to the homepage."""
    return RedirectResponse(url="/", status_code=302)


@app.get("/desktop", response_class=HTMLResponse)
async def desktop_download_page(request: Request):
    user = _current_user(request)
    return templates.TemplateResponse(
        "desktop.html",
        {
            "request": request,
            "prefill_email": user["email"] if user else "",
            "desktop_checkout_enabled": desktop_is_configured(),
            "desktop_bundle_available": resolve_download_target("rawtherapee") is not None,
        },
    )


@app.get("/blog", response_class=HTMLResponse)
async def blog_index(request: Request, cat: str = ""):
    posts = get_published_posts()
    if cat:
        posts = [p for p in posts if p["category"] == cat]
    for post in posts:
        post["date_display"] = _format_date(post["date"])
    return templates.TemplateResponse("blog_index.html", {"request": request, "posts": posts})


@app.get("/blog/{slug}", response_class=HTMLResponse)
async def blog_post(request: Request, slug: str):
    post = get_post_by_slug(slug)
    if post is None:
        raise HTTPException(404, "Post not found.")
    post["date_display"] = _format_date(post["date"])
    # Build prev/next navigation
    all_posts = get_published_posts()
    idx = next((i for i, p in enumerate(all_posts) if p["slug"] == slug), None)
    prev_post = all_posts[idx - 1] if idx and idx > 0 else None
    next_post = all_posts[idx + 1] if idx is not None and idx < len(all_posts) - 1 else None
    related = get_related_posts(slug, limit=3)
    return templates.TemplateResponse(
        "blog_post.html",
        {
            "request": request,
            "post": post,
            "prev_post": prev_post,
            "next_post": next_post,
            "related_posts": related,
        },
    )


@app.get("/sitemap.xml")
async def sitemap():
    posts = get_published_posts()
    urls = [
        "<url><loc>https://imagic.app/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>",
        "<url><loc>https://imagic.app/desktop</loc><changefreq>monthly</changefreq><priority>0.9</priority></url>",
        "<url><loc>https://imagic.app/blog</loc><changefreq>daily</changefreq><priority>0.8</priority></url>",
    ]
    for post in posts:
        urls.append(
            f"<url><loc>https://imagic.app/blog/{post['slug']}</loc>"
            f"<lastmod>{post['date']}</lastmod><changefreq>monthly</changefreq><priority>0.7</priority></url>"
        )
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += "\n".join(urls)
    xml += "\n</urlset>"
    return Response(content=xml, media_type="application/xml")


@app.get("/robots.txt")
async def robots():
    content = "User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /uploads/\n\nSitemap: https://imagic.app/sitemap.xml\n"
    return Response(content=content, media_type="text/plain")


def _format_date(iso_date: str) -> str:
    try:
        from datetime import datetime as _dt
        d = _dt.strptime(iso_date, "%Y-%m-%d")
        return f"{d.strftime('%B')} {d.day}, {d.year}"
    except Exception:
        return iso_date


@app.get("/desktop/thanks", response_class=HTMLResponse)
async def desktop_thanks_page(request: Request, session_id: str = ""):
    return templates.TemplateResponse(
        "desktop_thanks.html",
        {
            "request": request,
            "session_id": session_id.strip(),
            "desktop_bundle_available": resolve_download_target("rawtherapee") is not None,
        },
    )


# ---------------------------------------------------------------------------
# API — Upload
# ---------------------------------------------------------------------------


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _session_token(request: Request) -> str:
    return request.cookies.get(SESSION_COOKIE, "")


def _current_user(request: Request) -> dict | None:
    token = _session_token(request)
    if not token:
        return None
    return account_store.get_user_by_session(token)


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        max_age=60 * 60 * 24 * 30,
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE)


def _google_client():
    if not GOOGLE_OAUTH_ENABLED:
        raise HTTPException(503, "Google OAuth is not configured.")
    oauth_mod = importlib.import_module("authlib.integrations.starlette_client")
    oauth = oauth_mod.OAuth()
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth.create_client("google")


def _validate_file(file: UploadFile) -> None:
    if not file.filename:
        raise HTTPException(400, "No filename provided.")
    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            400, f"Unsupported format '{ext}'. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )


async def _store_upload(file: UploadFile, session_dir: Path) -> dict[str, str | int]:
    ext = Path(file.filename).suffix.lower()
    size = 0
    file_id = uuid.uuid4().hex[:16]
    temp_path = session_dir / f"{file_id}.upload"

    try:
        with temp_path.open("wb") as handle:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    raise HTTPException(413, f"File '{file.filename}' exceeds 100 MB limit.")
                handle.write(chunk)
    finally:
        await file.close()

    dest = session_dir / f"{file_id}{ext}"
    temp_path.replace(dest)
    return {
        "file_id": file_id,
        "filename": file.filename,
        "size": size,
    }


@app.post("/api/upload")
async def upload_photos(
    request: Request,
    files: list[UploadFile] = File(...),
):
    """Upload one or more images. Returns a session ID and file IDs."""
    ip = _client_ip(request)
    user = _current_user(request)

    free_remaining = rate_limiter.remaining(ip)
    credit_balance = account_store.get_credit_balance(user["id"]) if user else 0
    total_remaining = free_remaining + credit_balance
    if total_remaining <= 0:
        raise HTTPException(
            429,
            "No image allowance remaining. Sign in and buy another 500-image pack to continue.",
        )
    if len(files) > total_remaining:
        raise HTTPException(
            429,
            f"Only {total_remaining} image slots remain ({free_remaining} free today, {credit_balance} paid credits).",
        )

    session_id = uuid.uuid4().hex[:12]
    session_dir = UPLOAD_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    uploaded = []
    for f in files:
        _validate_file(f)
        uploaded.append(await _store_upload(f, session_dir))

    free_to_consume = min(len(files), free_remaining)
    paid_to_consume = max(0, len(files) - free_to_consume)
    if free_to_consume:
        rate_limiter.consume(ip, free_to_consume)
    if paid_to_consume:
        if not user or not account_store.consume_credits(user["id"], paid_to_consume):
            raise HTTPException(409, "Could not reserve paid credits for this upload.")

    return {
        "session_id": session_id,
        "files": uploaded,
        "remaining_today": rate_limiter.remaining(ip),
        "credit_balance": account_store.get_credit_balance(user["id"]) if user else 0,
    }


# ---------------------------------------------------------------------------
# API — Analyse
# ---------------------------------------------------------------------------


@app.get("/api/analyse/{session_id}")
async def analyse_session(session_id: str):
    """Run AI quality scoring on all photos in a session."""
    session_dir = _get_session_dir(session_id)
    images = [f for f in session_dir.glob("*") if f.is_file()]
    if not images:
        raise HTTPException(404, "No images found in session.")

    results = []
    for img_path in images:
        if img_path.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue

        cached = load_cached_analysis_result(img_path)
        if cached is not None:
            score_data = cached
        else:
            analysis_source = await run_in_threadpool(prepare_analysis_source, img_path)
            score_data = await run_in_threadpool(analyse_quality, analysis_source)
            await run_in_threadpool(save_cached_analysis_result, img_path, score_data)

        results.append({
            "file_id": img_path.stem,
            "filename": img_path.name,
            **score_data,
        })

    # Sort best → worst
    results.sort(key=lambda r: r.get("overall_score", 0), reverse=True)
    return {"session_id": session_id, "results": results}


@app.get("/api/duplicates/{session_id}")
async def find_duplicates(session_id: str):
    """Detect duplicate/near-duplicate images in a session."""
    session_dir = _get_session_dir(session_id)
    images = [
        p for p in session_dir.glob("*")
        if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS
    ]
    groups = detect_duplicates(images)
    return {"session_id": session_id, "duplicate_groups": groups}


@app.get("/api/crop/{session_id}/{file_id}")
async def get_crop_suggestion(session_id: str, file_id: str):
    """Get auto-crop suggestion for a single image."""
    img_path = _find_image(session_id, file_id)
    crop = suggest_crop(img_path)
    return {"file_id": file_id, **crop}


@app.get("/api/grades/{session_id}/{file_id}")
async def get_grade_previews(session_id: str, file_id: str):
    """Generate color-grade preview thumbnails for an image."""
    img_path = _find_image(session_id, file_id)
    grades = generate_grade_previews(img_path, session_id)
    return {"file_id": file_id, "grades": grades}


# ---------------------------------------------------------------------------
# API — Download
# ---------------------------------------------------------------------------


@app.post("/api/export/{session_id}/{file_id}")
async def export_photo(session_id: str, file_id: str, request: Request):
    """Export a photo using the native Python processing engine."""
    img_path = _find_image(session_id, file_id)
    body = await request.json() if request.headers.get("content-type") == "application/json" else {}
    grade = body.get("grade", "natural")
    quality_data = body.get("quality_data")
    manual_overrides = body.get("overrides")
    result = native_export(img_path, session_id, grade, quality_data, manual_overrides)
    if not result.get("success"):
        raise HTTPException(500, result.get("error", "Export failed"))
    return result


@app.get("/api/download/{session_id}/{filename:path}")
async def download_file(session_id: str, filename: str):
    """Download a processed/graded image."""
    session_dir = _get_session_dir(session_id)
    # Sanitise filename to prevent traversal
    safe_filename = Path(filename)
    if ".." in safe_filename.parts:
        raise HTTPException(403, "Access denied.")
    file_path = session_dir / safe_filename
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(404, "File not found.")
    if not file_path.resolve().is_relative_to(session_dir.resolve()):
        raise HTTPException(403, "Access denied.")
    return FileResponse(file_path, filename=safe_filename.name)


@app.get("/api/thumbnail/{session_id}/{file_id}")
async def get_thumbnail(session_id: str, file_id: str, kind: str = "grid"):
    """Get a display-size JPEG thumbnail for a photo.

    Generates a resized JPEG the first time and caches it so subsequent
    requests are fast.  Handles TIFF and other formats that browsers
    cannot display natively.
    """
    img_path = _find_image(session_id, file_id)
    thumb_dir = img_path.parent / "thumbs"
    thumb_dir.mkdir(exist_ok=True)
    clean_kind = kind if kind in {"grid", "editor"} else "grid"
    thumb_path = thumb_dir / f"{img_path.stem}.{clean_kind}.jpg"

    max_size = (320, 320) if clean_kind == "grid" else (1280, 1280)
    raw_embedded_only = clean_kind == "grid"

    if not thumb_path.exists():
        generated = await run_in_threadpool(
            _ensure_cached_thumbnail,
            img_path,
            thumb_path,
            max_size,
            82 if clean_kind == "grid" else 88,
            raw_embedded_only,
        )
        if not generated or not thumb_path.exists():
            raise HTTPException(500, "Thumbnail generation failed.")

    return FileResponse(thumb_path, media_type="image/jpeg")


@app.get("/api/editor-source/{session_id}/{file_id}")
async def get_editor_source(session_id: str, file_id: str):
    """Return the best editor source for a file.

    Native browser formats are served directly so the editor works from the
    original upload. RAW/TIFF-like formats fall back to the cached editor JPEG.
    """
    img_path = _find_image(session_id, file_id)
    if img_path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
        return FileResponse(img_path)
    return await get_thumbnail(session_id, file_id, kind="editor")


# ---------------------------------------------------------------------------
# API — Usage
# ---------------------------------------------------------------------------


@app.get("/api/usage")
async def get_usage(request: Request):
    """Check remaining free usage, paid credits, and session status."""
    ip = _client_ip(request)
    user = _current_user(request)
    limit = rate_limiter.limit_for(ip)
    remaining = rate_limiter.remaining(ip)
    credit_balance = account_store.get_credit_balance(user["id"]) if user else 0
    return {
        "daily_limit": limit,
        "used_today": limit - remaining,
        "remaining_today": remaining,
        "credit_balance": credit_balance,
        "total_remaining": remaining + credit_balance,
        "is_authenticated": user is not None,
        "email": user["email"] if user else "",
        "google_oauth_enabled": GOOGLE_OAUTH_ENABLED,
        "date": date.today().isoformat(),
    }


@app.get("/api/session/{session_id}")
async def get_session_metadata(session_id: str):
    """Return lightweight metadata for a saved upload session.

    Used by the web app to validate persisted local state before trying to
    restore thumbnails, editor state, or exports after a hard refresh.
    """
    session_dir = _get_session_dir(session_id)
    files = []
    for path in session_dir.glob("*"):
        if not path.is_file() or path.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue
        files.append({
            "file_id": path.stem,
            "filename": path.name,
            "size": path.stat().st_size,
        })

    files.sort(key=lambda item: item["filename"].lower())
    return {"session_id": session_id, "files": files}


@app.post("/api/auth/register")
async def register_account(request: Request):
    body = await request.json()
    email = str(body.get("email", "")).strip()
    password = str(body.get("password", ""))
    try:
        user = account_store.create_user(email, password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    token = account_store.create_session(user["id"])
    response = JSONResponse({
        "ok": True,
        "email": user["email"],
        "credit_balance": user["credit_balance"],
    })
    _set_session_cookie(response, token)
    return response


@app.post("/api/auth/login")
async def login_account(request: Request):
    body = await request.json()
    email = str(body.get("email", "")).strip()
    password = str(body.get("password", ""))
    user = account_store.authenticate_user(email, password)
    if user is None:
        raise HTTPException(401, "Invalid email or password.")

    token = account_store.create_session(user["id"])
    response = JSONResponse({
        "ok": True,
        "email": user["email"],
        "credit_balance": account_store.get_credit_balance(user["id"]),
    })
    _set_session_cookie(response, token)
    return response


@app.post("/api/auth/logout")
async def logout_account(request: Request):
    token = _session_token(request)
    if token:
        account_store.delete_session(token)
    response = JSONResponse({"ok": True})
    _clear_session_cookie(response)
    return response


@app.get("/api/auth/me")
async def auth_me(request: Request):
    user = _current_user(request)
    if user is None:
        return {"authenticated": False, "google_oauth_enabled": GOOGLE_OAUTH_ENABLED}
    return {
        "authenticated": True,
        "email": user["email"],
        "credit_balance": account_store.get_credit_balance(user["id"]),
        "google_oauth_enabled": GOOGLE_OAUTH_ENABLED,
    }


@app.get("/api/auth/google/login")
async def google_login(request: Request):
    client = _google_client()
    redirect_uri = GOOGLE_REDIRECT_URL
    return await client.authorize_redirect(request, redirect_uri)


@app.get("/api/auth/google/callback", name="google_callback")
async def google_callback(request: Request):
    client = _google_client()
    try:
        token = await client.authorize_access_token(request)
        userinfo = token.get("userinfo")
        if not userinfo:
            userinfo = await client.parse_id_token(request, token)
    except Exception as exc:
        raise HTTPException(401, f"Google sign-in failed: {exc}") from exc

    email = str((userinfo or {}).get("email", "")).strip().lower()
    subject = str((userinfo or {}).get("sub", "")).strip()
    if not email or not subject:
        raise HTTPException(400, "Google did not return a usable email account.")

    user = account_store.get_or_create_oauth_user(email, "google", subject)
    session_token = account_store.create_session(user["id"])
    response = RedirectResponse(url="/app?google=1", status_code=302)
    _set_session_cookie(response, session_token)
    return response


@app.post("/api/licenses/redeem")
async def redeem_license(request: Request):
    user = _current_user(request)
    if user is None:
        raise HTTPException(401, "Sign in before redeeming a key.")
    body = await request.json()
    license_key = str(body.get("license_key", ""))
    if not license_key:
        raise HTTPException(400, "License key is required.")
    try:
        balance = account_store.redeem_credit_license(user["id"], license_key)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "credit_balance": balance}


@app.post("/api/licenses/activate")
async def activate_desktop_license(request: Request):
    body = await request.json()
    license_key = str(body.get("license_key", "")).strip()
    device_id = str(body.get("device_id", "")).strip()
    device_name = str(body.get("device_name", "desktop")).strip() or "desktop"

    if not all([license_key, device_id]):
        raise HTTPException(400, "License key and device id are required.")
    try:
        result = account_store.activate_desktop_license(
            license_key=license_key,
            device_id=device_id,
            device_name=device_name,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "ok": True,
        "activation_token": result["activation_token"],
        "email": result["email"],
        "license_key": result["license_key"],
    }


@app.post("/api/licenses/validate")
async def validate_desktop_license(request: Request):
    body = await request.json()
    token = str(body.get("activation_token", "")).strip()
    device_id = str(body.get("device_id", "")).strip()
    if not token or not device_id:
        raise HTTPException(400, "Activation token and device id are required.")
    info = account_store.validate_activation(token, device_id)
    if info is None:
        raise HTTPException(401, "Activation is no longer valid.")
    return {"ok": True, **info}


@app.post("/api/admin/licenses/issue")
async def issue_license(request: Request):
    admin_secret = request.headers.get("x-admin-key", "")
    expected_secret = os.environ.get("IMAGIC_ADMIN_API_KEY", "")
    if not expected_secret or admin_secret != expected_secret:
        raise HTTPException(403, "Admin key required.")
    body = await request.json()
    product_type = str(body.get("product_type", "desktop")).strip()
    credits_total = int(body.get("credits_total", DEFAULT_CREDIT_PACK_SIZE))
    if product_type not in {"desktop", "web_credit"}:
        raise HTTPException(400, "product_type must be 'desktop' or 'web_credit'.")
    issued = account_store.issue_license(
        product_type=product_type,
        credits_total=credits_total if product_type == "web_credit" else 0,
    )
    return {"ok": True, "license": issued}


# ---------------------------------------------------------------------------
# Cleanup (scheduled separately or via middleware)
# ---------------------------------------------------------------------------


@app.post("/api/cleanup/{session_id}")
async def cleanup_session(session_id: str):
    """Delete all files in a session."""
    session_dir = _get_session_dir(session_id)
    shutil.rmtree(session_dir, ignore_errors=True)
    return {"status": "cleaned"}


# ---------------------------------------------------------------------------
# API — Stripe (credit-pack payments)
# ---------------------------------------------------------------------------


@app.post("/api/stripe/checkout")
async def stripe_checkout(request: Request):
    """Create a Stripe Checkout session and return the redirect URL."""
    if not stripe_configured():
        raise HTTPException(503, "Payments are not configured yet.")
    ip = _client_ip(request)
    user = _current_user(request)
    if user is None:
        raise HTTPException(401, "Sign in before purchasing credits.")
    try:
        url = create_checkout_session(ip, user["id"])
        return {"checkout_url": url}
    except Exception as exc:
        raise HTTPException(500, f"Could not create checkout session: {exc}")


@app.post("/api/desktop/checkout")
async def desktop_checkout(request: Request):
    if not desktop_is_configured():
        raise HTTPException(503, "Desktop payments are not configured yet.")
    body = await request.json()
    email = str(body.get("email", "")).strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "A valid delivery email is required.")
    try:
        url = create_desktop_checkout_session(_client_ip(request), email)
        return {"checkout_url": url}
    except Exception as exc:
        raise HTTPException(500, f"Could not create desktop checkout session: {exc}")


@app.get("/api/desktop/order-status")
async def desktop_order_status(session_id: str):
    purchase = account_store.get_desktop_purchase(session_id)
    if purchase is None:
        return {"ready": False, "pending": True}

    standard = account_store.issue_desktop_download(session_id, "standard")
    bundle_link = None
    if resolve_download_target("rawtherapee") is not None:
        bundle = account_store.issue_desktop_download(session_id, "rawtherapee")
        bundle_link = f"/desktop/download/{bundle['token']}"

    return {
        "ready": True,
        "pending": False,
        "delivery_email": purchase["delivery_email"],
        "license_key": purchase["license_key"],
        "email_sent": bool(purchase.get("email_sent_at")),
        "email_error": purchase.get("email_error") or "",
        "download_url": f"/desktop/download/{standard['token']}",
        "bundle_download_url": bundle_link,
    }


@app.get("/desktop/download/{token}")
async def desktop_download(token: str):
    grant = account_store.resolve_desktop_download(token)
    if grant is None:
        raise HTTPException(404, "Download link not found.")

    target = resolve_download_target(str(grant["variant"]))
    if target is None:
        raise HTTPException(404, "This installer is not currently available.")
    if target["kind"] == "redirect":
        return RedirectResponse(url=target["url"], status_code=302)
    return FileResponse(target["path"], filename=target["filename"])


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    """Handle incoming Stripe webhook events."""
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    result = handle_webhook(payload, sig)
    if result.get("status") == "error":
        raise HTTPException(400, result.get("message", "Webhook error"))
    return result


@app.get("/api/stripe/status")
async def stripe_status(request: Request):
    """Check payment and credit status for the current user."""
    user = _current_user(request)
    return {
        "is_authenticated": user is not None,
        "email": user.get("email", "") if user else "",
        "credit_balance": account_store.get_credit_balance(user["id"]) if user else 0,
        "google_oauth_enabled": GOOGLE_OAUTH_ENABLED,
        "stripe_configured": stripe_configured(),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_session_dir(session_id: str) -> Path:
    # Prevent directory traversal
    safe_id = "".join(c for c in session_id if c.isalnum())
    session_dir = UPLOAD_DIR / safe_id
    if not session_dir.exists():
        raise HTTPException(404, "Session not found.")
    if not session_dir.resolve().is_relative_to(UPLOAD_DIR.resolve()):
        raise HTTPException(403, "Access denied.")
    return session_dir


def _find_image(session_id: str, file_id: str) -> Path:
    session_dir = _get_session_dir(session_id)
    safe_file_id = "".join(c for c in file_id if c.isalnum())
    for f in session_dir.glob(f"{safe_file_id}.*"):
        if f.is_file() and f.suffix.lower() in ALLOWED_EXTENSIONS:
            return f
    raise HTTPException(404, f"Image '{file_id}' not found.")


def _get_thumbnail_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _THUMBNAIL_LOCKS_GUARD:
        lock = _THUMBNAIL_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _THUMBNAIL_LOCKS[key] = lock
        return lock


def _ensure_cached_thumbnail(
    image_path: Path,
    output_path: Path,
    max_size: tuple[int, int],
    quality: int,
    raw_embedded_only: bool,
) -> bool:
    lock = _get_thumbnail_lock(output_path)
    with lock:
        if output_path.exists():
            return True
        return generate_display_thumbnail(
            image_path,
            output_path,
            max_size=max_size,
            quality=quality,
            raw_embedded_only=raw_embedded_only,
        )
