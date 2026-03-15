---
name: ical
description: Read Jon's iCloud calendars via CalDAV. Use when Jon asks about upcoming events, birthdays, anniversaries, or anything on his personal calendar. Also use during heartbeat checks to surface prep-needed events (birthdays, anniversaries, special occasions) within the next 60 days and generate actionable To Do tasks with appropriate lead time. Jon's life is primarily in iCal, not Outlook — use this skill for personal calendar queries.
---

# iCal Skill

Reads Jon's iCloud calendars via Apple's CalDAV server. Credentials stored in
GNOME Keyring — never in files.

**Skill root:** `~/.openclaw/workspace/skills/ical/`
**Script:** `scripts/ical.py`
**Auth:** App-specific password in GNOME Keyring

## Setup (one-time)

1. Go to appleid.apple.com → Sign In → App-Specific Passwords
2. Generate a password labeled "Dae-CalDAV"
3. Store in keyring:
```bash
secret-tool store --label="iCloud CalDAV" application dae service ical username your@icloud.com
# Enter the app-specific password when prompted
```

## Usage

```bash
# Check connection and auth
python scripts/ical.py status

# List all calendars
python scripts/ical.py calendars

# Upcoming events (default 30 days)
python scripts/ical.py events
python scripts/ical.py events --days 60
python scripts/ical.py events --calendar "Personal"

# Birthdays and anniversaries (next 12 months)
python scripts/ical.py birthdays

# Events needing prep — with urgency + task suggestions (default 60 days)
python scripts/ical.py upcoming
python scripts/ical.py upcoming --days 90
```

## Heartbeat Workflow

During heartbeat checks, run `upcoming` to surface events needing prep:

```bash
python scripts/ical.py upcoming --days 60
```

For each event returned with urgency 🔴 (≤7 days) or 🟡 (≤14 days):
1. Create a To Do task in the `tasks` list with the suggested prep action
2. Message Jon via WhatsApp if it's 🔴 and no task already exists for it
3. Use 🟢 (≤21 days) as a gentle nudge — surface in next heartbeat summary, no WhatsApp

**Don't nag.** Check whether a task for this event already exists before creating another.
One task per event per reminder window.

## ADHD-Aware Task Generation

Jon has ADHD and needs tasks that are specific and completable, not vague.

❌ Bad: "Prepare for Rin's anniversary"
✅ Good: "Order Tiffany Elsa Peretti bracelet — ships in 3-5 days, order by [date]"

❌ Bad: "Get birthday gift for Mozz"
✅ Good: "Buy Mozz birthday gift — check his wishlist or ask Miko. Order by Jan 5 for Jan 12."

When generating tasks:
- Include a concrete action verb (order, book, call, buy)
- Include a deadline or "order by" date based on typical shipping/prep time
- Keep it to one sentence — completable in a single sitting
- If Dae knows the person's preferences (Rin → Tiffany, Mozz → age-appropriate toy/game), include a specific suggestion

## Priority Calendars

All events on these calendars are treated as prep-needed regardless of keywords:
- `Jon & Rin` — everything on this calendar matters. Surface all upcoming events.

Birthday calendar contacts are scanned for keyword/important-person matches only.
Known important people: Rin, Mozz, Miko, Mom, Art, Dad.

## Key Dates Already Known

These are already in USER.md — cross-reference when generating tasks:

| Person | Date | Notes |
|--------|------|-------|
| Rin | September 1 | Birthday. If apart, coordinate delivery to HCMC + video call. |
| Rin + Jon | March 23 | Anniversary — day they met in Vietnam 2024. |
| Mozz | January 12 | Birthday. Coordinate with custody schedule. |
| Rin | March 8 | International Women's Day. Non-negotiable. |
| Everyone | February 14 | Valentine's Day. Plan around any events. |

## Reminder Lead Times

| Lead time | Action |
|-----------|--------|
| 21 days | Create To Do task with specific prep action |
| 14 days | WhatsApp nudge if task not marked done |
| 7 days | WhatsApp nudge — more urgent tone |
| 3 days | WhatsApp nudge — "last chance" tone |
| Day-of | Nothing — too late, don't rub it in |

Morning reminders preferred (before 10am ET) — Jon's meds are active, he's sharp.
Never remind after 6pm — he's burnt after work.

## Error Handling

- Auth failure → tell Jon to regenerate app-specific password at appleid.apple.com
- Network timeout → retry once, then surface error
- Calendar not found → list available calendars and ask Jon to clarify
