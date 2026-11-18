"""ffmpeg-based editing: trim, vertical 9:16 reframe, burned captions (ASS)."""
from __future__ import annotations
import subprocess
from pathlib import Path
from typing import List, Dict, Optional

from . import config


_ENCODER_CACHE = None
_SUBS_FILTER_CACHE = None


def _has_subtitles_filter() -> bool:
    """True only if this ffmpeg was built with libass (the `subtitles` filter)."""
    global _SUBS_FILTER_CACHE
    if _SUBS_FILTER_CACHE is None:
        try:
            out = subprocess.run(
                ["ffmpeg", "-hide_banner", "-filters"],
                capture_output=True, text=True, timeout=15,
            )
            _SUBS_FILTER_CACHE = any(
                line.split()[1:2] == ["subtitles"]
                for line in out.stdout.splitlines() if line.strip()
            )
        except Exception:
            _SUBS_FILTER_CACHE = False
    return _SUBS_FILTER_CACHE


def _nvenc_available() -> bool:
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=15,
        )
        return "h264_nvenc" in out.stdout
    except Exception:
        return False


def _video_encoder() -> list:
    """ffmpeg video-encoder args. Uses NVIDIA NVENC (GPU) when available, else libx264 (CPU)."""
    global _ENCODER_CACHE
    if _ENCODER_CACHE is None:
        if config.USE_GPU and _nvenc_available():
            _ENCODER_CACHE = ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "23", "-b:v", "0"]
            print("[edit] video encode: h264_nvenc (GPU)")
        else:
            _ENCODER_CACHE = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20"]
            print("[edit] video encode: libx264 (CPU)")
    return list(_ENCODER_CACHE)


def _ass_time(t: float) -> str:
    if t < 0:
        t = 0.0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    if cs == 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _group_words(words: List[Dict], max_words=4, max_dur=1.8) -> List[Dict]:
    groups, cur = [], []
    for w in words:
        if not w["word"]:
            continue
        if not cur:
            cur = [w]
            continue
        start = cur[0]["start"]
        if len(cur) >= max_words or (w["end"] - start) > max_dur:
            groups.append(cur)
            cur = [w]
        else:
            cur.append(w)
    if cur:
        groups.append(cur)
    out = []
    for g in groups:
        out.append(
            {
                "start": g[0]["start"],
                "end": g[-1]["end"],
                "text": " ".join(x["word"] for x in g).strip(),
            }
        )
    return out


def write_ass(words: List[Dict], ass_path: Path, font_size: int = 76) -> Path:
    """Write a styled ASS caption file (1080x1920 canvas)."""
    groups = _group_words(words)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {config.VERT_W}
PlayResY: {config.VERT_H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Pop,Arial,{font_size},&H00FFFFFF,&H00000000,&H64000000,-1,0,1,5,2,2,80,80,320,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    for g in groups:
        txt = g["text"].replace("\n", " ").replace("{", "(").replace("}", ")")
        lines.append(
            f"Dialogue: 0,{_ass_time(g['start'])},{_ass_time(g['end'])},Pop,,0,0,0,,{txt.upper()}"
        )
    ass_path.write_text("\n".join(lines), encoding="utf-8")
    return ass_path


def _drawtext_chain(groups: List[Dict], cwd: Path, fontsize: int = 64) -> List[str]:
    """Build a `drawtext`-based caption filterchain (works without libass).

    Writes each caption group's text to a sidecar file (so no text escaping is
    needed) and returns the list of created sidecar paths for later cleanup.
    The filter string is returned via the first element convention: see below.
    """
    parts, sidecars = [], []
    for i, g in enumerate(groups):
        txt = (g["text"] or "").strip().upper()
        if not txt:
            continue
        cap = cwd / f"cap_{i}.txt"
        cap.write_text(txt, encoding="utf-8")
        sidecars.append(cap)
        s, e = max(0.0, g["start"]), max(0.0, g["end"])
        parts.append(
            f"drawtext=font=Arial:textfile={cap.name}:fontsize={fontsize}:"
            f"fontcolor=white:borderw=6:bordercolor=black:box=1:boxcolor=black@0.35:"
            f"boxborderw=14:x=(w-text_w)/2:y=h-420:"
            f"enable=between(t\\,{s:.2f}\\,{e:.2f})"
        )
    return [",".join(parts)] + [str(p) for p in sidecars]


def build_clip(
    in_path: Path,
    out_path: Path,
    fit: str = "crop",
    words: Optional[List[Dict]] = None,
) -> Path:
    """Produce the final vertical clip. `fit`: 'crop' (center-crop) or 'blur' (fit on blurred bg).

    Captions are burned with libass (`subtitles` filter) when the ffmpeg build
    supports it, otherwise with `drawtext` (always available via libfreetype).
    Runs ffmpeg with cwd=output dir so filenames need no Windows path escaping.
    """
    W, H = config.VERT_W, config.VERT_H
    cwd = out_path.parent
    venc = _video_encoder()

    # Pass 1: geometry -> vertical (no captions yet)
    if fit == "blur":
        geom = ["-filter_complex", (
            f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=40:2[bg];"
            f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
        ), "-map", "[v]", "-map", "0:a?"]
    else:  # crop (center-crop to fill 9:16)
        geom = ["-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"]

    has_caps = bool(words)
    if not has_caps:
        cmd = ["ffmpeg", "-y", "-i", str(in_path.resolve()), *geom, *venc,
               "-c:a", "aac", "-b:a", "160k", "-r", "30", "-pix_fmt", "yuv420p", out_path.name]
        subprocess.run(cmd, check=True, cwd=str(cwd))
        return out_path

    # Captions: geometry first, then burn text in a dedicated second pass.
    tmp = out_path.with_name(out_path.stem + "_geom.mp4")
    cmd1 = ["ffmpeg", "-y", "-i", str(in_path.resolve()), *geom, *venc,
            "-c:a", "aac", "-b:a", "160k", "-r", "30", "-pix_fmt", "yuv420p", tmp.name]
    subprocess.run(cmd1, check=True, cwd=str(cwd))

    cleanup: List[str] = []
    if _has_subtitles_filter():
        ass_file = write_ass(words, out_path.with_suffix(".ass"))
        vf = f"subtitles={ass_file.name}"
        print("[edit] captions: libass (subtitles)")
    else:
        groups = _group_words(words, max_words=3)
        chain = _drawtext_chain(groups, cwd)
        vf = chain[0]
        cleanup = chain[1:]
        print(f"[edit] captions: drawtext ({len(groups)} cues)")

    if not vf:  # nothing to burn -> just use the geometry pass
        tmp.replace(out_path)
        return out_path

    cmd2 = ["ffmpeg", "-y", "-i", tmp.name, "-vf", vf,
            *venc, "-c:a", "copy", "-pix_fmt", "yuv420p", out_path.name]
    subprocess.run(cmd2, check=True, cwd=str(cwd))
    for f in cleanup:
        try:
            Path(f).unlink()
        except OSError:
            pass
    try:
        tmp.unlink()
    except OSError:
        pass
    return out_path
