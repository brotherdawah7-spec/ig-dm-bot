"""Comment -> DM -> follow check -> free book link, for @brother.dawah.

Runs in GitHub Actions every few minutes using the official Instagram API (Instagram Login).
Follows Meta's messaging rules:
  * one private reply per comment, only within 7 days of the comment;
  * further messages only after the person replies (24-hour window);
  * the follow check (is_user_follow_business) only after the person has messaged us;
  * at most MAX_REMINDERS "please follow" reminders, then the bot goes quiet.

Which reels: any of your reels whose caption contains 'Comment "PDF"' (any case/quotes)
and the title of a book from books.json. The book in the caption decides the link.

Secrets: IG_TOKEN, IG_APP_SECRET (same values as the auto-poster).
State (who was messaged, when) is stored encrypted in state/dm.enc.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from cryptography.fernet import Fernet, InvalidToken

ROOT = Path(__file__).resolve().parent.parent
BOOKS = json.loads((ROOT / "bot" / "books.json").read_text(encoding="utf-8"))
STATE = ROOT / "state"
STATE_FILE = STATE / "dm.enc"
TOKEN_FILE = STATE / "token.enc"
GRAPH = "https://graph.instagram.com/v26.0"

KEYWORD = re.compile(r"\bpdf\b", re.I)
CAMPAIGN = re.compile(r"comment\s*[\"'“”‘’]?\s*pdf", re.I)
COMMENT_WINDOW = timedelta(days=7)      # Meta: private reply within 7 days
MSG_WINDOW = timedelta(hours=24)        # Meta: standard messaging window
MAX_REMINDERS = 2
MEDIA_LOOKBACK = timedelta(days=30)
MAX_ACTIONS_PER_RUN = 40                # stay well under rate limits

FIRST_DM = [
    "Assalamu alaikum {name}! Comment ka shukriya 🙏\n\n\"{title}\" ka free link bhej raha hun, bas pehle @brother.dawah ko follow kar lein. Follow karke yahan \"done\" likh dein, link foran mil jayega.",
    "Assalamu alaikum {name}, shukriya!\n\n\"{title}\" bilkul free hai. @brother.dawah ko follow karke yahan \"done\" reply kar dein, link bhej dunga.",
    "Assalamu alaikum {name}! JazakAllah comment ke liye.\n\nFree book \"{title}\" ke liye @brother.dawah ko follow karein aur yahan \"done\" likh dein. Link turant aa jayega.",
]
NOT_YET = [
    "Lagta hai abhi follow nahi hua 🙂 @brother.dawah ko follow karke dobara \"done\" likh dein.",
    "Follow abhi show nahi ho raha. Profile pe ja ke Follow dabayein, phir \"done\" likh dein, InshaAllah link mil jayega.",
]
LINK_MSG = "JazakAllah khair {name}! 📕 Ye raha \"{title}\" ({publisher}):\n{url}\n\nParh kar batayein kaisi lagi. Aur sawal ho to yahin pooch lein."
PUBLIC_REPLIES = ["DM check karein 📩", "Bhej diya, DM dekhein 📕", "DM mein link hai InshaAllah", "Check your DM 📩"]


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}Z] {msg}", flush=True)


def now() -> datetime:
    return datetime.now(timezone.utc)


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("+0000", "+00:00"))


# ---------------------------------------------------------------- storage --

def fernet() -> Fernet:
    secret = os.environ["IG_APP_SECRET"].strip()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("dm:" + secret).encode()).digest()))


_LOADED = {"json": None}


def load_state() -> dict:
    try:
        raw = fernet().decrypt(STATE_FILE.read_bytes()).decode()
    except (FileNotFoundError, InvalidToken):
        raw = json.dumps({"comments": {}, "users": {}, "seed": None})
    _LOADED["json"] = json.dumps(json.loads(raw), sort_keys=True)
    return json.loads(raw)


def save_state(st: dict) -> None:
    """Write only when something changed (encryption output differs every time)."""
    if json.dumps(st, sort_keys=True) == _LOADED["json"]:
        return
    STATE.mkdir(exist_ok=True)
    STATE_FILE.write_bytes(fernet().encrypt(json.dumps(st).encode()))


def seed_hash(t: str) -> str:
    return hashlib.sha256(("seed:" + t).encode()).hexdigest()[:16]


def token(st: dict) -> str:
    """Stored auto-refreshed token, unless the IG_TOKEN secret was replaced."""
    secret = os.environ.get("IG_TOKEN", "").strip()
    if TOKEN_FILE.exists() and st.get("seed") == seed_hash(secret):
        try:
            tok = fernet().decrypt(TOKEN_FILE.read_bytes()).decode()
        except InvalidToken:
            tok = secret
    else:
        tok = secret
        st["seed"], st["token_refreshed"] = seed_hash(secret), None
    last = st.get("token_refreshed")
    if not last or now() - datetime.fromisoformat(last) > timedelta(days=7):
        r = requests.get("https://graph.instagram.com/refresh_access_token",
                         params={"grant_type": "ig_refresh_token", "access_token": tok}, timeout=30)
        if r.ok and r.json().get("access_token"):
            tok = r.json()["access_token"]
            log("Token refreshed.")
        st["token_refreshed"] = now().isoformat()
        STATE.mkdir(exist_ok=True)
        TOKEN_FILE.write_bytes(fernet().encrypt(tok.encode()))
    return tok


# -------------------------------------------------------------------- api --

class ApiError(Exception):
    pass


def api(method: str, path: str, tok: str, json_body: dict | None = None, **params) -> dict:
    params["access_token"] = tok
    r = requests.request(method, f"{GRAPH}/{path}", params=params, json=json_body, timeout=60)
    try:
        body = r.json()
    except ValueError:
        body = {"raw": r.text[:300]}
    if not r.ok or "error" in body:
        raise ApiError(f"{method} {path} -> {r.status_code}: {json.dumps(body.get('error', body))[:500]}")
    return body


def paged(path: str, tok: str, **params):
    url, p = f"{GRAPH}/{path}", dict(params, access_token=tok)
    while url:
        r = requests.get(url, params=p, timeout=60)
        body = r.json()
        if not r.ok or "error" in body:
            raise ApiError(f"GET {path} -> {r.status_code}: {json.dumps(body.get('error', body))[:300]}")
        yield from body.get("data", [])
        url, p = body.get("paging", {}).get("next"), None


def send(tok: str, me: str, recipient: dict, text: str, quick: list[str] | None = None) -> None:
    msg: dict = {"text": text}
    if quick:
        msg["quick_replies"] = [{"content_type": "text", "title": q, "payload": "DONE"} for q in quick]
    api("POST", f"{me}/messages", tok, {"recipient": recipient, "message": msg})


# ------------------------------------------------------------------- logic --

def book_for(caption: str) -> dict | None:
    if not CAMPAIGN.search(caption or ""):
        return None
    low = (caption or "").lower()
    for b in BOOKS:
        if any(k.lower() in low for k in [b["title"], *b.get("aliases", [])]):
            return b
    return None


def campaigns(tok: str) -> list[tuple[dict, dict]]:
    out = []
    for m in paged("me/media", tok, fields="id,caption,timestamp,media_product_type", limit=50):
        if now() - ts(m["timestamp"]) > MEDIA_LOOKBACK:
            break
        b = book_for(m.get("caption", ""))
        if b:
            out.append((m, b))
    return out


def first_name(u: dict) -> str:
    return (u.get("username") or "").split(".")[0][:20] or "bhai"


def handle_new_comments(tok: str, me: str, st: dict, budget: list[int]) -> None:
    for media, book in campaigns(tok):
        for c in paged(f"{media['id']}/comments", tok, fields="id,text,timestamp,from,username", limit=50):
            if budget[0] <= 0:
                return
            cid = c["id"]
            if cid in st["comments"]:
                continue
            frm = c.get("from") or {}
            if frm.get("id") == me or c.get("username") == st.get("me_username"):
                st["comments"][cid] = {"skip": "own"}
                continue
            if now() - ts(c["timestamp"]) > COMMENT_WINDOW:
                st["comments"][cid] = {"skip": "too old"}
                continue
            if not KEYWORD.search(c.get("text", "")):
                st["comments"][cid] = {"skip": "no keyword"}
                continue
            uid = frm.get("id")
            user = st["users"].get(uid) if uid else None
            if user and user.get("sent_link") == book["title"]:
                st["comments"][cid] = {"skip": "already has this book"}
                continue
            name = c.get("username") or frm.get("username") or "bhai"
            try:
                send(tok, me, {"comment_id": cid},
                     random.choice(FIRST_DM).format(name=name, title=book["title"]),
                     quick=["Done ✅"])
            except ApiError as e:
                if "quick_replies" in str(e) or "param" in str(e).lower():
                    send(tok, me, {"comment_id": cid},
                         random.choice(FIRST_DM).format(name=name, title=book["title"]))
                else:
                    st["comments"][cid] = {"error": str(e)[:300], "at": now().isoformat()}
                    log(f"private reply failed for {cid}: {e}")
                    budget[0] -= 1
                    continue
            budget[0] -= 1
            st["comments"][cid] = {"dm": now().isoformat(), "user": uid}
            if uid:
                st["users"][uid] = {"username": name, "book": book["title"], "asked": now().isoformat(),
                                    "reminders": 0, "last_seen_msg": None, "sent_link": (user or {}).get("sent_link")}
            log(f"DM sent to @{name} for '{book['title']}'")
            try:
                api("POST", f"{cid}/replies", tok, message=random.choice(PUBLIC_REPLIES))
            except ApiError as e:
                log(f"  public reply skipped: {str(e)[:150]}")
            time.sleep(random.uniform(2, 5))


def latest_incoming(tok: str, me: str, uid: str) -> dict | None:
    convs = api("GET", "me/conversations", tok, platform="instagram", user_id=uid).get("data", [])
    if not convs:
        return None
    msgs = api("GET", convs[0]["id"], tok, fields="messages.limit(10){id,from,message,created_time}")
    for m in msgs.get("messages", {}).get("data", []):
        if (m.get("from") or {}).get("id") == uid:
            return m
    return None


def handle_replies(tok: str, me: str, st: dict, budget: list[int]) -> None:
    for uid, u in list(st["users"].items()):
        if budget[0] <= 0:
            return
        if u.get("sent_link") == u.get("book") or u.get("gave_up"):
            continue
        if now() - datetime.fromisoformat(u["asked"]) > COMMENT_WINDOW:
            continue
        try:
            m = latest_incoming(tok, me, uid)
        except ApiError as e:
            log(f"conversation lookup failed for @{u['username']}: {str(e)[:200]}")
            budget[0] -= 1
            continue
        budget[0] -= 1
        if not m or m["id"] == u.get("last_seen_msg"):
            continue
        if now() - ts(m["created_time"]) > MSG_WINDOW:
            continue
        u["last_seen_msg"] = m["id"]
        book = next(b for b in BOOKS if b["title"] == u["book"])
        try:
            prof = api("GET", uid, tok, fields="username,is_user_follow_business")
        except ApiError as e:
            log(f"follow check failed for @{u['username']}: {str(e)[:200]}")
            continue
        name = prof.get("username") or u["username"]
        follows = prof.get("is_user_follow_business")
        if follows:
            send(tok, me, {"id": uid}, LINK_MSG.format(name=name, **book))
            u["sent_link"] = book["title"]
            log(f"link sent to @{name}")
        elif u["reminders"] < MAX_REMINDERS:
            send(tok, me, {"id": uid}, NOT_YET[u["reminders"] % len(NOT_YET)], quick=["Done ✅"])
            u["reminders"] += 1
            log(f"@{name} not following yet (reminder {u['reminders']})")
        else:
            u["gave_up"] = True
            log(f"@{name} still not following after {MAX_REMINDERS} reminders; stopping")
        time.sleep(random.uniform(1, 3))


def prune(st: dict) -> None:
    cutoff = now() - timedelta(days=45)
    for uid, u in list(st["users"].items()):
        if datetime.fromisoformat(u["asked"]) < cutoff:
            del st["users"][uid]
    if len(st["comments"]) > 20000:
        st["comments"] = dict(list(st["comments"].items())[-10000:])


def main() -> None:
    st = load_state()
    tok = token(st)
    me_info = api("GET", "me", tok, fields="user_id,username")
    me, st["me_username"] = me_info["user_id"], me_info["username"]
    if "--check" in sys.argv:
        found = campaigns(tok)
        log(f"Logged in as @{st['me_username']}. Active PDF reels: {len(found)}")
        for m, b in found:
            log(f"  {m['id']} {m['timestamp'][:10]} -> {b['title']}")
        save_state(st)
        return
    budget = [MAX_ACTIONS_PER_RUN]
    try:
        handle_replies(tok, me, st, budget)
        handle_new_comments(tok, me, st, budget)
    finally:
        prune(st)
        save_state(st)
    log(f"Done. Tracking {len(st['users'])} people.")


def loop(minutes: float, every: float = 180) -> None:
    """Stay awake and check every few minutes (GitHub only starts scheduled runs now and then)."""
    end = time.time() + minutes * 60
    while True:
        try:
            main()
        except Exception as e:  # keep looping; log and try again next round
            log(f"round failed: {str(e)[:300]}")
        if time.time() + every > end:
            break
        time.sleep(every)


if __name__ == "__main__":
    if "--loop" in sys.argv:
        loop(float(sys.argv[sys.argv.index("--loop") + 1]))
    else:
        main()
