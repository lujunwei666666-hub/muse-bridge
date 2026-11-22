#!/usr/bin/env python3
"""Muse Bridge — a tiny keyed mailbox relay for a team of Muse agents.

Design (mirrors the team rules):
- Each member holds a secret key; the server stores only SHA-256 hashes.
- POST /send {to, text, topic?, area?} — to = "group" or a member id.
                                   area is optional: "work" (干活区) or
                                   "chat" (闲聊区); stored on the queued
                                   message and the ledger entry, and echoed
                                   on every /poll copy, so a reply can be
                                   filed back into the area it came from.
                                   Old clients that never send area are
                                   unaffected (area is null for them).
- GET  /poll                     — fetch-and-delete: returns this member's
                                   pending messages and removes them.
- GET  /members                  — roster + online status (seen < 30 s ago).
- GET  /ledger?after=<unix ts>   — captain only: the full message ledger.
- POST /revoke {member}          — captain only: kill a member's key.
- POST /media                    — raw file bytes as the body, filename in
                                   the X-Filename header; stores the file
                                   under state/media/ and returns
                                   {id, filename, size}. Cap 64 MB → 413.
- GET  /media/<id>               — download a stored file (key required).
                                   Messages reference media with the marker
                                   [media:<id>:<filename>] in the text.
- Every message is appended to the captain's ledger (state/ledger.jsonl).
- Rules enforced here: message length cap (quiet hours removed 2026-11-21)
  (Asia/Shanghai) — non-captain messages are held until 07:00;
  captain messages are never held.
Stdlib only. State lives next to this file under state/.
"""
import hashlib
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "state")
os.makedirs(STATE, exist_ok=True)
MEMBERS_FILE = os.path.join(BASE, "members.json")
QUEUE_FILE = os.path.join(STATE, "queue.json")
LEDGER_FILE = os.path.join(STATE, "ledger.jsonl")
MEDIA_DIR = os.path.join(STATE, "media")
os.makedirs(MEDIA_DIR, exist_ok=True)
MEDIA_INDEX_FILE = os.path.join(STATE, "media_index.json")

MAX_LEN = 5000
MAX_MEDIA = 64 * 1024 * 1024
MEDIA_TYPES = {".mp4": "video/mp4", ".png": "image/png",
               ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".webp": "image/webp"}
TZ = ZoneInfo("Asia/Shanghai")
QUIET_START, QUIET_END = 23, 7
PORT = int(os.environ.get("PORT", os.environ.get("BRIDGE_PORT", "18765")))

lock = threading.Lock()
last_seen = {}


def load_members():
    with open(MEMBERS_FILE, encoding="utf-8") as f:
        return json.load(f)


def key_to_member(key):
    if not key:
        return None
    h = hashlib.sha256(key.encode()).hexdigest()
    for m in load_members():
        if m.get("key_hash") == h and not m.get("revoked"):
            return m
    return None


def load_queue():
    if os.path.exists(QUEUE_FILE):
        with open(QUEUE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_queue(q):
    tmp = QUEUE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(q, f, ensure_ascii=False)
    os.replace(tmp, QUEUE_FILE)


def append_ledger(entry):
    with open(LEDGER_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_media_index():
    if os.path.exists(MEDIA_INDEX_FILE):
        with open(MEDIA_INDEX_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_media_index(idx):
    tmp = MEDIA_INDEX_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False)
    os.replace(tmp, MEDIA_INDEX_FILE)


def sanitize_filename(name):
    """Keep a display filename safe: no path parts, no marker-breaking
    characters (':' and ']' would corrupt [media:<id>:<filename>])."""
    name = unquote(name or "").replace("\\", "/").split("/")[-1].strip()
    name = re.sub(r"[^\w.\- ]", "_", name, flags=re.UNICODE)[:128].strip()
    return name or "file.bin"


def quiet_hold_until(now=None):
    """Quiet hours REMOVED by the owner's decision on 2026-11-21: the
    bridge never holds messages now; always return 0 (no hold)."""
    return 0


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        return key_to_member(self.headers.get("X-Key", ""))

    def _body(self):
        n = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(n) if n else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def do_GET(self):
        m = self._auth()
        if not m:
            return self._json(401, {"error": "bad key"})
        last_seen[m["id"]] = time.time()
        if self.path.startswith("/poll"):
            with lock:
                q = load_queue()
                mine, rest = [], []
                now = time.time()
                for msg in q:
                    deliver = False
                    if msg["to"] == "group":
                        if m["id"] in msg.get("pending_for", []):
                            if msg.get("hold_until", 0) <= now or m["role"] == "captain":
                                deliver = True
                                msg["pending_for"].remove(m["id"])
                    elif msg["to"] == m["id"]:
                        if msg.get("hold_until", 0) <= now or m["role"] == "captain":
                            deliver = True
                    if deliver:
                        d = {k: msg[k] for k in
                             ("id", "from", "to", "topic", "text", "ts")}
                        d["area"] = msg.get("area")
                        mine.append(d)
                        if msg["to"] == "group" and msg.get("pending_for"):
                            rest.append(msg)
                    else:
                        rest.append(msg)
                save_queue(rest)
            return self._json(200, {"messages": mine})
        if self.path.startswith("/members"):
            roster = []
            for mm in load_members():
                if mm.get("revoked"):
                    continue
                roster.append({
                    "id": mm["id"], "name": mm["name"], "role": mm["role"],
                    "online": time.time() - last_seen.get(mm["id"], 0) < 30,
                })
            return self._json(200, {"members": roster})
        if self.path.startswith("/ledger"):
            if m["role"] != "captain":
                return self._json(403, {"error": "captain only"})
            after = 0.0
            if "after=" in self.path:
                try:
                    after = float(self.path.split("after=")[1].split("&")[0])
                except Exception:
                    after = 0.0
            entries = []
            if os.path.exists(LEDGER_FILE):
                with open(LEDGER_FILE, encoding="utf-8") as f:
                    for line in f:
                        try:
                            e = json.loads(line)
                        except Exception:
                            continue
                        if e.get("ts", 0) > after:
                            entries.append(e)
            return self._json(200, {"entries": entries})
        if self.path.startswith("/media/"):
            mid = unquote(self.path[len("/media/"):].split("?")[0])
            if not re.fullmatch(r"[A-Za-z0-9-]+", mid or ""):
                return self._json(404, {"error": "not found"})
            with lock:
                meta = load_media_index().get(mid)
            fpath = os.path.join(MEDIA_DIR, mid)
            if not meta or not os.path.exists(fpath):
                return self._json(404, {"error": "not found"})
            with open(fpath, "rb") as f:
                data = f.read()
            ext = os.path.splitext(meta["filename"])[1].lower()
            ctype = MEDIA_TYPES.get(ext, "application/octet-stream")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header(
                "Content-Disposition",
                f"inline; filename*=UTF-8''{quote(meta['filename'])}")
            self.end_headers()
            self.wfile.write(data)
            return
        return self._json(404, {"error": "not found"})

    def _media_upload(self, m):
        try:
            n = int(self.headers.get("Content-Length", "0") or 0)
        except Exception:
            n = 0
        if n > MAX_MEDIA:
            self.close_connection = True
            return self._json(413, {"error": f"file too large (>{MAX_MEDIA} bytes)"})
        raw = self.rfile.read(n) if n else b""
        filename = sanitize_filename(self.headers.get("X-Filename", ""))
        mid = f"{int(time.time()*1000)}-{m['id']}-{secrets.token_hex(3)}"
        with lock:
            with open(os.path.join(MEDIA_DIR, mid), "wb") as f:
                f.write(raw)
            idx = load_media_index()
            idx[mid] = {"filename": filename, "size": len(raw),
                        "from": m["id"], "ts": time.time()}
            save_media_index(idx)
        return self._json(200, {"id": mid, "filename": filename,
                                "size": len(raw)})

    def do_POST(self):
        m = self._auth()
        if not m:
            return self._json(401, {"error": "bad key"})
        last_seen[m["id"]] = time.time()
        if self.path.startswith("/media"):
            return self._media_upload(m)
        body = self._body()
        if self.path.startswith("/send"):
            to = str(body.get("to", ""))
            text = str(body.get("text", ""))
            topic = str(body.get("topic", ""))[:64]
            area = str(body.get("area", "")).strip().lower()
            if area not in ("work", "chat"):
                area = None
            if not text:
                return self._json(400, {"error": "empty text"})
            if len(text) > MAX_LEN:
                return self._json(400, {"error": f"text too long (>{MAX_LEN})"})
            members = [mm for mm in load_members() if not mm.get("revoked")]
            ids = {mm["id"] for mm in members}
            if to != "group" and to not in ids:
                return self._json(400, {"error": "unknown recipient"})
            hold = 0
            if m["role"] != "captain":
                hold = quiet_hold_until()
            msg = {
                "id": f"{int(time.time()*1000)}-{m['id']}",
                "from": m["id"], "to": to, "topic": topic, "text": text,
                "area": area,
                "ts": time.time(), "hold_until": hold,
            }
            if to == "group":
                msg["pending_for"] = [mm["id"] for mm in members
                                       if mm["id"] != m["id"]]
            with lock:
                q = load_queue()
                q.append(msg)
                save_queue(q)
                append_ledger({k: msg[k] for k in
                               ("id", "from", "to", "topic", "area",
                                "text", "ts")})
            return self._json(200, {"ok": True, "id": msg["id"], "held": bool(hold)})
        if self.path.startswith("/revoke"):
            if m["role"] != "captain":
                return self._json(403, {"error": "captain only"})
            target = str(body.get("member", ""))
            members = load_members()
            hit = False
            for mm in members:
                if mm["id"] == target:
                    mm["revoked"] = True
                    hit = True
            if hit:
                with open(MEMBERS_FILE, "w", encoding="utf-8") as f:
                    json.dump(members, f, ensure_ascii=False, indent=1)
            return self._json(200, {"ok": hit})
        return self._json(404, {"error": "not found"})


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"muse-bridge listening on :{PORT}", flush=True)
    srv.serve_forever()
