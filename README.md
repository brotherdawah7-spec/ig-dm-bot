# @brother.dawah PDF DM bot

Free "comment PDF → DM → follow check → free book" bot, running on GitHub Actions every ~5–15 minutes
with the **official Instagram API**. No browser automation.

## How it works
1. It looks at your reels from the last 30 days whose caption contains `Comment "PDF"` **and** the
   title of a book in [`bot/books.json`](bot/books.json). The book in the caption decides the link.
2. Someone comments a message containing "pdf" → the bot sends **one private reply** (Instagram allows
   one per comment, within 7 days) asking them to follow and reply "done", plus a short public reply
   ("DM check karein").
3. When they reply, the bot checks whether they follow you (Instagram only allows this check after
   they message you). Following → it sends the book link. Not following → a friendly reminder, at most
   2 times, then it stops.

## Adding a book
Add an entry to `bot/books.json` (`title`, `publisher`, `url`, optional `aliases`) and put the title in
the reel caption together with `Comment "PDF"`.

## Secrets (Settings → Secrets and variables → Actions)
`IG_TOKEN` and `IG_APP_SECRET` — the same values as the auto-poster. The token renews itself.
State (who was messaged) is stored **encrypted** in `state/`; nothing personal is readable in this repo.

## Test
Actions → **DM bot** → Run workflow → mode `check`: logs in and lists the reels the bot is watching.

## Limits
- Meta only allows apps without Advanced Access (App Review) to message people who have a role on
  the app. If DMs to real commenters fail with a permissions error, the app needs Meta App Review +
  Business Verification.
- GitHub may delay scheduled runs; replies usually go out within 5–15 minutes.
