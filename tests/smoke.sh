#!/bin/bash
# Smoke test: runs the daemon against a throwaway folder. Works on macOS and Linux
# (the Reminders bridge is a no-op where osascript is absent). Usage: bash tests/smoke.sh
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
T="$(mktemp -d)"; export RD_HOME="$T" RD_DRIVE="$T/drive"; mkdir -p "$T/drive" "$T/inbox_local"
D="python3 $ROOT/daemon.py"
$D init >/dev/null
echo '{"action":"add","title":"buy toothpaste","due":"2030-01-01T17:00"}' > "$T/inbox_local/a1.json"
echo '{"action":"add","title":"QC report","assignee":"Dunja","status":"in_progress","project":"work"}' > "$T/drive/a2.json"
echo '{"action":"add","title":"buy milk","due":"2030-01-01T17:00"}' > "$T/inbox_local/a3.json"
$D once | grep -q "added T001 buy toothpaste" || { echo FAIL add; exit 1; }
[ -z "$(ls "$T/inbox_local" "$T/drive" | grep '\.json$' | grep -v status)" ] || { echo FAIL "inbox not emptied"; exit 1; }
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
