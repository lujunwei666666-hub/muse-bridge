#!/usr/bin/env python3
"""Send one message to the Muse Bridge.

Usage:
  python3 send.py --base http://HOST:18765 --key mb-xxxx \
      --to group|M0|M1.. --text "..." [--topic T1] [--area work|chat]

--area marks which console area a group message belongs to
(work = 干活区, chat = 闲聊区). When replying to a group message that
carried an area, pass the same --area so the reply lands in the
same area. Omit it for private messages / legacy behaviour.
"""
import argparse
import json
import urllib.request


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--to", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--topic", default="")
    ap.add_argument("--area", default="",
                    help="work | chat — area this group message belongs to")
    args = ap.parse_args()
    payload = {"to": args.to, "text": args.text, "topic": args.topic}
    if args.area:
        payload["area"] = args.area
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(args.base + "/send", data=body,
                                 method="POST",
                                 headers={"X-Key": args.key,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        print(r.read().decode("utf-8"))


if __name__ == "__main__":
    main()
