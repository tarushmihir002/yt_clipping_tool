"""Creator Clipping Tool — CLI.

Commands:
  python main.py auth                         Authorize YouTube (OAuth)
  python main.py whoami                       Show the authorized channel
  python main.py clip  --url U --start S --end E [--no-captions] [--fit blur] [--upload]
  python main.py auto  --url U [--count 3] [--len 45] [--upload] ...
  python main.py live  --url U --duration 60 [--upload] ...
  python main.py upload --file F --title T [--privacy private]
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

from clipper import config, pipeline, upload


def _add_common(p):
    p.add_argument("--no-captions", action="store_true", help="skip burned captions")
    p.add_argument("--fit", choices=["crop", "blur"], default="crop", help="vertical fit mode")
    p.add_argument("--upload", action="store_true", help="upload result(s) to YouTube")
    p.add_argument("--privacy", default="public", choices=["private", "unlisted", "public"])


def _upload_result(r, privacy):
    desc = r.description or "Auto-clipped with Creator Clipping Tool."
    tags = r.tags or ["shorts", "clip"]
    print(f"[upload] '{r.title}' as {privacy}…")
    resp = upload.upload_video(
        Path(r.path), title=r.title, description=desc,
        tags=tags, privacy=privacy,
    )
    vid = resp["id"]
    print(f"[upload] done -> https://youtu.be/{vid}  (studio: https://studio.youtube.com)")
    return vid


def cmd_auth(args):
    creds = upload.run_oauth_flow(open_browser=not args.manual)
    ch = upload.get_channel()
    print(f"Authorized channel: {ch.get('title')} ({ch.get('id')})")


def cmd_whoami(args):
    ch = upload.get_channel()
    print(f"{ch.get('title')} ({ch.get('id')})")


def cmd_clip(args):
    r = pipeline.clip_range(
        args.url, args.start, args.end,
        captions=not args.no_captions, fit=args.fit, title=args.title,
    )
    print(f"[clip] -> {r.path}  | title: {r.title}")
    if args.upload:
        _upload_result(r, args.privacy)


def cmd_auto(args):
    results = pipeline.auto_clips(
        args.url, count=args.count, clip_len=args.len,
        captions=not args.no_captions, fit=args.fit,
    )
    for r in results:
        print(f"[auto] -> {r.path}  | {r.title}")
    if args.upload:
        for r in results:
            _upload_result(r, args.privacy)


def cmd_live(args):
    r = pipeline.clip_live(args.url, args.duration, captions=not args.no_captions, fit=args.fit)
    print(f"[live] -> {r.path}  | {r.title}")
    if args.upload:
        _upload_result(r, args.privacy)


def cmd_upload(args):
    resp = upload.upload_video(
        Path(args.file), title=args.title, description=args.description or "",
        tags=(args.tags.split(",") if args.tags else []), privacy=args.privacy,
    )
    print(f"https://youtu.be/{resp['id']}")


def build_parser():
    ap = argparse.ArgumentParser(prog="clipper", description="Clip YouTube VODs/livestreams and upload.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("auth", help="authorize YouTube")
    a.add_argument("--manual", action="store_true", help="don't auto-open a browser; print URL only")
    a.set_defaults(func=cmd_auth)

    sub.add_parser("whoami", help="show authorized channel").set_defaults(func=cmd_whoami)

    c = sub.add_parser("clip", help="clip a known range from a VOD")
    c.add_argument("--url", required=True)
    c.add_argument("--start", required=True, help="start (sec or HH:MM:SS)")
    c.add_argument("--end", required=True, help="end (sec or HH:MM:SS)")
    c.add_argument("--title", default=None)
    _add_common(c)
    c.set_defaults(func=cmd_clip)

    au = sub.add_parser("auto", help="auto-detect highlights from a VOD")
    au.add_argument("--url", required=True)
    au.add_argument("--count", type=int, default=3)
    au.add_argument("--len", type=int, default=45, help="target clip length (s)")
    _add_common(au)
    au.set_defaults(func=cmd_auto)

    lv = sub.add_parser("live", help="clip from an ongoing livestream")
    lv.add_argument("--url", required=True)
    lv.add_argument("--duration", type=int, default=60)
    _add_common(lv)
    lv.set_defaults(func=cmd_live)

    up = sub.add_parser("upload", help="upload an existing file")
    up.add_argument("--file", required=True)
    up.add_argument("--title", required=True)
    up.add_argument("--description", default="")
    up.add_argument("--tags", default="")
    up.add_argument("--privacy", default="public", choices=["private", "unlisted", "public"])
    up.set_defaults(func=cmd_upload)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
