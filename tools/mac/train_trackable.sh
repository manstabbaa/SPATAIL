#!/usr/bin/env bash
# tools/mac/train_trackable.sh — train one SPATAIL trackable on this Mac.
#
# USDZ (photoreal, real-scale — Object Capture or CAD) → Create ML object
# tracker → <id>.referenceobject + <id>.json sidecar → committed into
# public/assets/spatail-trackables/ and pushed. The PC's job server serves the
# folder at GET /trackables; the phone downloads at runtime — no app rebuild.
#
#   tools/mac/train_trackable.sh -s scan.usdz -i trash_can -j "trash can,bin"
#
# Options:
#   -s <path>   source USDZ (required)
#   -i <id>     trackable id, snake_case (required; output filenames)
#   -j <list>   comma-separated subjects — words a user's prompt would contain
#               (required; drives prompt→trackable matching on the PC)
#   -n <name>   display name             (default: id, underscores→spaces, Title Case)
#   -t <lane>   detection | tracking     (default: detection; tracking = handheld,
#               per-frame pose, high power — pair with -a all-angles)
#   -a <view>   upright | front | all-angles   (default: upright)
#   -A <path>   USDZ of a similar-looking object to avoid (repeatable)
#   -P          skip git commit+push (train + stage files only)
#
# Training runs for HOURS (Mac-spec dependent) — run inside caffeinate (the
# script does this itself) and leave the Mac on AC power.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
DEST="$REPO/public/assets/spatail-trackables"

SRC="" ID="" SUBJECTS="" NAME="" LANE="detection" VIEW="upright" PUSH=1
AVOID_ARGS=()

while getopts "s:i:j:n:t:a:A:P" opt; do
  case "$opt" in
    s) SRC="$OPTARG" ;;
    i) ID="$OPTARG" ;;
    j) SUBJECTS="$OPTARG" ;;
    n) NAME="$OPTARG" ;;
    t) LANE="$OPTARG" ;;
    a) VIEW="$OPTARG" ;;
    A) AVOID_ARGS+=(--objects-to-avoid "$OPTARG") ;;
    P) PUSH=0 ;;
    *) exit 2 ;;
  esac
done

[ -n "$SRC" ] && [ -n "$ID" ] && [ -n "$SUBJECTS" ] || {
  sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
[ -f "$SRC" ] || { echo "error: source USDZ not found: $SRC" >&2; exit 1; }
[[ "$ID" =~ ^[a-z0-9_]+$ ]] || { echo "error: id must be snake_case: $ID" >&2; exit 1; }
[[ "$LANE" == "detection" || "$LANE" == "tracking" ]] || {
  echo "error: -t must be detection|tracking" >&2; exit 1; }
[[ "$VIEW" == "upright" || "$VIEW" == "front" || "$VIEW" == "all-angles" ]] || {
  echo "error: -a must be upright|front|all-angles" >&2; exit 1; }
[ -n "$NAME" ] || NAME="$(echo "$ID" | tr '_' ' ' | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) substr($i,2)}1')"

WORK="$(mktemp -d /tmp/spatail_trackable_XXXX)"
REF="$WORK/$ID.referenceobject"

echo "[train_trackable] $ID ← $SRC  (view: $VIEW, lane: $LANE)"
echo "[train_trackable] training starts now — this takes HOURS. Checkpoints: $WORK/checkpoints"
caffeinate -ims xcrun createml objecttracker \
  --source "$SRC" --output "$REF" \
  --"$VIEW" ${AVOID_ARGS[@]+"${AVOID_ARGS[@]}"} \
  --checkpoint "$WORK/checkpoints" --summary

[ -f "$REF" ] || { echo "error: training produced no $REF" >&2; exit 1; }

# Sidecar consumed by job_server._trackables_index(): {name, tracking, subjects}.
python3 - "$WORK/$ID.json" "$NAME" "$LANE" "$SUBJECTS" <<'PY'
import json, sys
path, name, lane, subjects = sys.argv[1:5]
json.dump({"name": name, "tracking": lane,
           "subjects": [s.strip() for s in subjects.split(",") if s.strip()]},
          open(path, "w"), indent=2)
PY

mkdir -p "$DEST"
cp "$REF" "$WORK/$ID.json" "$DEST/"
echo "[train_trackable] staged: $DEST/$ID.referenceobject ($(du -h "$REF" | cut -f1)) + $ID.json"

if [ "$PUSH" -eq 1 ]; then
  cd "$REPO"
  git add "public/assets/spatail-trackables/$ID.referenceobject" \
          "public/assets/spatail-trackables/$ID.json"
  git commit -m "mac: trackable $ID ($LANE, $VIEW) — $NAME"
  git push origin "$(git rev-parse --abbrev-ref HEAD)"
  echo "[train_trackable] pushed — the PC serves it at GET /trackables after its next git:sync."
else
  echo "[train_trackable] -P given: not committed. Files staged in $DEST."
fi
