---
name: ical
description: Read iCloud calendars via CalDAV. Query events, birthdays, anniversaries; surface prep-needed events with actionable tasks. Jon's primary personal calendar.
---

# iCal Skill

Reads iCloud calendars via CalDAV. Credentials in GNOME Keyring.

**Script:** `scripts/ical.py`

## Setup (one-time)

1. Generate app-specific password at appleid.apple.com
2. Store: `secret-tool store --label="iCloud CalDAV" application dae service ical username your@icloud.com`

## Usage

```bash
python scripts/ical.py status                     # Check connection
python scripts/ical.py calendars                   # List all calendars
python scripts/ical.py events [--days 60] [--calendar "Personal"]
python scripts/ical.py birthdays                   # Next 12 months
python scripts/ical.py upcoming [--days 90]        # Events needing prep (urgency + task suggestions)
```

## Heartbeat Workflow

Run `upcoming --days 60`. For events with urgency 🔴 (≤7d) or 🟡 (≤14d): create a To Do task if none exists. WhatsApp Jon for 🔴 only. 🟢 (≤21d) = surface in heartbeat summary.

One task per event per reminder window — don't nag.

## ADHD-Aware Task Generation

Tasks must be specific and completable, not vague:
- ❌ "Prepare for Rin's anniversary"
- ✅ "Order Tiffany Elsa Peretti bracelet — ships 3-5 days, order by [date]"

Rules: concrete action verb, deadline/order-by date, one sentence, include specific suggestions when preferences are known.

## Priority Calendars

- `Jon & Rin` — surface all events (everything matters)
- Birthday calendar — scan for important people only: Rin, Mozz, Miko, Mom, Art, Dad

Key dates in USER.md — cross-reference when generating tasks.

## Reminder Lead Times

21d → create task | 14d → WhatsApp if undone | 7d → urgent WhatsApp | 3d → "last chance" | day-of → nothing

Remind before 10am ET (meds active). Never after 6pm.

## Errors

Auth failure → regenerate app-specific password at appleid.apple.com. Network timeout → retry once. Calendar not found → list calendars and clarify.
