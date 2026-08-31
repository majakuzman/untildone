#!/bin/bash
# Smoke test: runs the daemon against a throwaway folder. Works on macOS and Linux.
# On macOS it exercises the real Apple Reminders bridge, but only in its own list
# "Claude-TEST" with titles prefixed TEST; the list is deleted at the end.
# It never touches your real "Claude" list. Usage: bash tests/smoke.sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"; export RD_HOME="$T" RD_DRIVE="$T/drive" RD_REM_LIST="Claude-TEST"; mkdir -p "$T/drive" "$T/inbox_local"
D="python3 $ROOT/daemon.py"
cleanup() {
  if command -v osascript >/dev/null 2>&1; then
    osascript -e 'tell application "Reminders" to if exists list "Claude-TEST" then delete list "Claude-TEST"' >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
$D init >/dev/null
echo '{"action":"add","title":"TEST buy toothpaste","due":"2030-01-01T17:00"}' > "$T/inbox_local/a1.json"
echo '{"action":"add","title":"TEST QC report","assignee":"Dunja","status":"in_progress","project":"work"}' > "$T/drive/a2.json"
echo '{"action":"add","title":"TEST buy milk","due":"2030-01-01T17:00"}' > "$T/inbox_local/a3.json"
$D once | grep -q "added T001 TEST buy toothpaste" || { echo FAIL add; exit 1; }
[ -z "$(ls "$T/inbox_local" "$T/drive" | grep '\.json$' | grep -v status)" ] || { echo FAIL "inbox not emptied"; exit 1; }
if command -v osascript >/dev/null 2>&1; then
  n="$(osascript -e 'tell application "Reminders" to count of (reminders of list "Claude-TEST" whose completed is false)' 2>/dev/null || echo 0)"
  [ "$n" -ge 2 ] || { echo FAIL "expected pre-armed items in Claude-TEST, got $n"; exit 1; }
  echo "reminders bridge OK ($n items in Claude-TEST)"
fi
echo '{"action":"done","id":"buy"}' > "$T/inbox_local/b1.json"
$D once | grep -q "ambiguous 'buy'" || { echo FAIL ambiguity; exit 1; }
echo '{"action":"done","id":"toothpaste"}' > "$T/inbox_local/b2.json"
echo '{"action":"update","id":"T002","status":"waiting"}' > "$T/inbox_local/b3.json"
echo '{"action":"snooze","id":"milk","minutes":0}' > "$T/inbox_local/b4.json"
echo '{"action":"export","filter":{"project":"work"}}' > "$T/inbox_local/b5.json"
echo 'garbage' > "$T/inbox_local/b6.json"
OUT="$($D once)"
echo "$OUT" | grep -q "T001 not_started->done" || { echo FAIL done; exit 1; }
echo "$OUT" | grep -q "T002 in_progress->waiting" || { echo FAIL update; exit 1; }
echo "$OUT" | grep -q "exported 1 rows" || { echo FAIL export; exit 1; }
echo "$OUT" | grep -q "FAIL b6.json" || { echo FAIL "bad json not reported"; exit 1; }
[ -f "$T/inbox_failed/b6.json" ] || { echo FAIL "bad json not quarantined"; exit 1; }
python3 - "$T/status.json" <<'PY'
import json,sys; s=json.load(open(sys.argv[1]))
assert s["open_count"]==2 and s["open_by_assignee"]=={"Dunja":1,"me":1}, s["open_by_assignee"]
PY
echo "smoke test OK ($T)"
