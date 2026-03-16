"""Blog post data and helper functions for the imagic blog."""
from __future__ import annotations
from datetime import date, datetime

# ---------------------------------------------------------------------------
# Post data — imported from separate batch files
# ---------------------------------------------------------------------------

try:
    from .blog_posts_batch1 import POSTS_BATCH_1
except ImportError:
    POSTS_BATCH_1 = []

try:
    from .blog_posts_batch2 import POSTS_BATCH_2
except ImportError:
    POSTS_BATCH_2 = []

ALL_POSTS: list[dict] = POSTS_BATCH_1 + POSTS_BATCH_2

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TODAY = date.today().isoformat()


def get_published_posts() -> list[dict]:
    """Return all posts with date <= today, sorted newest first."""
    published = [p for p in ALL_POSTS if p.get("date", "9999") <= _TODAY]
    return sorted(published, key=lambda p: p["date"], reverse=True)


def get_post_by_slug(slug: str) -> dict | None:
    for post in ALL_POSTS:
        if post.get("slug") == slug and post.get("date", "9999") <= _TODAY:
            return dict(post)
    return None


def get_related_posts(slug: str, limit: int = 3) -> list[dict]:
    """Return posts in the same category, excluding the current post."""
    current = get_post_by_slug(slug)
    if not current:
        return []
    cat = current.get("category", "")
    related = [
        p for p in get_published_posts()
        if p["slug"] != slug and p.get("category") == cat
    ]
    return related[:limit]
