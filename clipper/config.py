"""Central config: paths and environment."""
from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Point ffmpeg's OpenSSL (used when it fetches googlevideo URLs for section/live
# downloads) at a valid CA bundle, or it fails with "certificate verify failed".
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
except Exception:
    pass

# Working/output directories
WORK_DIR = Path(os.getenv("CLIP_WORK_DIR", ROOT / "work"))
OUTPUT_DIR = Path(os.getenv("CLIP_OUTPUT_DIR", ROOT / "output"))
CRED_DIR = ROOT / "credentials"

for d in (WORK_DIR, OUTPUT_DIR, CRED_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Google / YouTube
CLIENT_SECRETS = CRED_DIR / "credentials.json"
TOKEN_FILE = CRED_DIR / "token.json"
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
OAUTH_PORT = int(os.getenv("OAUTH_PORT", "8765"))

# Gemini (optional, for AI titles + highlight detection)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
# The google-genai SDK silently prefers a GOOGLE_API_KEY env var over an explicitly
# passed key. A stale GOOGLE_API_KEY in the shell can make calls hang/auth-fail, so
# align both env vars to our chosen key (or clear a stray one if we have none).
if GEMINI_API_KEY:
    os.environ["GOOGLE_API_KEY"] = GEMINI_API_KEY
    os.environ["GEMINI_API_KEY"] = GEMINI_API_KEY
else:
    os.environ.pop("GOOGLE_API_KEY", None)
# Wall-clock cap on each Gemini call; on timeout we fall back to the heuristic.
GEMINI_TIMEOUT_S = float(os.getenv("GEMINI_TIMEOUT_S", "25"))

# Title style: crisp and eye-catching — aim within this character band.
TITLE_MIN_CHARS = int(os.getenv("TITLE_MIN_CHARS", "30"))
TITLE_MAX_CHARS = int(os.getenv("TITLE_MAX_CHARS", "62"))

# yt-dlp auth to get past YouTube bot detection ("Sign in to confirm you're not a bot").
# Set COOKIES_FROM_BROWSER to e.g. "chrome:Profile 2" (the browser profile logged
# into your YouTube account). Cookies are pulled fresh from that profile and cached
# to COOKIES_FILE, so YouTube's periodic cookie rotation is handled automatically.
# COOKIES_FILE alone (no browser) also works but goes stale and must be re-exported.
COOKIES_FILE = os.getenv("COOKIES_FILE", "").strip()
COOKIES_FROM_BROWSER = os.getenv("COOKIES_FROM_BROWSER", "").strip()
# Auto-refresh cookies from the browser profile before downloading (and retry on
# auth failure). Set to 0 to use only a static COOKIES_FILE.
COOKIES_AUTO_REFRESH = os.getenv("COOKIES_AUTO_REFRESH", "1").strip().lower() not in ("0", "false", "no", "")
# How old the cached cookies may get before we re-pull from the browser.
COOKIES_MAX_AGE_HOURS = float(os.getenv("COOKIES_MAX_AGE_HOURS", "6"))

# GPU: use NVIDIA GPU (NVENC encode + CUDA Whisper) when available. Set USE_GPU=0 to force CPU.
USE_GPU = os.getenv("USE_GPU", "1").strip().lower() not in ("0", "false", "no", "")

# Whisper (captions). base is a good speed/quality tradeoff.
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto")  # auto|cpu|cuda
WHISPER_COMPUTE = os.getenv("WHISPER_COMPUTE", "int8")          # CPU compute type
# CUDA compute type. Pascal (GTX 10xx) lacks efficient float16 -> int8 is the safe/fast choice.
WHISPER_COMPUTE_CUDA = os.getenv("WHISPER_COMPUTE_CUDA", "int8")

# Output video spec for Shorts
VERT_W, VERT_H = 1080, 1920
