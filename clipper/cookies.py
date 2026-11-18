"""Automatic YouTube cookie management.

YouTube (via Chrome's device-bound session credentials) *rotates* account
cookies every few hours, so any static cookies.txt goes stale and downloads
start failing with "cookies are no longer valid" / "Sign in to confirm you're
not a bot". To keep the pipeline hands-off, we refresh cookies straight from the
logged-in browser profile on demand and cache them to COOKIES_FILE.

Flow:
  * Before a download, `ensure_fresh_cookies()` refreshes the cache from the
    browser if it's missing or older than COOKIES_MAX_AGE_HOURS.
  * If a download still fails with an auth/bot error, the caller forces a
    refresh (`ensure_fresh_cookies(force=True)`) and retries once.
  * If the browser can't be read (e.g. Chrome's DB is locked/encrypted), we
    keep whatever cached cookies.txt exists so nothing hard-fails.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional, Tuple

from . import config


class _QuietLogger:
    def debug(self, *a, **k):
        pass

    info = debug
    warning = debug

    def error(self, *a, **k):
        pass


def _browser_spec() -> Optional[Tuple[str, Optional[str]]]:
    """Parse COOKIES_FROM_BROWSER (e.g. 'chrome:Profile 2') -> (browser, profile)."""
    spec = (config.COOKIES_FROM_BROWSER or "").strip()
    if not spec:
        return None
    parts = spec.split(":", 1)
    browser = parts[0].strip().lower()
    profile = parts[1].strip() if len(parts) > 1 else None
    return (browser, profile or None)


def _cache_path() -> Optional[Path]:
    """Where the refreshed cookies.txt lives. Falls back to a work-dir default."""
    if config.COOKIES_FILE:
        return Path(config.COOKIES_FILE)
    spec = _browser_spec()
    if spec:
        return config.WORK_DIR / "yt_cookies.txt"
    return None


def _is_fresh(path: Path, max_age_hours: float) -> bool:
    try:
        age = time.time() - path.stat().st_mtime
        return age < max_age_hours * 3600
    except OSError:
        return False


def refresh_from_browser() -> Optional[Path]:
    """Dump current cookies from the configured browser profile to the cache file.

    Returns the cache path on success, or None if the browser couldn't be read.
    """
    spec = _browser_spec()
    dest = _cache_path()
    if not spec or dest is None:
        return None
    browser, profile = spec
    try:
        from yt_dlp.cookies import extract_cookies_from_browser

        jar = extract_cookies_from_browser(browser, profile, _QuietLogger())
        dest.parent.mkdir(parents=True, exist_ok=True)
        jar.save(str(dest), ignore_discard=True, ignore_expires=True)
        n = len(jar)
        if n == 0:
            print(f"[cookies] browser '{browser}' returned 0 cookies (not logged in / locked)")
            return None
        print(f"[cookies] refreshed {n} cookies from {browser}"
              f"{':' + profile if profile else ''} -> {dest.name}")
        return dest
    except Exception as e:  # noqa: BLE001 - browser may be locked/encrypted
        print(f"[cookies] could not read browser cookies ({browser}): {e}")
        return None


def ensure_fresh_cookies(force: bool = False) -> Optional[Path]:
    """Return a usable cookies file, refreshing from the browser when needed.

    - force=True always re-pulls from the browser (used after an auth failure).
    - Otherwise refreshes only if the cache is missing or stale.
    - Always falls back to an existing cache file so we never hard-fail here.
    """
    dest = _cache_path()
    spec = _browser_spec()

    if spec and config.COOKIES_AUTO_REFRESH:
        if force or dest is None or not _is_fresh(dest, config.COOKIES_MAX_AGE_HOURS):
            refreshed = refresh_from_browser()
            if refreshed is not None:
                return refreshed

    # Fall back to whatever cache exists.
    if dest is not None and dest.exists():
        return dest
    return None


_AUTH_ERROR_MARKERS = (
    "cookies are no longer valid",
    "sign in to confirm",
    "confirm you're not a bot",
    "confirm you’re not a bot",
    "this video is available to this channel's members",
    "http error 429",
    "account cookies",
    "please sign in",
    "login required",
)


def is_auth_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in _AUTH_ERROR_MARKERS)
