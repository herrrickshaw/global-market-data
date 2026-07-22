# Providing screener.in access (for India fundamentals)

The authenticated module (`screener_in_auth.py`) downloads per-company financials
from screener.in so the India fundamental screeners run on real data. Credentials
are read **only** from environment variables — never hard-code, paste, or commit them.

## 1. Account
Sign up / log in at **screener.in** (a free account already unlocks the per-company
"Export to Excel" this uses). Prefer a **dedicated password** for this account.

## 2. Set the credentials

**Easiest — a local `.env` file (gitignored, never committed):**
```bash
cp .env.example .env      # then open .env and paste your real password
python screener_in_auth.py RELIANCE   # expect: logged in ✓
```
`screener_in_auth` auto-loads `.env`, so nothing else is needed.

**Or export env vars directly**

**Local (macOS/Linux):**
```bash
export SCREENER_EMAIL="you@example.com"
export SCREENER_PASSWORD="your-screener-password"
# persist (optional): add the two lines to ~/.zshrc
```

**Google Colab** — use the 🔑 Secrets panel (add `SCREENER_EMAIL`, `SCREENER_PASSWORD`), then:
```python
import os
from google.colab import userdata
os.environ["SCREENER_EMAIL"]    = userdata.get("SCREENER_EMAIL")
os.environ["SCREENER_PASSWORD"] = userdata.get("SCREENER_PASSWORD")
```

**Docker** — pass at runtime (never bake into the image):
```bash
docker run --rm -e SCREENER_EMAIL="you@example.com" -e SCREENER_PASSWORD="••••" \
  ghcr.io/herrrickshaw/global-stock-screener:latest \
  python -c "import full_report as fr; fr.india_all_screeners(auth_limit=300)"
```

**launchd/cron** — add both keys to the plist `EnvironmentVariables` block
(next to `GMAIL_APP_PASSWORD`).

## 3. Verify
```bash
python screener_in_auth.py RELIANCE
```
- `logged in ✓` + a dict  → working.
- `AUTH: login failed — check credentials`  → wrong email/password.
- `AUTH: set SCREENER_EMAIL and SCREENER_PASSWORD…`  → env vars not exported here.

## 4. Run the full India report (all 11 screeners)
```bash
python -c "import full_report as fr; fr.india_all_screeners(auth_limit=300)"
```
With creds set, the fundamental screeners run on real financials for the top-N
liquid names; without them it auto-falls back to the bhavcopy + scan path (no error).

## Security & etiquette
- Env-vars only; the password never touches the repo, logs, or chat.
- Use a dedicated/low-value password.
- Respect screener.in's Terms of Service and rate limits — `fundamentals_batch`
  already pauses between requests; keep `auth_limit` reasonable.
