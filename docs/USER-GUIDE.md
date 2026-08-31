# untildone — user guide

You talk. It writes a file. A daemon reads it. Your phone nags you until you tick the box. That's the whole loop; this page is how to talk to it.

## Where to talk

- **Claude on your laptop** (Claude Desktop): just say it. Claude writes to the daemon's local inbox; it's picked up within a minute.
- **Claude on your phone**: same words. Claude drops the command in your Drive folder; it reaches the Mac within a minute or two.
- **The terminal**: `./untildone add "buy milk" --due 17:00` — same thing without the chat.
- **The Reminders app**: tick the box. That's the only thing you *have* to do there.

## Saying things

| You say | What happens |
|---|---|
| *remind me to buy toothpaste at 5* | task created, due today 17:00 |
| *pay the race entry tomorrow 2pm* | task, due tomorrow 14:00 |
| *book the dentist* | task, no time, no nagging — it just sits in the list |
| *idea: rotate morning tasks* | captured as an idea, never nags |
| *Sam is starting the QC report* | task assigned to Sam, in progress, project *work* |
| *the QC report is waiting on the vendor* | status → waiting |
| *move toothpaste to 3pm* | due changed; phone reminder moves with it |
| *done toothpaste* / *did it* / *paid* | task closed, reminder disappears |
| *snooze the race entry for an hour* | quiet for 60 minutes, then back |
| *what's open?* / *what's Sam on?* | Claude reads the live status and tells you |
| *export work tasks for September* | CSV lands in `exports/` (and Drive if configured) |
| *clean up reminders* | the Reminders list is rebuilt to match the database |

Times are forgiving: `5`, `17:00`, `tomorrow 2pm`, `Tuesday 9`, `2026-09-02` all work. A bare date means 09:00. If Claude isn't sure which task you mean, it asks; otherwise it acts and shows you one line you can correct.

## What you'll see

- **Mac:** a notification at the due time, then every 30 minutes.
- **iPhone:** the task appears in the Reminders app, list **Claude**, as `[T012] title` with its alert already set. It buzzes at the due time; every 30 minutes the same item re-alerts. No duplicates.
- **After you tick it:** gone from Reminders within a minute; marked done in the database with a timestamp.
- **Quiet hours** (22:00–07:00 by default): nothing buzzes. A task due at 23:00 first nags at 07:00.

## Rules of the road

- **Only your tasks nag you.** Tasks assigned to other people are tracked, listed, exportable — silent.
- **No due time, no nagging.** A task without a time is a note in the list. Give it a time when it becomes real.
- **Tick or tell — either works.** Ticking in Reminders on any device closes the task. Saying "done" in chat also clears the phone.
- **Don't rename items in the Reminders app.** The daemon matches them by name (`[T012] …`). Renaming orphans them; *clean up reminders* fixes it.
- **Mac asleep:** the first alert on each task still fires from the phone. The 30-minute repeats resume when the Mac wakes.

## When something looks off

| Symptom | Likely | Do |
|---|---|---|
| Nothing on the Mac at due time | Focus / Do Not Disturb, or Script Editor not allowed in Notifications | System Settings → Notifications → Script Editor; allow it in your Focus mode |
| Nothing on the phone | Reminders permission not granted to the daemon | `python3 daemon.py remtest` once in Terminal; allow the prompt |
| Reminders list has items that aren't tasks | something else wrote to the list | say *clean up reminders* |
| "what's open?" is stale on the phone | Mac asleep — `status.json` older than a few minutes | wake the Mac; commands you sent are queued, not lost |
| Claude says it wrote a file but nothing happened | daemon not running | `./daemonctl status`; if not running, `bash install.sh` |

## Under the hood (one paragraph)

`daemon.py` wakes every minute, ingests any `*.json` in `inbox_local/` and the Drive mailbox, applies it to `tasks.db` (SQLite; every status change is logged), re-arms Apple Reminders items so alerts fire on the phone, marks tasks done that were ticked on any device, and rewrites `status.json`. Claude never talks to the process — only to files. That's why the same design will move to a Raspberry Pi unchanged.
