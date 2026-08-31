# Changelog

## 0.99.0 — 2026-08-31 (pre-release)

First public cut. Built in one day; everything below is live on one Mac and has closed real tasks.

**Works**
- Mailbox protocol: one JSON file per command (`add`, `update`, `done`, `snooze`, `export`, `cleanup`), ingested every 60 s from a local inbox and an optional Google Drive folder, deleted after ingest.
- SQLite store with full status history (`status_log`) for per-person, per-period reporting; CSV export. `__version__` reported in `status.json`.
- Nag loop: macOS notification + Apple Reminders re-arm every 30 min until done; quiet hours; snooze.
- Pre-arming: every open task is on the phone with its due-time alert from creation, so the first alert fires even if the Mac sleeps.
- Reverse sync: tick in Reminders on any device → task done; closing via chat clears the phone.
- `untildone` CLI, `install.sh` (launchd agent, config prompts, retires legacy agent), `daemonctl`, smoke test that uses its own Reminders list (`Claude-TEST`) and never touches yours.
- `CLAUDE.md`: the chat-side protocol, with prerequisites.
- Docs: README with hero graphic and 30-second usage table, `USER-GUIDE.md`, 12-slide deck (PPTX for presenting, PDF for reading on GitHub).

**Known limits (why this is 0.99, not 1.0)**
- macOS only. Linux/Raspberry Pi port (systemd + CalDAV) is the next milestone.
- 30-minute repeats need the Mac awake.
- No dashboard yet; `untildone list` / `status.json` are the read interface.
- Phone→Mac round trip via Drive is designed and wired but not yet exercised end-to-end.
- `untildone list` doesn't distinguish ideas from tasks.

**Not planned**
- Multi-writer / team collaboration. One person writes; assignees are labels.
