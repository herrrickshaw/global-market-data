#!/bin/bash
# strip_vcrud_workflow.sh
# =======================
# Remove the vCRUD "mandatory workflow" git enforcement and restore normal git
# with clean Git LFS hooks only. Idempotent + safe (touches only .git hook config,
# never your data). Run from the repo root.
set -uo pipefail
REPO="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO" || exit 1
GITDIR="$(git rev-parse --git-dir)"

echo "== stripping vCRUD workflow from $REPO =="

echo "1) reset core.hooksPath to git default"
git config --unset core.hooksPath 2>/dev/null || true

echo "2) remove vCRUD-flavoured hooks (keep git-lfs behaviour)"
for h in pre-commit prepare-commit-msg commit-msg post-commit post-merge post-checkout pre-push reference-transaction post-rewrite; do
    f="$GITDIR/hooks/$h"
    if [ -f "$f" ] && grep -qiE "vcrud|mandatory|find_duplicates|repository.systems|feature branch summary" "$f"; then
        rm -f "$f"
        echo "   removed hooks/$h"
    fi
done

echo "3) uninstall the pre-commit framework if present, disable its config"
command -v pre-commit >/dev/null 2>&1 && pre-commit uninstall >/dev/null 2>&1 || true
[ -f .pre-commit-config.yaml ] && grep -qi "vcrud" .pre-commit-config.yaml 2>/dev/null \
    && mv .pre-commit-config.yaml .pre-commit-config.yaml.disabled && echo "   disabled vcrud .pre-commit-config.yaml" || true

echo "4) reinstall clean Git LFS hooks"
git lfs install --force >/dev/null 2>&1 && echo "   git-lfs hooks reinstalled" || echo "   (git-lfs not found; skipped)"

echo "5) clean scaffolding"
rm -rf "$GITDIR/nohooks" "$GITDIR/hooks_vcrud_disabled"

echo
echo "== result =="
echo "   core.hooksPath : $(git config core.hooksPath 2>/dev/null || echo '(default .git/hooks)')"
echo "   active hooks   : $(ls "$GITDIR/hooks" 2>/dev/null | grep -v '\.sample$' | tr '\n' ' ' || echo none)"
echo "Done. Normal git restored. The vCRUD *tracker* (vcrud_manager/db_handler) is"
echo "untouched and still usable on demand; only the auto-enforcement is removed."
