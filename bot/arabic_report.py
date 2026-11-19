"""One-off: count recent "arabic" comments (no usernames written) so we can confirm what the bot will answer."""
import json, sys
from pathlib import Path
sys.argv = ["report"]
sys.path.insert(0, str(Path(__file__).parent))
import bot as B

st = B.load_state()
tok = B.token(st)
rows, total = [], 0
for m, _ in B.watched_media(tok):
    for c in B.paged(f"{m['id']}/comments", tok, fields="id,text,timestamp", limit=50):
        if B.ARABIC.search(c.get("text", "")):
            age_h = round((B.now() - B.ts(c["timestamp"])).total_seconds() / 3600, 1)
            total += 1
            rows.append({"reel": m["id"], "hours_ago": age_h, "within_7_days": age_h <= 168,
                         "already_handled": c["id"] in st["comments"] and "skip" not in st["comments"][c["id"]]})
out = {"checked_at": B.now().isoformat(), "arabic_comments": total, "details": rows}
Path("reports/arabic.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
