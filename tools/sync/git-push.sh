#!/usr/bin/env bash
# tools/sync/git-push.sh
#
# Used at the END of every meaningful change. Stages everything, runs the
# boundary check, commits with the message you pass, pushes.
#
#     npm run git:push -- "ios: BundleLoader wired to ZIPFoundation"
#
# If sync:check fails, we DO NOT push. Fix the drift first.
#
# Convention for commit prefixes:
#   windows:   pipeline / blender / server / Windows-only files
#   ios:       ios/* only
#   protocol:  cross-boundary change (touches docs/xr/ + at least one of
#              experience_contract.js, Vocab.swift, SessionClient.swift,
#              spatail_session_server.py, spatail_export_xr.py)
#   docs:      docs-only change, no code
#   fix:       bug fix; pair with one of the above (e.g. "fix(ios):")

set -euo pipefail

cd "$(dirname "$0")/../.."

if [ $# -lt 1 ]; then
  echo "usage: npm run git:push -- \"<commit message>\""
  echo "       prefix the message with one of: windows: ios: protocol: docs: fix:"
  exit 2
fi

MSG="$*"

# Stash any staged-but-uncommitted state from a partial flow, so we work from a
# clean tree.
if [ -n "$(git status --porcelain)" ]; then
  echo "[git-push] staging working tree…"
  git add -A
fi

if git diff --cached --quiet; then
  echo "[git-push] nothing to commit. Pulling instead to make sure we're current."
  git pull --rebase --autostash origin main
  exit 0
fi

echo "[git-push] running boundary check…"
node tools/sync/check_protocol_sync.mjs

echo "[git-push] committing: $MSG"
git commit -m "$MSG"

echo "[git-push] rebasing on origin/main before push…"
git pull --rebase --autostash origin main

echo "[git-push] pushing to origin/main…"
git push origin main

echo "[git-push] done."
