"""End-to-end orchestration tying download -> transcribe -> highlights -> edit -> upload."""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import config, download, transcribe, highlights, edit


@dataclass
class ClipResult:
    path: Path
    title: str
    start: float
    end: float
    reason: str = ""
    transcript: str = ""
    description: str = ""
    tags: List[str] = field(default_factory=list)


def _safe(name: str) -> str:
    """Filesystem/ffmpeg-filtergraph-safe slug: alphanumerics + underscores only."""
    kept = "".join(c if (c.isalnum() or c in " -_") else "" for c in name)
    slug = "_".join(kept.split())  # collapse whitespace to underscores
    return slug[:60].strip("_-") or "clip"


def process_clip(
    source_video: Path,
    out_name: str,
    captions: bool = True,
    fit: str = "crop",
    seg_words: Optional[List[dict]] = None,
) -> Path:
    """Format one already-trimmed source into a final vertical clip."""
    out_path = config.OUTPUT_DIR / f"{_safe(out_name)}.mp4"
    words = None
    if captions:
        if seg_words is None:
            audio_segs = transcribe.transcribe(source_video)
            seg_words = transcribe.slice_words(audio_segs, 0, 10 ** 9)
        words = seg_words
    edit.build_clip(source_video, out_path, fit=fit, words=words)
    return out_path


def clip_range(
    url: str,
    start,
    end,
    captions: bool = True,
    fit: str = "crop",
    title: Optional[str] = None,
) -> ClipResult:
    """Clip a single known [start,end] range from a VOD."""
    meta = download.probe(url)
    src = download.download_section(url, start, end, max_height=1080)
    seg_words = None
    transcript = ""
    if captions:
        segs = transcribe.transcribe(src)
        seg_words = transcribe.slice_words(segs, 0, 10 ** 9)
        transcript = " ".join(s["text"] for s in segs)
    md = highlights.build_metadata(
        transcript, meta.get("title", ""), meta.get("uploader", ""), title_hint=title or ""
    )
    out = process_clip(src, md["title"], captions=captions, fit=fit, seg_words=seg_words)
    return ClipResult(out, md["title"], 0, 0, transcript=transcript,
                      description=md["description"], tags=md["tags"])


def auto_clips(
    url: str,
    count: int = 3,
    clip_len: int = 45,
    captions: bool = True,
    fit: str = "crop",
) -> List[ClipResult]:
    """Transcribe full video, pick highlights, cut + format each."""
    meta = download.probe(url)
    print(f"[auto] '{meta.get('title')}' ({meta.get('duration')}s) — downloading audio…")
    audio = download.download_audio(url)
    print("[auto] transcribing (first run downloads the Whisper model)…")
    segments = transcribe.transcribe(audio)
    print(f"[auto] transcript: {len(segments)} segments. Selecting {count} highlights…")
    picks = highlights.find_highlights(segments, meta.get("title", ""), count, clip_len)
    results = []
    for i, p in enumerate(picks, 1):
        print(f"[auto] clip {i}/{len(picks)}: [{p['start']:.1f}-{p['end']:.1f}] {p['title']}")
        src = download.download_section(url, p["start"], p["end"], max_height=1080)
        seg_words = transcribe.slice_words(segments, p["start"], p["end"]) if captions else None
        clip_txt = " ".join(
            s["text"] for s in segments if s["start"] >= p["start"] and s["end"] <= p["end"]
        )
        md = highlights.build_metadata(
            clip_txt, meta.get("title", ""), meta.get("uploader", ""), title_hint=p["title"]
        )
        out = process_clip(src, md["title"], captions=captions, fit=fit, seg_words=seg_words)
        results.append(
            ClipResult(out, md["title"], p["start"], p["end"], p.get("reason", ""),
                       transcript=clip_txt, description=md["description"], tags=md["tags"])
        )
    return results


def clip_live(url: str, duration: int, captions: bool = True, fit: str = "crop") -> ClipResult:
    meta = download.probe(url)
    print(f"[live] capturing {duration}s from live edge…")
    src = download.capture_live(url, duration)
    seg_words, transcript = None, ""
    if captions:
        segs = transcribe.transcribe(src)
        seg_words = transcribe.slice_words(segs, 0, 10 ** 9)
        transcript = " ".join(s["text"] for s in segs)
    md = highlights.build_metadata(transcript, meta.get("title", ""), meta.get("uploader", ""))
    out = process_clip(src, md["title"], captions=captions, fit=fit, seg_words=seg_words)
    return ClipResult(out, md["title"], 0, duration, transcript=transcript,
                      description=md["description"], tags=md["tags"])
