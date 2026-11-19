"""One-off: look at the newest reels for "arabic"-like comments (no usernames written)."""
import json, re, sys
from pathlib import Path
sys.argv = ["report"]
sys.path.insert(0, str(Path(__file__).parent))
import bot as B

LOOSE = re.compile(r"arab|arbi|عرب|ar\s*abic", re.I)
st = B.load_state()
tok = B.token(st)
reels = []
for i, m in enumerate(B.paged("me/media", tok, fields="id,caption,timestamp,media_product_type,comments_count,permalink", limit=10)):
    if i >= 8:
        break
    texts = []
    for c in B.paged(f"{m['id']}/comments", tok, fields="id,text,timestamp,replies{text,timestamp}", limit=50):
        texts.append(c.get("text", ""))
        texts += [r.get("text", "") for r in (c.get("replies") or {}).get("data", [])]
    reels.append({"link": m.get("permalink"), "posted": m["timestamp"], "type": m.get("media_product_type"),
                  "instagram_count": m.get("comments_count"), "readable": len(texts),
                  "arabic_like": [t[:80] for t in texts if LOOSE.search(t)],
                  "strict_match": sum(bool(B.ARABIC.search(t)) for t in texts)})
out = {"checked_at": B.now().isoformat(), "reels": reels}
Path("reports/arabic.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
print(json.dumps(out, indent=1, ensure_ascii=False))
