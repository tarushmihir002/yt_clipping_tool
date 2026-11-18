# Creator Clipping Tool

Clip YouTube **VODs** and **livestreams** into vertical (9:16) Shorts with burned-in
captions and AI-generated titles, then upload them to your channel.

## Pipeline
`download (yt-dlp) → transcribe (faster-whisper) → highlight pick + title (Gemini / heuristic) → format (ffmpeg: 9:16 + captions) → upload (YouTube Data API v3)`

## Requirements
- Python 3.10+, `ffmpeg` on PATH
- `pip install -r requirements.txt`
- A Google Cloud OAuth client (`credentials/credentials.json`) — Desktop app type, YouTube Data API v3 enabled
- Optional: `GEMINI_API_KEY` in `.env` for AI titles & highlight detection (free tier)

## Setup
1. `pip install -r requirements.txt`
2. `copy .env.example .env` and fill in `GEMINI_API_KEY` (optional).
3. Put your OAuth client secrets at `credentials/credentials.json`.
4. `python main.py auth`  → approve in the browser once (token saved to `credentials/token.json`).

## Usage
```bash
# Auto-detect the 3 best moments from a VOD and make Shorts (no upload)
python main.py auto --url "https://youtu.be/VIDEO_ID" --count 3 --len 45

# Clip a specific range and upload as private
python main.py clip --url "https://youtu.be/VIDEO_ID" --start 00:12:30 --end 00:13:15 --upload --privacy private

# Capture 60s from an ongoing livestream
python main.py live --url "https://youtu.be/LIVE_ID" --duration 60 --upload

# Upload a file you already made
python main.py upload --file output/clip.mp4 --title "My clip" --privacy private
```

### Options
- `--fit crop` (default, fills frame) or `--fit blur` (whole frame on blurred background)
- `--no-captions` to skip burned captions
- `--privacy private|unlisted|public`

## Notes
- Uploads from an **unverified** OAuth app are forced to **private** by YouTube until the app is verified.
- Only clip content you own or have permission to use. Reused content can block monetization even when reuploads are permitted.

## Troubleshooting

### "Sign in to confirm you're not a bot" / HTTP 429 on download
YouTube blocks automated downloads that have no browser session. Give yt-dlp your
logged-in cookies via `.env` (either option works):

```
# Point to an exported cookies.txt (use a "Get cookies.txt" browser extension), OR
COOKIES_FILE=C:\path\to\cookies.txt
# read directly from the Chrome profile logged into your account:
COOKIES_FROM_BROWSER=chrome:Profile 2
```

Find your Chrome profile folder name under
`%LOCALAPPDATA%\Google\Chrome\User Data\` (e.g. `Default`, `Profile 2`).
Then re-run any `clip`/`auto`/`live` command.

### First run is slow
The first captioning run downloads the Whisper model (~140 MB for `base`). Later runs are fast.
