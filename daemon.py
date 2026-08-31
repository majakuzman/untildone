#!/usr/bin/env python3
"""reminder_daemon — a task tracker you drive from a chat (or a CLI), that nags you until done.
macOS only for now (launchd + Apple Reminders). See README.md; the Claude protocol is in CLAUDE.md.

Usage:
  python3 daemon.py init     create DB + folders, then exit
  python3 daemon.py once     one tick (ingest + nag + status), then exit
  python3 daemon.py run      loop forever, one tick per minute (launchd target)
  python3 daemon.py status   print status.json to stdout
  python3 daemon.py remtest  create one test Reminder (grants permission, tests phone)

Step-1 scope: ingest from inbox folders, SQLite, status.json, macOS notifications.
Step 2 (Apple Reminders bridge) implemented. Dashboard server (step 4) pending.
"""
import glob
import json
import os
import random
import shutil
import sqlite3
import string
import subprocess
import sys
import time
from datetime import datetime, timedelta

# ------------------------------------------------------- paths / config ---
# HOME is the folder this file lives in (override with RD_HOME for tests).
# Everything user-specific comes from HOME/config.json, written by install.sh:
#   {"drive_folder": "/path/to/synced/claude_daemon" | "",   # "" = no phone mailbox
#    "quiet_hours": [22, 7], "nag_every_min": 30, "reminders_list": "Claude"}
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HOME = os.environ.get("RD_HOME", SCRIPT_DIR)


def _load_config():
    p = os.path.join(HOME, "config.json")
    try:
        with open(p) as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}


CFG = _load_config()
DRIVE = os.environ.get("RD_DRIVE", CFG.get("drive_folder", "") or "")
DB_PATH = os.path.join(HOME, "tasks.db")
STATUS_LOCAL = os.path.join(HOME, "status.json")
LOG_PATH = os.path.join(HOME, "daemon.log")
INBOX_LOCAL = os.path.join(HOME, "inbox_local")
INBOX_FAILED = os.path.join(HOME, "inbox_failed")
EXPORTS = os.path.join(HOME, "exports")
DRIVE_INBOX = os.path.join(DRIVE, "inbox")
DRIVE_OUTBOX = os.path.join(DRIVE, "outbox")

TICK_SECONDS = 60
QUIET_HOURS = tuple(CFG.get("quiet_hours", [22, 7]))  # no nags from 22:00 to 07:00
DEFAULT_NAG_MIN = int(CFG.get("nag_every_min", 30))
OPEN_STATUSES = ("not_started", "in_progress", "waiting")
ALL_STATUSES = OPEN_STATUSES + ("done", "dropped")


# ------------------------------------------------------------- helpers ---
def now():
    return datetime.now()


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S") if dt else None


def parse_dt(s):
    """Accept ISO 'YYYY-MM-DDTHH:MM[:SS]' or 'YYYY-MM-DD' (-> 09:00)."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            if fmt == "%Y-%m-%d":
                dt = dt.replace(hour=9)
            return dt
        except ValueError:
            continue
    raise ValueError(f"bad datetime: {s!r}")


def log(msg):
    line = f"{iso(now())} {msg}\n"
    with open(LOG_PATH, "a") as f:
        f.write(line)
    if os.path.getsize(LOG_PATH) > 1_000_000:
        shutil.move(LOG_PATH, LOG_PATH + ".1")


def in_quiet_hours(dt):
    start, end = QUIET_HOURS
    h = dt.hour
    return h >= start or h < end


def next_allowed(dt):
    """If dt falls in quiet hours, push to end of quiet window."""
    if not in_quiet_hours(dt):
        return dt
    end_h = QUIET_HOURS[1]
    day = dt if dt.hour < end_h else dt + timedelta(days=1)
    return day.replace(hour=end_h, minute=0, second=0)


def notify(title, body):
    """macOS notification. Silently no-op if osascript is unavailable."""
    safe_t = title.replace('"', "'")
    safe_b = body.replace('"', "'")
    script = f'display notification "{safe_b}" with title "{safe_t}" sound name "Glass"'
    if not HAS_OSA:
        return
    try:
        subprocess.run(["osascript", "-e", script], timeout=5, capture_output=True)
    except Exception as e:  # noqa
        log(f"notify failed: {e}")


# ------------------------------------------------------------------ db ---
SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id            TEXT PRIMARY KEY,
  title         TEXT NOT NULL,
  notes         TEXT,
  kind          TEXT NOT NULL DEFAULT 'task',
  project       TEXT NOT NULL DEFAULT 'personal',
  assignee      TEXT NOT NULL DEFAULT 'me',
  status        TEXT NOT NULL DEFAULT 'not_started',
  due           TEXT,
  nag_every_min INTEGER DEFAULT 30,
  next_nag      TEXT,
  reminder_id   TEXT,
  source        TEXT,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  done_at       TEXT
);
CREATE TABLE IF NOT EXISTS status_log (
  task_id     TEXT NOT NULL,
  from_status TEXT,
  to_status   TEXT NOT NULL,
  changed_at  TEXT NOT NULL,
  note        TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO meta VALUES ('next_id', '1')")
        cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)")]
        if "reminder_synced" not in cols:
            conn.execute("ALTER TABLE tasks ADD COLUMN reminder_synced TEXT")


def new_id(conn):
    n = int(conn.execute("SELECT value FROM meta WHERE key='next_id'").fetchone()[0])
    conn.execute("UPDATE meta SET value=? WHERE key='next_id'", (str(n + 1),))
    return f"T{n:03d}"


def log_status(conn, task_id, from_s, to_s, note=None):
    conn.execute(
        "INSERT INTO status_log VALUES (?,?,?,?,?)",
        (task_id, from_s, to_s, iso(now()), note),
    )


def resolve(conn, ref):
    """Resolve an id or fuzzy title to exactly one task row. Raise if ambiguous."""
    if not ref:
        raise ValueError("no id/title given")
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (ref.upper(),)).fetchone()
    if row:
        return row
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status IN (?,?,?) AND lower(title) LIKE ? ORDER BY created_at DESC",
        (*OPEN_STATUSES, f"%{ref.lower()}%"),
    ).fetchall()
    if len(rows) == 1:
        return rows[0]
    if not rows:
        raise ValueError(f"no open task matches {ref!r}")
    opts = ", ".join(f"{r['id']} {r['title']}" for r in rows[:5])
    raise ValueError(f"ambiguous {ref!r}: {opts}")


# ------------------------------------------------------------- actions ---
def act_add(conn, cmd):
    t = now()
    kind = cmd.get("kind", "task")
    status = cmd.get("status", "not_started")
    if status not in ALL_STATUSES:
        raise ValueError(f"bad status {status}")
    due = parse_dt(cmd.get("due"))
    assignee = cmd.get("assignee", "me")
    nag_min = int(cmd.get("nag_every_min", DEFAULT_NAG_MIN))
    next_nag = iso(next_allowed(due)) if (due and assignee == "me" and kind == "task") else None
    tid = new_id(conn)
    conn.execute(
        """INSERT INTO tasks (id,title,notes,kind,project,assignee,status,due,nag_every_min,
           next_nag,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (tid, cmd["title"].strip(), cmd.get("notes"), kind, cmd.get("project", "personal"),
         assignee, status, iso(due), nag_min, next_nag, cmd.get("source"), iso(t), iso(t)),
    )
    log_status(conn, tid, None, status, cmd.get("note"))
    return f"added {tid} {cmd['title']} [{assignee}/{status}]" + (f" due {iso(due)}" if due else "")


def act_update(conn, cmd):
    row = resolve(conn, cmd.get("id") or cmd.get("title"))
    fields, vals = [], []
    for k in ("title", "notes", "kind", "project", "assignee", "nag_every_min"):
        if k in cmd:
            fields.append(f"{k}=?")
            vals.append(cmd[k])
    if "due" in cmd:
        due = parse_dt(cmd["due"])
        fields += ["due=?", "next_nag=?"]
        vals += [iso(due), iso(next_allowed(due)) if due else None]
    msg = f"updated {row['id']}"
    if "status" in cmd and cmd["status"] != row["status"]:
        s = cmd["status"]
        if s not in ALL_STATUSES:
            raise ValueError(f"bad status {s}")
        fields.append("status=?")
        vals.append(s)
        if s in ("done", "dropped"):
            fields += ["done_at=?", "next_nag=?"]
            vals += [iso(now()), None]
        log_status(conn, row["id"], row["status"], s, cmd.get("note"))
        msg += f" {row['status']}->{s}"
    if not fields:
        return f"nothing to update on {row['id']}"
    fields.append("updated_at=?")
    vals.append(iso(now()))
    vals.append(row["id"])
    conn.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", vals)
    return msg


def act_done(conn, cmd):
    cmd = dict(cmd, status="done")
    return act_update(conn, cmd)


def act_snooze(conn, cmd):
    row = resolve(conn, cmd.get("id") or cmd.get("title"))
    mins = int(cmd.get("minutes", 60))
    nn = now() + timedelta(minutes=mins)
    conn.execute("UPDATE tasks SET next_nag=?, updated_at=? WHERE id=?", (iso(nn), iso(now()), row["id"]))
    return f"snoozed {row['id']} until {iso(nn)}"


def act_export(conn, cmd):
    import csv
    f = cmd.get("filter", {})
    where, vals = [], []
    if f.get("project"):
        where.append("t.project=?"); vals.append(f["project"])
    if f.get("assignee"):
        where.append("t.assignee=?"); vals.append(f["assignee"])
    if f.get("from"):
        where.append("t.created_at>=?"); vals.append(f["from"])
    if f.get("to"):
        where.append("t.created_at<=?"); vals.append(f["to"] + "T23:59:59")
    sql = "SELECT * FROM tasks t" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY created_at"
    rows = conn.execute(sql, vals).fetchall()
    os.makedirs(EXPORTS, exist_ok=True)
    tag = "_".join(str(v) for v in f.values()) or "all"
    name = f"{now().strftime('%Y%m%d_%H%M')}_{tag}.csv"
    path = os.path.join(EXPORTS, name)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(rows[0].keys() if rows else ["id"])
        for r in rows:
            w.writerow(list(r))
    if os.path.isdir(DRIVE):
        os.makedirs(DRIVE_OUTBOX, exist_ok=True)
        shutil.copy(path, os.path.join(DRIVE_OUTBOX, name))
    return f"exported {len(rows)} rows to exports/{name}"


ACTIONS = {"add": act_add, "update": act_update, "done": act_done, "snooze": act_snooze, "export": act_export}

# -------------------------------------------------------------- ingest ---
def inbox_dirs():
    dirs = [INBOX_LOCAL]
    if os.path.isdir(DRIVE):
        dirs += [DRIVE_INBOX, DRIVE]  # root watched too during the first weeks
    return [d for d in dirs if os.path.isdir(d)]


def ingest(conn):
    results = []
    files = []
    for d in inbox_dirs():
        files += [p for p in glob.glob(os.path.join(d, "*.json")) if os.path.basename(p) != "status.json"]
    for path in sorted(files, key=os.path.basename):
        name = os.path.basename(path)
        try:
            with open(path) as fh:
                cmd = json.load(fh)
            action = cmd.get("action", "add")
            if action not in ACTIONS:
                raise ValueError(f"unknown action {action}")
            if action == "add" and not cmd.get("title"):
                raise ValueError("add needs a title")
            msg = ACTIONS[action](conn, cmd)
            conn.commit()
            os.remove(path)
            results.append({"file": name, "ok": True, "msg": msg})
            log(f"OK   {name}: {msg}")
        except Exception as e:  # noqa
            conn.rollback()
            os.makedirs(INBOX_FAILED, exist_ok=True)
            try:
                shutil.move(path, os.path.join(INBOX_FAILED, name))
            except Exception:
                pass
            results.append({"file": name, "ok": False, "msg": str(e)})
            log(f"FAIL {name}: {e}")
    return results


# ----------------------------------------------------------------- nag ---
def nag_pass(conn):
    t = now()
    fired = []
    if in_quiet_hours(t):
        return fired
    rows = conn.execute(
        "SELECT * FROM tasks WHERE next_nag IS NOT NULL AND next_nag<=? AND status IN (?,?,?)",
        (iso(t), *OPEN_STATUSES),
    ).fetchall()
    for r in rows:
        due_txt = r["due"][11:16] if r["due"] else ""
        notify(f"[{r['id']}] {r['title']}", f"due {due_txt} — say 'done {r['id']}' or 'snooze {r['id']}'")
        rid = reminders_bump(r)
        nn = next_allowed(t + timedelta(minutes=r["nag_every_min"] or DEFAULT_NAG_MIN))
        conn.execute("UPDATE tasks SET next_nag=?, reminder_id=COALESCE(?, reminder_id), reminder_synced=NULL WHERE id=?", (iso(nn), rid, r["id"]))
        log(f"NAG  {r['id']} {r['title']} (next {iso(nn)})")
        fired.append(r["id"])
    conn.commit()
    return fired


# ------------------------------------------------- apple reminders ---
REM_LIST = CFG.get("reminders_list", "Claude")
REM_LEAD_SEC = 60   # alert time = now + this, so iCloud has time to sync before it fires


HAS_OSA = shutil.which("osascript") is not None  # False on Linux: Reminders bridge becomes a no-op


def osa(script, timeout=25):
    if not HAS_OSA:
        raise RuntimeError("osascript not available (not macOS)")
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "osascript failed")
    return r.stdout.strip()


def _q(s):
    return (s or "").replace("\\", "\\\\").replace('"', '\\"')


def rem_title(row):
    return f"[{row['id']}] {row['title']}"


def _asdate(dt):
    """AppleScript snippet building a date object, locale-independent."""
    return (f"(current date)" if dt is None else
            f"my mkdate({dt.year}, {dt.month}, {dt.day}, {dt.hour}, {dt.minute})")


MKDATE = '''
on mkdate(y, m, d, h, mi)
  set t to current date
  set day of t to 1
  set year of t to y
  set month of t to m
  set day of t to d
  set hours of t to h
  set minutes of t to mi
  set seconds of t to 0
  return t
end mkdate
'''


def reminders_bump(row, alert_at=None):
    """Create the Reminders item for this task, or re-arm its alert.
    alert_at=None -> now + REM_LEAD_SEC (a nag). Otherwise the given datetime (pre-arm at due)."""
    if not HAS_OSA:
        return None
    title = _q(rem_title(row))
    body = _q(f"due {(row['due'] or '-')[:16].replace('T', ' ')} | say 'done {row['id']}' or 'snooze {row['id']}' to Claude")
    when = f"(current date) + {REM_LEAD_SEC}" if alert_at is None else _asdate(alert_at)
    script = MKDATE + f'''
    tell application "Reminders"
      if not (exists list "{REM_LIST}") then make new list with properties {{name:"{REM_LIST}"}}
      set L to list "{REM_LIST}"
      set nowD to {when}
      set rs to (reminders of L whose name is "{title}" and completed is false)
      if (count of rs) > 0 then
        set r to item 1 of rs
        set remind me date of r to nowD
      else
        tell L to set r to make new reminder with properties {{name:"{title}", body:"{body}", remind me date:nowD}}
      end if
      return id of r
    end tell'''
    try:
        rid = osa(script)
        log(f"REM  bumped {row['id']}")
        return rid
    except Exception as e:  # noqa
        log(f"REM  bump failed {row['id']}: {e}")
        return None


def reminders_reverse_sync(conn):
    """Tasks whose Reminders item was ticked (on any device) -> done. Completed items are deleted."""
    if not HAS_OSA:
        return []
    script = f'''
    tell application "Reminders"
      if not (exists list "{REM_LIST}") then return ""
      set out to ""
      set rs to (reminders of list "{REM_LIST}" whose completed is true)
      repeat with r in rs
        set out to out & (name of r) & linefeed
        delete r
      end repeat
      return out
    end tell'''
    try:
        names = [n for n in osa(script).split("\n") if n.strip()]
    except Exception as e:  # noqa
        log(f"REM  reverse sync failed: {e}")
        return []
    done = []
    for n in names:
        if n.startswith("[") and "]" in n:
            tid = n[1:n.index("]")]
            row = conn.execute("SELECT * FROM tasks WHERE id=? AND status IN (?,?,?)", (tid, *OPEN_STATUSES)).fetchone()
            if row:
                act_update(conn, {"id": tid, "status": "done", "note": "completed in Reminders"})
                conn.execute("UPDATE tasks SET reminder_id=NULL WHERE id=?", (tid,))
                done.append(tid)
                log(f"REM  {tid} completed on device -> done")
    conn.commit()
    return done


def reminders_arm(conn):
    """Pre-arm: each open task of mine with a next_nag gets a Reminders item alerting at that time.
    Means the phone sees the whole list, and the first alert fires even if the Mac is asleep."""
    if not HAS_OSA:
        return []
    rows = conn.execute(
        "SELECT * FROM tasks WHERE assignee='me' AND kind='task' AND next_nag IS NOT NULL "
        "AND status IN (?,?,?) AND (reminder_synced IS NULL OR reminder_synced != next_nag)", OPEN_STATUSES
    ).fetchall()
    for r in rows:
        rid = reminders_bump(r, parse_dt(r["next_nag"]))
        if rid:
            conn.execute("UPDATE tasks SET reminder_id=?, reminder_synced=? WHERE id=?", (rid, r["next_nag"], r["id"]))
    conn.commit()


def reminders_close(conn):
    """Tasks closed via chat/dashboard still have a Reminders item -> remove it."""
    if not HAS_OSA:
        return []
    rows = conn.execute("SELECT * FROM tasks WHERE reminder_id IS NOT NULL AND status IN ('done','dropped')").fetchall()
    for r in rows:
        title = _q(rem_title(r))
        script = f'''
        tell application "Reminders"
          if exists list "{REM_LIST}" then
            delete (reminders of list "{REM_LIST}" whose name is "{title}")
          end if
        end tell'''
        try:
            osa(script)
            conn.execute("UPDATE tasks SET reminder_id=NULL WHERE id=?", (r["id"],))
            log(f"REM  removed item for closed {r['id']}")
        except Exception as e:  # noqa
            log(f"REM  close failed {r['id']}: {e}")
    conn.commit()


# -------------------------------------------------------------- status ---
def write_status(conn, ingest_results=None, fired=None):
    open_rows = conn.execute(
        "SELECT id,title,kind,project,assignee,status,due,next_nag,created_at FROM tasks "
        "WHERE status IN (?,?,?) ORDER BY assignee, due IS NULL, due, created_at", OPEN_STATUSES
    ).fetchall()
    by_assignee = {}
    for r in open_rows:
        by_assignee.setdefault(r["assignee"], []).append(dict(r))
    recent = [dict(r) for r in conn.execute(
        "SELECT l.task_id, t.title, l.from_status, l.to_status, l.changed_at "
        "FROM status_log l JOIN tasks t ON t.id=l.task_id ORDER BY l.changed_at DESC LIMIT 10"
    ).fetchall()]
    status = {
        "generated_at": iso(now()),
        "open_count": len(open_rows),
        "open_by_assignee": {k: len(v) for k, v in by_assignee.items()},
        "open": by_assignee,
        "recent_changes": recent,
        "last_ingest": ingest_results or [],
        "last_nags": fired or [],
        "id_hint": "refer to tasks by id (T012) or a unique title fragment",
    }
    txt = json.dumps(status, indent=1, ensure_ascii=False)
    with open(STATUS_LOCAL, "w") as fh:
        fh.write(txt)
    if os.path.isdir(DRIVE):
        os.makedirs(DRIVE_OUTBOX, exist_ok=True)
        tmp = os.path.join(DRIVE_OUTBOX, ".status.tmp")
        with open(tmp, "w") as fh:
            fh.write(txt)
        os.replace(tmp, os.path.join(DRIVE_OUTBOX, "status.json"))
    return status


def housekeeping():
    if not os.path.isdir(DRIVE_OUTBOX):
        return
    cutoff = time.time() - 24 * 3600
    for p in glob.glob(os.path.join(DRIVE_OUTBOX, "*.csv")):
        if os.path.getmtime(p) < cutoff:
            os.remove(p)


# ---------------------------------------------------------------- main ---
def ensure_dirs():
    for d in (HOME, INBOX_LOCAL, EXPORTS):
        os.makedirs(d, exist_ok=True)
    if os.path.isdir(DRIVE):
        for d in (DRIVE_INBOX, DRIVE_OUTBOX):
            os.makedirs(d, exist_ok=True)


def tick():
    with db() as conn:
        res = ingest(conn)
        reminders_reverse_sync(conn)
        reminders_close(conn)
        fired = nag_pass(conn)
        reminders_arm(conn)
        write_status(conn, res, fired)
    housekeeping()
    return res, fired


def main():
    try:
        import signal
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # quiet `daemon.py once | head`
    except (ImportError, AttributeError):
        pass
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    ensure_dirs()
    init_db()
    if mode == "init":
        with db() as conn:
            write_status(conn)
        print(f"initialised {DB_PATH}")
    elif mode == "once":
        res, fired = tick()
        for r in res:
            print(("OK   " if r["ok"] else "FAIL ") + r["file"] + ": " + r["msg"])
        if fired:
            print("nagged: " + ", ".join(fired))
        print(f"status -> {STATUS_LOCAL}")
    elif mode == "run":
        log("daemon start")
        while True:
            try:
                tick()
            except Exception as e:  # noqa
                log(f"tick error: {e}")
            time.sleep(TICK_SECONDS)
    elif mode == "remtest":
        # triggers the macOS Automation permission prompt and proves the phone path
        fake = {"id": "T000", "title": "test from daemon (tick me on phone)", "due": iso(now())}
        rid = reminders_bump(fake)
        print("created reminder" if rid else "FAILED - see daemon.log")
    elif mode == "status":
        with open(STATUS_LOCAL) as fh:
            print(fh.read())
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
