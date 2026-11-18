"""Download YouTube VODs (full / sectioned / audio-only) and capture livestream clips."""
from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Optional

from yt_dlp import YoutubeDL
from yt_dlp.utils import download_range_func, DownloadError

from . import config
from . import cookies


def _auth_opts(force_refresh: bool = False) -> dict:
    """Cookie options so YouTube doesn't block downloads as a bot.

    Pulls fresh cookies from the configured browser profile (auto-refresh) and
    uses the cached cookies.txt. force_refresh re-pulls even if the cache is fresh.
    """
    opts: dict = {}
    path = cookies.ensure_fresh_cookies(force=force_refresh)
    if path is not None:
        opts["cookiefile"] = str(path)
    return opts


def _extract(base_opts: dict, url: str, download: bool):
    """Run yt-dlp, trying WITHOUT cookies first (works for public videos).

    Only if YouTube blocks it with a bot/auth error do we fall back to cookies:
    a fresh pull from the browser if possible, else the cached cookies.txt.
    """
    # Tier 1: no cookies at all.
    try:
        with YoutubeDL(dict(base_opts)) as ydl:
            return ydl.extract_info(url, download=download)
    except DownloadError as e:
        if not cookies.is_auth_error(e):
            raise
        print("[download] YouTube asked for sign-in — falling back to cookies...")

    # Tier 2: cookies (browser refresh -> cached file).
    auth = _auth_opts(force_refresh=True)
    if not auth:
        raise DownloadError(
            "YouTube blocked this download as a bot and no usable cookies are available. "
            "Close Chrome and retry, or set COOKIES_FROM_BROWSER=firefox after logging into "
            "YouTube in Firefox, or re-export cookies.txt to COOKIES_FILE."
        )
    opts = dict(base_opts)
    opts.update(auth)
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=download)


def _hhmmss_to_s(ts) -> float:
    if isinstance(ts, (int, float)):
        return float(ts)
    parts = [float(p) for p in str(ts).split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


def probe(url: str) -> dict:
    """Return metadata without downloading (title, duration, is_live, id)."""
    opts = {"quiet": True, "skip_download": True, "noplaylist": True}
    info = _extract(opts, url, download=False)
    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "duration": info.get("duration"),
        "is_live": info.get("is_live", False),
        "uploader": info.get("uploader"),
        "webpage_url": info.get("webpage_url", url),
    }


def download_audio(url: str, out_dir: Path = config.WORK_DIR) -> Path:
    """Download audio only (fast, small) — used for transcription."""
    out_tmpl = str(out_dir / "%(id)s.audio.%(ext)s")
    opts = {
        "format": "bestaudio/best",
        "outtmpl": out_tmpl,
        "quiet": True,
        "noplaylist": True,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "m4a", "preferredquality": "128"}
        ],
    }
    info = _extract(opts, url, download=True)
    return out_dir / f"{info['id']}.audio.m4a"


def download_full(url: str, out_dir: Path = config.WORK_DIR, max_height: int = 1080) -> Path:
    out_tmpl = str(out_dir / "%(id)s.full.%(ext)s")
    opts = {
        "format": f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/best",
        "outtmpl": out_tmpl,
        "merge_output_format": "mp4",
        "quiet": True,
        "noplaylist": True,
    }
    info = _extract(opts, url, download=True)
    return out_dir / f"{info['id']}.full.mp4"


def download_section(
    url: str, start, end, out_dir: Path = config.WORK_DIR, max_height: int = 1080
) -> Path:
    """Download ONLY the given [start, end] section (seconds or HH:MM:SS)."""
    s, e = _hhmmss_to_s(start), _hhmmss_to_s(end)
    tag = f"{int(s)}_{int(e)}"
    out_tmpl = str(out_dir / f"%(id)s.sec_{tag}.%(ext)s")
    opts = {
        "format": f"bestvideo[height<={max_height}]+bestaudio/best[height<={max_height}]/best",
        "outtmpl": out_tmpl,
        "merge_output_format": "mp4",
        "quiet": True,
        "noplaylist": True,
        "download_ranges": download_range_func(None, [(s, e)]),
        "force_keyframes_at_cuts": True,
    }
    info = _extract(opts, url, download=True)
    return out_dir / f"{info['id']}.sec_{tag}.mp4"


def capture_live(url: str, duration: int, out_dir: Path = config.WORK_DIR) -> Path:
    """Capture `duration` seconds from the LIVE edge of an ongoing livestream.

    Resolves the live stream URL via yt-dlp, then grabs N seconds with ffmpeg.
    """
    opts = {"quiet": True, "noplaylist": True, "format": "best"}
    info = _extract(opts, url, download=False)
    if not info.get("is_live"):
        raise RuntimeError("This URL is not currently live. Use the 'clip'/'auto' command for VODs.")
    stream_url = info["url"]
    out = out_dir / f"{info['id']}.live.mp4"
    cmd = [
        "ffmpeg", "-y", "-i", stream_url,
        "-t", str(duration),
        "-c", "copy", "-bsf:a", "aac_adtstoasc",
        str(out),
    ]
    subprocess.run(cmd, check=True)
    return out
