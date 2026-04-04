"""imagic web — FastAPI backend for the browser-based photo editor."""

from __future__ import annotations

import hashlib
import importlib
import os
import shutil
import uuid
from datetime import date
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from .account_store import account_store
from .blog_posts import get_published_posts, get_post_by_slug, get_related_posts
from .desktop_delivery import resolve_download_target, VALID_VARIANTS
from .rate_limit import RateLimiter
from .processing import (
    analyse_quality,
    detect_duplicates,
    suggest_crop,
    generate_grade_previews,
    native_export,
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

# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/favicon.ico")
async def favicon():
    ico = WEBSITE_DIR / "static" / "favicon.ico"
    if ico.is_file():
        return FileResponse(ico, media_type="image/x-icon")
    return Response(status_code=204)


@app.get("/app", response_class=HTMLResponse)
async def web_app(request: Request):
    return RedirectResponse(url="/", status_code=302)


@app.get("/blog", response_class=HTMLResponse)
async def blog_index(request: Request, cat: str = ""):
    posts = get_published_posts()
    if cat:
        posts = [p for p in posts if p["category"] == cat]
    for post in posts:
        post["date_display"] = _format_date(post["date"])
    return templates.TemplateResponse(request, "blog_index.html", {"posts": posts})


@app.get("/blog/{slug}", response_class=HTMLResponse)
async def blog_post(request: Request, slug: str):
    post = get_post_by_slug(slug)
    if post is None:
        raise HTTPException(404, "Post not found.")
    post["date_display"] = _format_date(post["date"])
    all_posts = get_published_posts()
    idx = next((i for i, p in enumerate(all_posts) if p["slug"] == slug), None)
    prev_post = all_posts[idx - 1] if idx and idx > 0 else None
    next_post = all_posts[idx + 1] if idx is not None and idx < len(all_posts) - 1 else None
    related = get_related_posts(slug, limit=3)
    return templates.TemplateResponse(
        request,
        "blog_post.html",
        {
            "post": post,
            "prev_post": prev_post,
            "next_post": next_post,
            "related_posts": related,
        },
    )


@app.get("/about", response_class=HTMLResponse)
async def about_page(request: Request):
    return templates.TemplateResponse(request, "docs.html")


@app.get("/docs")
async def docs_redirect():
    return RedirectResponse(url="/about", status_code=301)


@app.get("/changelog", response_class=HTMLResponse)
async def changelog_page(request: Request):
    return templates.TemplateResponse(request, "changelog.html")


@app.get("/community", response_class=HTMLResponse)
async def community_page(request: Request):
    return templates.TemplateResponse(request, "community.html")


@app.get("/contact", response_class=HTMLResponse)
async def contact_page(request: Request):
    return templates.TemplateResponse(request, "contact.html")


@app.get("/sitemap.xml")
async def sitemap():
    posts = get_published_posts()
    urls = [
        "<url><loc>https://imagic.ink/</loc><changefreq>weekly</changefreq><priority>1.0</priority></url>",
        "<url><loc>https://imagic.ink/desktop</loc><changefreq>monthly</changefreq><priority>0.9</priority></url>",
        "<url><loc>https://imagic.ink/blog</loc><changefreq>daily</changefreq><priority>0.8</priority></url>",
        "<url><loc>https://imagic.ink/about</loc><changefreq>monthly</changefreq><priority>0.7</priority></url>",
        "<url><loc>https://imagic.ink/changelog</loc><changefreq>monthly</changefreq><priority>0.7</priority></url>",
        "<url><loc>https://imagic.ink/community</loc><changefreq>monthly</changefreq><priority>0.6</priority></url>",
        "<url><loc>https://imagic.ink/contact</loc><changefreq>monthly</changefreq><priority>0.5</priority></url>",
    ]
    for post in posts:
        urls.append(
            f"<url><loc>https://imagic.ink/blog/{post['slug']}</loc>"
            f"<lastmod>{post['date']}</lastmod><changefreq>monthly</changefreq><priority>0.7</priority></url>"
        )
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += "\n".join(urls)
    xml += "\n</urlset>"
    return Response(content=xml, media_type="application/xml")


@app.get("/robots.txt")
async def robots():
    content = "User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /uploads/\n\nSitemap: https://imagic.ink/sitemap.xml\n"
    return Response(content=content, media_type="text/plain")


@app.get("/desktop", response_class=HTMLResponse)
async def desktop_download_page(request: Request):
    user = _current_user(request)
    return templates.TemplateResponse(
        request,
        "desktop.html",
        {
            "prefill_email": user["email"] if user else "",
            "desktop_checkout_enabled": desktop_is_configured(),
            "desktop_bundle_available": resolve_download_target("rawtherapee") is not None,
        },
    )


@app.get("/desktop/thanks", response_class=HTMLResponse)
async def desktop_thanks_page(request: Request, session_id: str = ""):
    return templates.TemplateResponse(
        request,
        "desktop_thanks.html",
        {
            "session_id": session_id.strip(),
            "desktop_bundle_available": resolve_download_target("rawtherapee") is not None,
        },
    )


def _format_date(iso_date: str) -> str:
    try:
        from datetime import datetime as _dt
        d = _dt.strptime(iso_date, "%Y-%m-%d")
        return f"{d.strftime('%B')} {d.day}, {d.year}"
    except Exception:
        return iso_date


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

        content = await f.read()
        if len(content) > MAX_FILE_SIZE:
            raise HTTPException(413, f"File '{f.filename}' exceeds 100 MB limit.")

        file_id = hashlib.sha256(content).hexdigest()[:16]
        ext = Path(f.filename).suffix.lower()
        dest = session_dir / f"{file_id}{ext}"
        dest.write_bytes(content)

        uploaded.append({
            "file_id": file_id,
            "filename": f.filename,
            "size": len(content),
        })

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
        score_data = analyse_quality(img_path)
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
async def get_thumbnail(session_id: str, file_id: str):
    """Get a display-size JPEG thumbnail for a photo.

    Generates a resized JPEG the first time and caches it so subsequent
    requests are fast.  Handles TIFF and other formats that browsers
    cannot display natively.
    """
    img_path = _find_image(session_id, file_id)
    thumb_dir = img_path.parent / "thumbs"
    thumb_dir.mkdir(exist_ok=True)
    thumb_path = thumb_dir / f"{img_path.stem}.jpg"

    if not thumb_path.exists():
        try:
            from PIL import Image
            img = Image.open(img_path)
            img = img.convert("RGB")
            img.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
            img.save(str(thumb_path), "JPEG", quality=85, optimize=True)
        except Exception:
            # If thumbnail generation fails, try serving the original
            return FileResponse(img_path)

    return FileResponse(thumb_path, media_type="image/jpeg")


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


def _is_admin(request: Request) -> bool:
    admin_token = request.cookies.get("imagic_admin_token", "")
    expected = os.environ.get("IMAGIC_ADMIN_API_KEY", "")
    return bool(expected and admin_token == expected)


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    if not _is_admin(request):
        return templates.TemplateResponse(request, "admin_login.html")
    return templates.TemplateResponse(request, "admin.html")


@app.post("/api/admin/login")
async def admin_login(request: Request):
    body = await request.json()
    key = str(body.get("admin_key", "")).strip()
    expected = os.environ.get("IMAGIC_ADMIN_API_KEY", "")
    if not expected or key != expected:
        raise HTTPException(403, "Invalid admin key.")
    
    response = JSONResponse({"ok": True})
    response.set_cookie(
        "imagic_admin_token",
        key,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        max_age=60 * 60 * 24 * 7,
    )
    return response


@app.get("/api/admin/data")
async def admin_data(request: Request):
    if not _is_admin(request):
        raise HTTPException(403, "Admin access required.")
    
    purchases = account_store.get_all_desktop_purchases()
    analytics = account_store.get_sales_analytics()
    
    return {
        "purchases": purchases,
        "analytics": analytics,
    }


@app.post("/api/admin/resend-email")
async def admin_resend_email(request: Request):
    if not _is_admin(request):
        raise HTTPException(403, "Admin access required.")
    
    body = await request.json()
    session_id = str(body.get("session_id", "")).strip()
    if not session_id:
        raise HTTPException(400, "session_id is required.")
    
    # Reuse fulfillment logic but force resend
    from .stripe_integration import _handle_desktop_checkout_completed
    from .account_store import account_store
    
    purchase = account_store.get_desktop_purchase(session_id)
    if not purchase:
        raise HTTPException(404, "Purchase not found.")
    
    # We clear the email_sent_at to allow _handle_desktop_checkout_completed to run
    account_store.mark_desktop_purchase_email_result(session_id, sent=False, error_message="")
    
    # Mocking the session object for _handle_desktop_checkout_completed
    # It only needs id and metadata or customer_details
    mock_session = {
        "id": session_id,
        "metadata": {
            "delivery_email": purchase["delivery_email"],
            "purchase_type": "desktop"
        }
    }
    
    try:
        _handle_desktop_checkout_completed(mock_session)
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(500, f"Failed to resend email: {exc}")


@app.post("/api/admin/licenses/issue")
async def issue_license(request: Request):
    if not _is_admin(request):
        raise HTTPException(403, "Admin access required.")
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

    windows_link = None
    if resolve_download_target("windows") is not None:
        windows_grant = account_store.issue_desktop_download(session_id, "windows")
        windows_link = f"/desktop/download/{windows_grant['token']}"

    macos_link = None
    if resolve_download_target("macos") is not None:
        macos_grant = account_store.issue_desktop_download(session_id, "macos")
        macos_link = f"/desktop/download/{macos_grant['token']}"

    return {
        "ready": True,
        "pending": False,
        "delivery_email": purchase["delivery_email"],
        "license_key": purchase["license_key"],
        "email_sent": bool(purchase.get("email_sent_at")),
        "email_error": purchase.get("email_error") or "",
        "download_url": windows_link,
        "bundle_download_url": windows_link,
        "macos_download_url": macos_link,
        "macos_bundle_download_url": macos_link,
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


@app.get("/desktop/get/{variant}")
async def desktop_public_download(variant: str):
    """Public download — the app requires an activation key so free download is fine."""
    if variant not in VALID_VARIANTS:
        raise HTTPException(404, "Unknown variant.")
    target = resolve_download_target(variant)
    if target is None:
        raise HTTPException(404, "This installer is not currently available.")
    if target["kind"] == "redirect":
        return RedirectResponse(url=target["url"], status_code=302)
    return FileResponse(target["path"], filename=target["filename"])


DESKTOP_LATEST_VERSION = os.environ.get("IMAGIC_DESKTOP_LATEST_VERSION", "0.1.0").strip()


@app.get("/api/desktop/latest-version")
async def desktop_latest_version():
    """Return the latest desktop app version and download page URL."""
    return {
        "latest_version": DESKTOP_LATEST_VERSION,
        "download_url": f"{BASE_URL}/desktop",
    }


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    """Handle incoming Stripe webhook events."""
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    result = handle_webhook(payload, sig)
    if result.get("status") == "error":
        raise HTTPException(400, result.get("message", "Webhook error"))
    return result


# ---------------------------------------------------------------------------
# Feedback / bug report
# ---------------------------------------------------------------------------

_feedback_limiter = RateLimiter(max_tokens=5, refill_per_second=0.05)  # ~3 per min

@app.post("/api/feedback")
async def submit_feedback(request: Request):
    """Accept a bug report or feature request from the changelog page."""
    client_ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown").split(",")[0].strip()
    if not _feedback_limiter.allow(client_ip):
        raise HTTPException(429, "Too many reports. Please wait a minute.")

    body = await request.json()
    report_type = str(body.get("type", "")).strip()
    email = str(body.get("email", "")).strip()
    version = str(body.get("version", "")).strip()[:20]
    subject = str(body.get("subject", "")).strip()[:200]
    report_body = str(body.get("body", "")).strip()[:5000]

    if report_type not in ("bug", "feature", "other"):
        raise HTTPException(400, "Invalid report type.")
    if not email or "@" not in email:
        raise HTTPException(400, "A valid email is required.")
    if not subject:
        raise HTTPException(400, "Subject is required.")
    if not report_body:
        raise HTTPException(400, "Description is required.")

    # Forward via SMTP (same config as purchase emails)
    import smtplib
    from email.message import EmailMessage as _EmailMessage

    smtp_host = os.environ.get("IMAGIC_SMTP_HOST", "")
    smtp_user = os.environ.get("IMAGIC_SMTP_USERNAME", "")
    smtp_pass = os.environ.get("IMAGIC_SMTP_PASSWORD", "")
    smtp_port = int(os.environ.get("IMAGIC_SMTP_PORT", "587"))
    from_email = os.environ.get("IMAGIC_SMTP_FROM_EMAIL", smtp_user)

    if not all([smtp_host, smtp_user, smtp_pass]):
        raise HTTPException(503, "Email delivery is not configured.")

    type_label = {"bug": "Bug Report", "feature": "Feature Request", "other": "Feedback"}.get(report_type, report_type)
    msg = _EmailMessage()
    msg["Subject"] = f"[imagic {type_label}] {subject}"
    msg["From"] = from_email
    msg["To"] = from_email  # send to ourselves
    msg["Reply-To"] = email
    body_text = (
        f"Type: {type_label}\n"
        f"From: {email}\n"
        f"Version: {version or 'not specified'}\n"
        f"Subject: {subject}\n"
        f"IP: {client_ip}\n"
        f"\n---\n\n{report_body}"
    )
    msg.set_content(body_text)

    try:
        use_ssl = os.environ.get("IMAGIC_SMTP_USE_SSL", "false").lower() == "true"
        if use_ssl:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
            if os.environ.get("IMAGIC_SMTP_USE_TLS", "true").lower() == "true":
                server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)
        server.quit()
    except Exception:
        raise HTTPException(502, "Failed to send report. Please email snap@imagic.ink directly.")

    return {"ok": True}


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
