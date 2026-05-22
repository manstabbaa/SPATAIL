#!/usr/bin/env bash
# tools/sync/git-sync.sh
#
# Used at the START of any work session on either machine.
# Pulls latest, then verifies cross-machine boundary invariants.
#
#     npm run git:sync   →   `git pull --rebase --autostash && npm run sync:check`
#
# Exits non-zero if the boundary check fails after pull — that means the
# OTHER machine pushed a change that requires regen on this side. The fix
# is `npm run sync:swift-vocab && git add -A && git commit -m "ios: regen vocab"`.

set -euo pipefail

cd "$(dirname "$0")/../.."

echo "[git-sync] fetching + rebasing on origin/main…"
git pull --rebase --autostash origin main

echo "[git-sync] verifying boundary contract…"
if ! node tools/sync/check_protocol_sync.mjs; then
  echo ""
  echo "─────────────────────────────────────────────────────"
  echo "Cross-machine sync drift detected after pull."
  echo "If Vocab.swift is stale, run:"
  echo "    npm run sync:swift-vocab"
  echo "    git add ios/SpatailPlayer/Sources/SpatailPlayer/Contract/Vocab.swift"
  echo "    git commit -m \"ios: regen Vocab.swift after pull\""
  echo "    git push"
  echo "─────────────────────────────────────────────────────"
  exit 1
fi

echo "[git-sync] in sync."
