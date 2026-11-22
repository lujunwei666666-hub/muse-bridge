#!/usr/bin/env python3
"""Upload a file to the Muse Bridge and send a message referencing it.

Deliverables (videos, images, documents) must be sent with this script
so the file itself travels over the bridge — never send a bare local
path, nobody else can open it. The script uploads the file to
POST /media, then posts the message text with a
[media:<id>:<filename>] marker appended via POST /send.

Usage:
  python3 media_send.py --base http://HOST:18765 --key mb-xxxx \
      --to group --file ~/out/video.mp4 --text "交活：视频做好了" \
      [--area work|chat] [--topic T1]
"""
import argparse
import json
import os
import urllib.request
from urllib.parse import quote


def post(base, key, path, data, headers):
    req = urllib.request.Request(base + path, data=data, method="POST",
                                 headers={"X-Key": key, **headers})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--to", required=True)
    ap.add_argument("--text", default="")
    ap.add_argument("--file", required=True)
    ap.add_argument("--area", default="",
                    help="work | chat — area this group message belongs to")
    ap.add_argument("--topic", default="")
    args = ap.parse_args()
    with open(args.file, "rb") as f:
        data = f.read()
    filename = os.path.basename(args.file)
    up = post(args.base, args.key, "/media", data,
              {"X-Filename": quote(filename),
               "Content-Type": "application/octet-stream"})
    marker = f"[media:{up['id']}:{up['filename']}]"
    text = f"{args.text}\n{marker}" if args.text else marker
    payload = {"to": args.to, "text": text, "topic": args.topic}
    if args.area:
        payload["area"] = args.area
    sent = post(args.base, args.key, "/send",
                json.dumps(payload).encode("utf-8"),
                {"Content-Type": "application/json"})
    print(json.dumps({"media": up, "send": sent}, ensure_ascii=False))


if __name__ == "__main__":
    main()
