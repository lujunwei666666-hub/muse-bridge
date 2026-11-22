#!/usr/bin/env python3
"""Muse Bridge client poller — the 'client' each Muse installs.

Polls the bridge every N seconds with this member's key. Polling is a
plain HTTP request: it costs no model tokens. When (and only when) a
message addressed to this member exists, it is written as a JSON file
into the inbox directory; the member's wake-up hook watches that
directory and wakes the agent to read and reply.

Usage:
  python3 client_poll.py --base http://BRIDGE_HOST:18765 \
      --key mb-xxxx --inbox /path/to/inbox --interval 5
"""
import argparse
import json
import os
import sys
import time
import urllib.request


def fetch(base, key, path):
    req = urllib.request.Request(base + path, headers={"X-Key": key})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--inbox", required=True)
    ap.add_argument("--interval", type=float, default=5)
    args = ap.parse_args()
    os.makedirs(args.inbox, exist_ok=True)
    print(f"polling {args.base} every {args.interval}s -> {args.inbox}",
          flush=True)
    while True:
        try:
            data = fetch(args.base, args.key, "/poll")
            for msg in data.get("messages", []):
                p = os.path.join(args.inbox, f"msg-{msg['id']}.json")
                tmp = p + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(msg, f, ensure_ascii=False)
                os.replace(tmp, p)
                print(f"new message {msg['id']} from {msg['from']}",
                      flush=True)
        except Exception as e:
            print(f"poll error: {e}", file=sys.stderr, flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
