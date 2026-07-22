# Security & data integrity

How the repository's data is secured and how tampering is made detectable.

## 1. Tamper-evidence (cryptographic integrity)

Git blobs cannot be made literally immutable, but any change can be made
**cryptographically detectable**. Every tracked file has a SHA-256 recorded in
[`cache_seed/CHECKSUMS.sha256`](cache_seed/CHECKSUMS.sha256).

```bash
python integrity.py --verify        # recompute + compare; exit 1 on ANY drift
python integrity.py --generate      # rewrite the manifest after a legit change
python integrity.py --verify --data-only   # just cache_seed/ + reference_seed/
```

Enforced automatically:
- **CI gate** — `.github/workflows/integrity.yml` runs `--verify` on every push/PR;
  a mismatch, a missing file, or a file the manifest doesn't cover **fails the build**.
- **pre-commit hook** — regenerates the manifest on every commit so committed data
  always ships with matching checksums (`pip install pre-commit && pre-commit install`).

A verification failure means a tracked file changed out-of-band — an unauthorized
edit, a corrupted Git-LFS object, or a tampered blob.

## 2. Authenticity (signed commits)

Sign commits so authorship can't be forged and GitHub shows a **Verified** badge:

```bash
# SSH signing (simplest)
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_ed25519.pub
git config --global commit.gpgsign true
# then add the key at github.com → Settings → SSH and GPG keys → "Signing Key"
```

## 3. Branch protection (server-side, can't be bypassed by a clone)

Make `main` tamper-proof against force-pushes and unreviewed changes (run once):

```bash
gh api -X PUT repos/herrrickshaw/global-stock-screener/branches/main/protection \
  -H "Accept: application/vnd.github+json" \
  -f "required_status_checks[strict]=true" \
  -f "required_status_checks[contexts][]=verify-checksums" \
  -f "required_status_checks[contexts][]=pytest" \
  -F "enforce_admins=true" \
  -F "required_pull_request_reviews[required_approving_review_count]=1" \
  -F "restrictions=null" \
  -F "allow_force_pushes=false" -F "allow_deletions=false"
```

This blocks force-pushes/deletes, requires the integrity + test checks to pass, and
requires PR review before anything lands on `main`.

## 4. Secrets hygiene (verified)

- **No credentials are committed.** `.env` is gitignored; only `.env.example`
  (placeholders) is tracked. Credentials load **only** from env vars / `.env`
  (see `SCREENER_ACCESS.md`) — never hard-coded, pasted, or committed.
- Trained models, the promotion registry, the data manifest, serving/CDC caches and
  the derived STM seeds are all gitignored (regenerated locally), reducing the
  attack surface of committed artifacts.
- To scan before pushing:
  ```bash
  git ls-files | xargs grep -nE "(password|api[_-]?key|secret|token)\s*=\s*['\"][A-Za-z0-9]{12,}" 2>/dev/null
  ```

## Reporting

This is an educational/research project. Report any security concern via a private
GitHub issue or to the repository owner directly.
