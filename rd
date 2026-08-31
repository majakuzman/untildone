#!/usr/bin/env python3
"""rd — command-line front end for reminder_daemon. Same mailbox the chat uses.

  rd add "buy milk" [--due 17:00 | 2026-09-02 | 2026-09-02T14:00] [--project work]
                    [--assignee Dunja] [--status in_progress] [--idea] [--notes "..."]
  rd done <id-or-title-fragment>
  rd update <id> [--due ...] [--status ...] [--assignee ...] [--project ...] [--title ...]
  rd snooze <id> [--minutes 60]
  rd export [--project work] [--assignee Dunja] [--from 2026-09-01] [--to 2026-09-30]
  rd list                      open tasks, from status.json
  rd log [n]                   last n daemon log lines

Every command writes one JSON file to inbox_local/ and runs one daemon tick so you
see the result immediately (the background agent would have picked it up within a minute anyway).
"""
import argparse
import json
import os
import random
import string
import subprocess
import sys
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
INBOX = os.path.join(HERE, "inbox_local")
DAEMON = os.path.join(HERE, "daemon.py")


def norm_due(s):
    """'17:00' -> today (or tomorrow if past); 'tomorrow 14:00'; else pass through."""
    if not s:
        return None
    s = s.strip().lower()
    now = datetime.now()
    if s.startswith("tomorrow"):
        rest = s[len("tomorrow"):].strip() or "09:00"
        return (now + timedelta(days=1)).strftime("%Y-%m-%d") + "T" + rest
    if len(s) <= 5 and ":" in s:
        h, m = s.split(":")
        t = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
        if t < now:
            t += timedelta(days=1)
        return t.strftime("%Y-%m-%dT%H:%M")
    return s


def drop(cmd):
    os.makedirs(INBOX, exist_ok=True)
    tag = "".join(random.choices(string.ascii_lowercase, k=4))
    name = f"{datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}_{cmd['action']}_{tag}.json"
    cmd["source"] = "cli"
    with open(os.path.join(INBOX, name), "w") as fh:
        json.dump(cmd, fh)
    out = subprocess.run([sys.executable, DAEMON, "once"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if name in line:
            print(line.split(": ", 1)[1] if ": " in line else line)
            return
    print("queued (daemon will process within a minute)")


def cmd_list():
    p = os.path.join(HERE, "status.json")
    try:
        st = json.load(open(p))
    except FileNotFoundError:
        print("no status.json yet — run install.sh or `python3 daemon.py once`")
        return
    if not st["open_count"]:
        print("nothing open")
        return
    for who, rows in st["open"].items():
        print(f"{who} ({len(rows)})")
        for r in rows:
            due = (r["due"] or "")[:16].replace("T", " ")
            print(f"  {r['id']}  {r['title']:<48} {r['status']:<12} {due}")
    if st.get("last_nags"):
        print("last nags:", ", ".join(st["last_nags"]))


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("action")
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--due"); ap.add_argument("--project"); ap.add_argument("--assignee")
    ap.add_argument("--status"); ap.add_argument("--notes"); ap.add_argument("--title")
    ap.add_argument("--minutes", type=int); ap.add_argument("--from", dest="from_"); ap.add_argument("--to")
    ap.add_argument("--idea", action="store_true")
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print(__doc__); return
    a = ap.parse_args()
    ref = " ".join(a.rest)
    opt = {k: v for k, v in dict(project=a.project, assignee=a.assignee, status=a.status, notes=a.notes, title=a.title).items() if v}
    if a.due:
        opt["due"] = norm_due(a.due)
    if a.action == "add":
        if not ref:
            sys.exit("add needs a title")
        c = {"action": "add", "title": ref, **opt}
        if a.idea:
            c["kind"] = "idea"
        drop(c)
    elif a.action == "done":
        drop({"action": "done", "id": ref})
    elif a.action == "update":
        drop({"action": "update", "id": ref, **opt})
    elif a.action == "snooze":
        drop({"action": "snooze", "id": ref, "minutes": a.minutes or 60})
    elif a.action == "export":
        f = {k: v for k, v in dict(project=a.project, assignee=a.assignee).items() if v}
        if a.from_: f["from"] = a.from_
        if a.to: f["to"] = a.to
        drop({"action": "export", "filter": f})
    elif a.action == "list":
        cmd_list()
    elif a.action == "log":
        n = int(ref) if ref.isdigit() else 20
        subprocess.run(["tail", "-n", str(n), os.path.join(HERE, "daemon.log")])
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
