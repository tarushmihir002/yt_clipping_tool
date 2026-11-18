"""Pick highlight moments + generate titles. Uses Gemini if a key is set, else heuristic."""
from __future__ import annotations
import json
import re
import threading
from typing import List, Dict, Any

from . import config


_SMALL_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into",
    "nor", "of", "on", "onto", "or", "the", "to", "up", "vs", "via", "with",
}
_LEADING_FILLER = {
    "so", "and", "but", "um", "uh", "oh", "okay", "ok", "well", "like", "yeah",
    "just", "now", "right", "look", "hey", "you", "know",
}


def _clean_title(s: str, video_title: str = "") -> str:
    """Normalize any title candidate: strip junk, cap length on a word boundary."""
    import re as _re
    s = (s or "").strip().strip('"“”\'')
    s = _re.sub(r"#\w+", "", s)                 # drop hashtags
    s = _re.sub(r"\s+", " ", s).strip()
    # drop emoji / non-text symbols but keep common punctuation & $ % & -
    s = "".join(ch for ch in s if ch.isalnum() or ch in " $%&'\"!?.,:-—()").strip()
    s = s.strip(" -—:,.")
    if not s:
        return (video_title or "Clip").strip()[: config.TITLE_MAX_CHARS]
    if len(s) > config.TITLE_MAX_CHARS:
        cut = s[: config.TITLE_MAX_CHARS].rsplit(" ", 1)[0].strip(" -—:,.")
        s = cut or s[: config.TITLE_MAX_CHARS]
    return s


def _title_case(s: str) -> str:
    words = s.split()
    out = []
    for i, w in enumerate(words):
        low = w.lower()
        if any(c.isdigit() or c == "$" for c in w):   # keep "$1,000", "100K"
            out.append(w)
        elif i != 0 and i != len(words) - 1 and low in _SMALL_WORDS:
            out.append(low)
        else:
            out.append(w[:1].upper() + w[1:] if w else w)
    return " ".join(out)


def _crisp_heuristic(text: str, video_title: str = "") -> str:
    """No-AI title: strongest short clause from the transcript, cleanly cased."""
    import re as _re
    text = (text or "").strip()
    if not text:
        return _clean_title(video_title, video_title)
    # first sentence-ish clause
    clause = _re.split(r"(?<=[.!?])\s+", text)[0].strip()
    toks = clause.split()
    # trim leading filler words ("so", "okay", "oh", ...)
    while toks and _re.sub(r"[^a-z]", "", toks[0].lower()) in _LEADING_FILLER:
        toks.pop(0)
    # grow/limit toward the target band (~6-10 words)
    picked, n = [], 0
    for t in toks:
        picked.append(t)
        n += len(t) + 1
        if n >= config.TITLE_MAX_CHARS or len(picked) >= 10:
            break
    candidate = " ".join(picked) if picked else clause
    # A short/weak opening line makes a poor title — the source video's own
    # headline is already catchy and human-written, so prefer it in that case.
    if len(candidate) < config.TITLE_MIN_CHARS and video_title:
        return _clean_title(video_title, video_title)
    return _clean_title(_title_case(candidate), video_title)


def _transcript_block(segments: List[Dict]) -> str:
    lines = []
    for s in segments:
        lines.append(f"[{s['start']:.1f}-{s['end']:.1f}] {s['text']}")
    return "\n".join(lines)


def _gemini_client():
    from google import genai
    from google.genai import types

    # SDK-level timeout (ms); belt-and-suspenders with the thread timeout below.
    http = types.HttpOptions(timeout=int(config.GEMINI_TIMEOUT_S * 1000))
    return genai.Client(api_key=config.GEMINI_API_KEY, http_options=http)


def _generate_text(prompt: str) -> str:
    """Call Gemini with a hard wall-clock timeout. Raises on timeout/any error.

    Runs the call on a daemon thread so that even a fully stuck SDK call can
    never block the pipeline — we abandon the thread and move on.
    """
    box: dict = {}

    def _call():
        try:
            client = _gemini_client()
            resp = client.models.generate_content(model=config.GEMINI_MODEL, contents=prompt)
            box["text"] = resp.text
        except Exception as e:  # noqa
            box["err"] = e

    t = threading.Thread(target=_call, daemon=True)
    t.start()
    t.join(timeout=config.GEMINI_TIMEOUT_S)
    if t.is_alive():
        raise TimeoutError(f"Gemini call exceeded {config.GEMINI_TIMEOUT_S}s")
    if "err" in box:
        raise box["err"]
    return box.get("text") or ""


def _gemini_highlights(segments, video_title, count, clip_len) -> List[Dict[str, Any]]:
    transcript = _transcript_block(segments)
    prompt = f"""You are a short-form video editor. Below is a timestamped transcript of a video titled "{video_title}".

Pick the {count} best standalone moments to turn into vertical Shorts. Each clip should be roughly {clip_len} seconds long, start and end on natural sentence boundaries, and be self-contained (hook in the first 2 seconds, satisfying payoff).

Return ONLY valid JSON, no markdown fences, as a list of objects:
[{{"start": <seconds float>, "end": <seconds float>, "title": "<crisp eye-catching title, {config.TITLE_MIN_CHARS}-{config.TITLE_MAX_CHARS} chars, a curiosity hook, no hashtags/emojis/quotes>", "reason": "<why this clips well>"}}]

Transcript:
{transcript}
"""
    text = _generate_text(prompt).strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
    data = json.loads(text)
    clips = []
    for d in data[:count]:
        clips.append(
            {
                "start": float(d["start"]),
                "end": float(d["end"]),
                "title": _clean_title(str(d.get("title", "")), video_title) or "Clip",
                "reason": str(d.get("reason", "")).strip(),
            }
        )
    return clips


def _heuristic_highlights(segments, video_title, count, clip_len) -> List[Dict[str, Any]]:
    """No AI: spread `count` clips of ~clip_len across the transcript, snapped to segments."""
    if not segments:
        return []
    total = segments[-1]["end"]
    clips = []
    for i in range(count):
        center = total * (i + 1) / (count + 1)
        start = max(0.0, center - clip_len / 2)
        end = min(total, start + clip_len)
        # snap start to the nearest segment boundary
        start = min(segments, key=lambda s: abs(s["start"] - start))["start"]
        end = min(total, start + clip_len)
        text = " ".join(s["text"] for s in segments if s["start"] >= start and s["end"] <= end)
        title = _crisp_heuristic(text, video_title) or f"{video_title} — clip {i+1}"
        clips.append({"start": start, "end": end, "title": title, "reason": "even spread (no AI key)"})
    return clips


def find_highlights(segments, video_title, count=3, clip_len=45) -> List[Dict[str, Any]]:
    if config.GEMINI_API_KEY:
        try:
            return _gemini_highlights(segments, video_title, count, clip_len)
        except Exception as e:  # noqa
            print(f"[highlights] Gemini failed ({e}); falling back to heuristic.")
    return _heuristic_highlights(segments, video_title, count, clip_len)


def suggest_title(text: str, video_title: str = "") -> str:
    if config.GEMINI_API_KEY and text:
        try:
            out = _generate_text(
                "You write titles for YouTube Shorts. Give ONE crisp, eye-catching title "
                f"of {config.TITLE_MIN_CHARS}-{config.TITLE_MAX_CHARS} characters "
                "(roughly 5-9 words). Make it a curiosity hook that captures the single most "
                "striking moment. No quotes, no hashtags, no emojis, no clickbait ALL-CAPS. "
                "Return only the title.\n\n"
                f"Source video: {video_title}\nClip transcript:\n{text[:1500]}"
            )
            cleaned = _clean_title(out, video_title)
            if cleaned:
                return cleaned
        except Exception as e:  # noqa
            print(f"[title] Gemini failed ({e}); using crisp heuristic fallback.")
    return _crisp_heuristic(text, video_title)


# --- Full metadata (title + hashtags + description + search tags) -------------

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "so", "to", "of", "in",
    "on", "for", "with", "at", "by", "from", "up", "out", "is", "are", "was",
    "were", "be", "been", "it", "its", "this", "that", "these", "those", "i",
    "you", "he", "she", "we", "they", "me", "him", "her", "us", "them", "my",
    "your", "our", "their", "do", "did", "does", "not", "no", "yes", "oh", "okay",
    "ok", "um", "uh", "like", "just", "got", "get", "going", "gonna", "really",
    "very", "will", "would", "can", "could", "what", "when", "where", "who", "how",
    "guys", "yeah", "well", "now", "right", "one", "two", "all", "here", "there",
}
_ALWAYS_TAGS = ["Shorts", "viral", "trending", "fyp"]
# weak words that make poor hashtags even when capitalized in a title
_WEAK_TAG_WORDS = _STOPWORDS | {
    "beat", "win", "won", "goes", "get", "got", "make", "made", "new", "best",
    "big", "day", "time", "way", "guy", "guys", "thing", "things", "video",
}
# whole-word transcript/title keyword -> topic hashtag
_TOPIC_RULES = [
    (("win", "won", "prize", "cash", "money", "dollar", "dollars", "giveaway"), "Giveaway"),
    (("challenge", "dare", "versus", "compete", "contest", "beat"), "Challenge"),
    (("funny", "laugh", "joke", "hilarious", "comedy"), "Funny"),
    (("game", "play", "gaming", "player", "match"), "Gaming"),
    (("food", "burger", "pizza", "taste", "cook", "cooking"), "Food"),
    (("stunt", "jump", "crazy", "insane", "extreme"), "Insane"),
]


def _camel(s: str) -> str:
    return "".join(w[:1].upper() + w[1:] for w in re.sub(r"[^A-Za-z0-9]+", " ", s).split())


def _hashtags_heuristic(video_title: str, transcript: str, uploader: str, limit: int = 9) -> List[str]:
    tags: List[str] = []

    def add(t: str):
        t = re.sub(r"[^A-Za-z0-9]", "", t or "")
        if t and len(t) >= 3 and t.lower() not in {x.lower() for x in tags}:
            tags.append(t)

    add("Shorts")
    if uploader:
        add(_camel(uploader))                       # e.g. MrBeast
    words = set(re.findall(r"[a-z0-9]+", f"{video_title} {transcript}".lower()))
    has_money = "$" in f"{video_title} {transcript}"
    for keys, tag in _TOPIC_RULES:                  # whole-word topical tags
        if (has_money and tag == "Giveaway") or any(k in words for k in keys):
            add(tag)
    for w in re.findall(r"[A-Z][a-zA-Z]{3,}", video_title or ""):  # strong proper nouns in title
        if w.lower() not in _WEAK_TAG_WORDS:
            add(w)
    for t in _ALWAYS_TAGS:                           # reach tags
        add(t)
    return tags[:limit]


def _keywords_heuristic(video_title: str, transcript: str, uploader: str, limit: int = 12) -> List[str]:
    kws: List[str] = []

    def add(k: str):
        k = k.strip().lower()
        if k and k not in kws:
            kws.append(k)

    if uploader:
        add(uploader)
    for w in re.findall(r"[A-Za-z][A-Za-z']{2,}", f"{video_title} {transcript}"):
        if w.lower() not in _STOPWORDS:
            add(w.lower())
        if len(kws) >= limit:
            break
    for base in ("shorts", "youtube shorts", "viral video"):
        add(base)
    return kws[:limit]


def _hook_sentence(transcript: str, title: str) -> str:
    t = (transcript or "").strip()
    if not t:
        return title
    sents = re.split(r"(?<=[.!?])\s+", t)
    # strip leading filler from the opening sentence
    toks = sents[0].split()
    while toks and re.sub(r"[^a-z]", "", toks[0].lower()) in _LEADING_FILLER:
        toks.pop(0)
    hook = (" ".join(toks).strip() or sents[0]).strip(" ,")
    # if the opening line is too short to stand alone, add the next sentence
    if len(hook) < 30 and len(sents) > 1:
        hook = f"{hook} {sents[1]}".strip()
    hook = re.sub(r"\s+", " ", hook)
    if len(hook) > 200:
        hook = hook[:200].rsplit(" ", 1)[0].strip() + "…"
    return (hook[:1].upper() + hook[1:]) if hook else title


def _title_with_hashtags(title: str, hashtags: List[str], max_len: int = 100) -> str:
    title = _clean_title(title).rstrip(" -—:,.")
    picked: List[str] = []
    for h in hashtags:                                # prefer Shorts + a brand/topic tag
        tag = "#" + h
        if len(f"{title} {' '.join(picked + [tag])}") <= max_len and len(picked) < 3:
            picked.append(tag)
    return (f"{title} {' '.join(picked)}".strip())[:max_len] if picked else title[:max_len]


def _assemble_description(hook: str, video_title: str, uploader: str, hashtags: List[str]) -> str:
    blocks: List[str] = []
    if hook:
        blocks.append(hook)
    credit = ""
    if video_title:
        credit = f'🎬 Clipped from "{video_title}"'
        if uploader:
            credit += f" by {uploader}"
    if credit:
        blocks.append(credit)
    blocks.append("👍 Like & subscribe for more clips every day!")
    if hashtags:
        blocks.append(" ".join("#" + h for h in hashtags))
    return "\n\n".join(blocks)[:4900]


def build_metadata(transcript: str, video_title: str = "", uploader: str = "",
                   title_hint: str = "") -> Dict[str, Any]:
    """Produce professional upload metadata: {title, description, hashtags, tags}.

    Uses Gemini when a valid key is set; otherwise a content-aware heuristic.
    """
    hashtags: List[str] = []
    tags: List[str] = []
    title = (title_hint or "").strip()
    hook = ""

    if config.GEMINI_API_KEY and transcript:
        try:
            raw = _generate_text(
                "You are a YouTube Shorts growth editor. From the clip transcript below, "
                "return ONLY JSON (no markdown) with keys:\n"
                f'  "title": crisp eye-catching title {config.TITLE_MIN_CHARS}-{config.TITLE_MAX_CHARS} chars, a curiosity hook, no hashtags/emojis/quotes;\n'
                '  "description": 1-2 engaging sentences about THIS clip (no hashtags);\n'
                '  "hashtags": 6-9 relevant tags WITHOUT the # symbol, include "Shorts";\n'
                '  "tags": 8-12 lowercase search keywords.\n\n'
                f"Source video: {video_title}\nCreator: {uploader}\nTranscript:\n{transcript[:1600]}"
            )
            raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
            data = json.loads(raw)
            title = title or _clean_title(str(data.get("title", "")), video_title)
            hook = re.sub(r"\s+", " ", str(data.get("description", ""))).strip()
            hashtags = [re.sub(r"[^A-Za-z0-9]", "", str(h)) for h in data.get("hashtags", [])]
            hashtags = [h for h in hashtags if len(h) >= 3][:9]
            tags = [str(t).strip().lower() for t in data.get("tags", []) if str(t).strip()][:12]
        except Exception as e:  # noqa
            print(f"[metadata] Gemini failed ({e}); using heuristic metadata.")

    # Fill any gaps with heuristics.
    if not title:
        title = _crisp_heuristic(transcript, video_title)
    if not hashtags:
        hashtags = _hashtags_heuristic(video_title, transcript, uploader)
    if not tags:
        tags = _keywords_heuristic(video_title, transcript, uploader)
    if not hook:
        hook = _hook_sentence(transcript, title)

    return {
        "title": _title_with_hashtags(title, hashtags),
        "description": _assemble_description(hook, video_title, uploader, hashtags),
        "hashtags": hashtags,
        "tags": tags,
    }
