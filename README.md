# untildone

A task tracker you drive from a chat window, that nags you every 30 minutes until you tick it off — on your Mac and your iPhone — using nothing you don't already own.

**macOS only** (launchd + Apple Reminders). No accounts, no subscriptions, no dependencies beyond Python 3.8.

```
you (Claude chat, or the `untildone` CLI)  ──writes a small JSON file──▶  daemon.py (runs every minute)
                                                                       │
                          Apple Reminders ◀── "[T012] pay half marathon" ── SQLite
                          (syncs to iPhone;                                (tasks + full status history)
                           tick it = done)
```

## Why

Every "remind me until I actually do it" feature is behind a paywall, and every AI chat forgets you the moment you close the tab. This splits the job in three:

- **The brain** — a chat (Claude) or a CLI turns "Tuesday at 2, pay Damjan" into a precise instruction.
- **The clock** — a tiny Python daemon on your Mac wakes every minute, reads new instructions, decides who needs nagging.
- **The messenger** — Apple Reminders, which is already on your phone, already syncs, and already has a tick-box.

The one rule that makes it work: **nothing ever talks to a running process.** The chat writes a file; the daemon reads it and writes files back. That's the whole contract.

## Install (2 minutes)

```bash
git clone <this repo> ~/untildone && cd ~/untildone
bash install.sh              # asks 4 questions, writes config.json, registers a launchd agent
python3 daemon.py remtest    # grants Reminders access (macOS prompts you), fires one test alert
```

Then:

```bash
./untildone add "buy milk" --due 17:00
./untildone add "QC report" --assignee Dunja --status in_progress --project work
./untildone list
./untildone done milk
```

At 17:00 your Mac notifies you and a `[T001] buy milk` reminder appears on your phone. Every 30 minutes until you tick it — on either device — or say `untildone done milk`.

## Using it from Claude

The point of the project. Paste [`CLAUDE.md`](CLAUDE.md) into a Claude Project's instructions, give Claude Desktop's Filesystem connector access to this folder, and talk normally: *"remind me to pay the half marathon Tuesday at 2"*, *"Dunja is starting the QC report"*, *"what's open?"*, *"done toothpaste"*. Day-to-day phrases and what to expect: [`docs/USER-GUIDE.md`](docs/USER-GUIDE.md). The story in 12 slides: [`docs/untildone-showcase.pdf`](docs/untildone-showcase.pdf).

From a phone, Claude can't touch your files, so it drops the same JSON into a **Google Drive folder** that Google Drive for desktop syncs to your Mac. Set `drive_folder` in `config.json` (or answer the install prompt) and the daemon watches that folder too. Skip it and everything still works laptop-only.

## What the daemon does every minute

1. Ingests every `*.json` in `inbox_local/` (and the Drive folder if configured), in filename order, then deletes it. Nothing accumulates.
2. Reverse-syncs Apple Reminders: anything you ticked on any device → `done`.
3. Nags: every task of yours with `next_nag ≤ now` gets a macOS notification and its Reminders alert re-armed. `next_nag += 30 min`, skipping quiet hours (default 22:00–07:00).
4. Pre-arms: every open task of yours has a Reminders item alerting at its due time, so the first alert fires from the phone's own clock even if the Mac is asleep.
5. Rewrites `status.json` — open tasks by assignee, recent changes, last nags, any failed files.

Tasks assigned to other people are tracked but never nag *you*. Every status change is logged in `status_log`, so "who was on what, from when to when" is a query, and `untildone export --project work` gives you the CSV.

## The mailbox format

One JSON file per command. Missing fields take defaults.

```json
{"action":"add",    "title":"pay half marathon", "due":"2026-09-01T14:00", "project":"personal"}
{"action":"add",    "title":"QC report", "assignee":"Dunja", "status":"in_progress"}
{"action":"add",    "title":"rotate morning tasks", "kind":"idea"}
{"action":"update", "id":"T001", "due":"2026-08-31T15:00"}
{"action":"done",   "id":"toothpaste"}
{"action":"snooze", "id":"T003", "minutes":90}
{"action":"export", "filter":{"project":"work","from":"2026-09-01","to":"2026-09-30"}}
```

`id` may be a task id or a unique fragment of the title. Ambiguous → refused, with the candidates listed in `status.json`. Unparseable files go to `inbox_failed/`, never silently dropped.

## Files

```
daemon.py       the whole thing (stdlib only)
untildone       CLI front end
install.sh      one-time setup + launchd agent      daemonctl   status / stop / start / log / tick
CLAUDE.md       instructions for the chat side     tests/smoke.sh
config.json     yours, gitignored                  tasks.db · status.json · daemon.log   yours, gitignored
```

## Troubleshooting

- **No notification on the Mac** → System Settings → Notifications → *Script Editor* must be allowed, and Focus / Do Not Disturb hides it unless you allow Script Editor in that Focus mode.
- **No reminder on the phone** → run `python3 daemon.py remtest` from Terminal once; macOS will ask *"python3 wants access to control Reminders"*. launchd may ask a second time for the background process. Check `./untildone log` for `REM  bump failed`.
- **`state = running` but nothing happens** → `./daemonctl tick` runs one cycle in the foreground and prints what it did.
- **Mac asleep** → the first alert of each task still reaches the phone (pre-armed). The 30-minute repeats resume when the Mac wakes. On a MacBook: Battery → Options → *Prevent automatic sleeping on power adapter when the display is off*.

## Limits, honestly

- macOS only, because Apple Reminders is the phone transport and AppleScript is how we reach it. A Linux/Raspberry Pi port (systemd + CalDAV to iCloud) is the planned next step.
- Repeats need the Mac awake. Nothing on iOS can run a daemon; the Reminders scheduler is the closest thing, and the pre-arm step uses it.
- It's a single-user database with an assignee column, not a team tool. That's deliberate.

## Privacy

Your tasks live in `tasks.db` in this folder and in your own Apple Reminders. Nothing leaves your machine except the optional Drive mailbox (your own Drive). `.gitignore` and `.githooks/pre-commit` refuse to commit any of it; `install.sh` enables the hook if the folder is a git repo.

MIT — see LICENSE.
