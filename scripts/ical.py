#!/usr/bin/env python3
"""
iCloud CalDAV skill for OpenClaw.
Reads Jon's iCal calendars via Apple's CalDAV server.
Credentials stored in GNOME Keyring — never in files.

Setup:
  secret-tool store --label="iCloud CalDAV" application dae service ical username your@icloud.com
  (enter app-specific password from appleid.apple.com when prompted)

Usage:
  python ical.py calendars                    # list all calendars
  python ical.py events [--days 30]           # upcoming events across all calendars
  python ical.py events --calendar "Personal" # events in a specific calendar
  python ical.py birthdays                    # all birthday/anniversary events
  python ical.py upcoming --days 60           # events needing prep (birthdays, anniversaries, etc.)
  python ical.py status                       # check auth + connectivity
"""

import sys
import os
import json
import subprocess
import urllib.request
import urllib.error
import urllib.parse
import base64
import re
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────
CALDAV_BASE    = "https://caldav.icloud.com"
PRINCIPAL_PATH = "/principals/apple-id/"   # append username
KEYRING_SERVICE = "ical"
KEYRING_APP     = "dae"

# Event types that need prep reminders
PREP_KEYWORDS = [
    "birthday", "anniversary", "wedding", "graduation",
    "mothers day", "father", "christmas", "valentine",
    "women's day", "new year",
]

# Calendars where ALL events are surfaced regardless of keywords
# These get ⭐ treatment and are fully scanned without date filter
PRIORITY_CALENDARS = [
    "jon & rin",
    "jon and rin",
]

# Calendars scanned without date filter (for recurring annual events)
# but NOT given blanket ⭐ priority — only keyword-matched events surface
BIRTHDAY_CALENDARS = [
    "birthday calendar",
    "birthday",
]

# People who deserve real prep tasks (known important people)
# Everyone else gets a lightweight nudge only
KNOWN_IMPORTANT_PEOPLE = [
    "rin", "mozz", "miko", "mom", "art", "dad",
]

# ── Keyring ────────────────────────────────────────────────────────────────────

def get_credentials():
    """Retrieve iCloud credentials from GNOME Keyring."""
    try:
        # Get password via lookup (uses all attributes as a filter)
        result = subprocess.run(
            ["secret-tool", "lookup", "application", KEYRING_APP, "service", KEYRING_SERVICE],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0 or not result.stdout.strip():
            print("ERROR: iCloud credentials not found in keyring.", file=sys.stderr)
            print("Run: secret-tool store --label='iCloud CalDAV' application dae service ical username your@icloud.com", file=sys.stderr)
            sys.exit(1)

        password = result.stdout.strip()

        # Get username — secret-tool search output format varies by distro.
        # Try both known formats: 'attribute.username = X' and 'username = X'
        result2 = subprocess.run(
            ["secret-tool", "search", "--all", "--unlock",
             "application", KEYRING_APP, "service", KEYRING_SERVICE],
            capture_output=True, text=True, timeout=5
        )
        username = None
        for line in result2.stdout.splitlines() + result2.stderr.splitlines():
            line = line.strip()
            # Match: 'attribute.username = foo' or 'username = foo'
            m = re.match(r"(?:attribute\.)?username\s*=\s*(.+)", line, re.IGNORECASE)
            if m:
                username = m.group(1).strip()
                break

        # Fallback: try lookup with username attribute directly
        if not username:
            result3 = subprocess.run(
                ["secret-tool", "search", "application", KEYRING_APP, "service", KEYRING_SERVICE],
                capture_output=True, text=True, timeout=5
            )
            for line in result3.stdout.splitlines() + result3.stderr.splitlines():
                line = line.strip()
                m = re.match(r"(?:attribute\.)?username\s*=\s*(.+)", line, re.IGNORECASE)
                if m:
                    username = m.group(1).strip()
                    break

        if not username:
            print("ERROR: Username not found in keyring entry.", file=sys.stderr)
            print("Debug output:", file=sys.stderr)
            print(result2.stdout[:500], file=sys.stderr)
            print("Re-run: secret-tool store --label='iCloud CalDAV' application dae service ical username your@icloud.com", file=sys.stderr)
            sys.exit(1)

        return username, password

    except FileNotFoundError:
        print("ERROR: secret-tool not found. Install: sudo apt install libsecret-tools", file=sys.stderr)
        sys.exit(1)


def make_auth_header(username, password):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


# ── CalDAV HTTP helpers ────────────────────────────────────────────────────────

def caldav_request(method, url, body=None, headers=None, username=None, password=None):
    """Make a CalDAV request with Basic auth."""
    req_headers = {
        "Authorization": make_auth_header(username, password),
        "Content-Type": "application/xml; charset=utf-8",
        "Depth": "1",
    }
    if headers:
        req_headers.update(headers)

    data = body.encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method, headers=req_headers)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        return e.code, body_text
    except Exception as e:
        print(f"ERROR: Request failed: {e}", file=sys.stderr)
        sys.exit(1)


def discover_principal(username, password):
    """Discover the CalDAV principal URL for this user.
    
    Apple uses numeric IDs, not email addresses. The real principal path
    comes from current-user-principal in a PROPFIND on /.
    e.g. /100864067/principal/
    """
    body = """<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:">
  <d:prop>
    <d:current-user-principal/>
  </d:prop>
</d:propfind>"""

    status, response = caldav_request(
        "PROPFIND", CALDAV_BASE + "/",
        body=body, headers={"Depth": "0"},
        username=username, password=password
    )

    # Apple returns the principal inside <current-user-principal><href>...
    # XML may or may not be namespace-prefixed depending on response
    for pattern in [
        r"<current-user-principal[^>]*>\s*<href[^>]*>([^<]+)</href>",
        r"<d:current-user-principal>\s*<d:href>([^<]+)</d:href>",
    ]:
        m = re.search(pattern, response, re.DOTALL)
        if m:
            path = m.group(1).strip()
            return path if path.startswith("http") else CALDAV_BASE + path

    # Fallback: parse any href that looks like a numeric Apple principal
    m = re.search(r"<href[^>]*>(/\d+/principal/)</href>", response)
    if m:
        return CALDAV_BASE + m.group(1)

    raise RuntimeError(f"Could not discover principal URL. Response:\n{response[:500]}")


def get_calendar_home(principal_url, username, password):
    """Get the calendar home set URL from the principal.
    
    Apple's calendar home is typically /100864067/calendars/
    discovered via calendar-home-set property on the principal.
    """
    body = """<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <cal:calendar-home-set/>
  </d:prop>
</d:propfind>"""

    status, response = caldav_request(
        "PROPFIND", principal_url,
        body=body, headers={"Depth": "0"},
        username=username, password=password
    )

    # Try both namespaced and non-namespaced variants
    for pattern in [
        r"calendar-home-set[^>]*>\s*<[^>]*href[^>]*>([^<]+)</",
        r"<cal:calendar-home-set>\s*<d:href>([^<]+)</d:href>",
    ]:
        m = re.search(pattern, response, re.DOTALL | re.IGNORECASE)
        if m:
            path = m.group(1).strip()
            return path if path.startswith("http") else CALDAV_BASE + path

    # Fallback: derive from principal URL — /100864067/principal/ -> /100864067/calendars/
    m = re.match(r"(https://caldav\.icloud\.com/\d+)/", principal_url)
    if m:
        return m.group(1) + "/calendars/"

    raise RuntimeError(f"Could not discover calendar home. Principal: {principal_url}\nResponse:\n{response[:500]}")


def parse_caldav_response(response, home_url):
    """
    Parse a CalDAV multistatus response into a list of calendar dicts.
    Handles both namespaced (d:response) and bare (response xmlns=DAV:) XML,
    which Apple iCloud uses inconsistently.
    """
    calendars = []

    # Normalise: strip xmlns declarations and namespace prefixes so we can
    # match tags uniformly.  We convert both <d:foo> and <foo xmlns="DAV:"> 
    # to bare <foo> for easier regex matching.
    norm = re.sub(r'\s+xmlns(?::\w+)?=["\'][^"\']*["\']', '', response)
    norm = re.sub(r'<(/?)(\w+):', r'<\1', norm)  # strip prefixes: <d:href> -> <href>

    for resp_block in re.findall(r'<response>(.*?)</response>', norm, re.DOTALL):
        href_match  = re.search(r'<href>([^<]+)</href>', resp_block)
        name_match  = re.search(r'<displayname>([^<]*)</displayname>', resp_block)
        # A calendar resource has <calendar/> inside <resourcetype>
        is_calendar = bool(re.search(r'<resourcetype[^>]*>.*?<calendar', resp_block, re.DOTALL))

        if not (href_match and is_calendar):
            continue

        href = href_match.group(1).strip()
        raw_name = name_match.group(1).strip() if name_match else href.rstrip('/').split('/')[-1]
        # Decode XML entities (e.g. &amp; -> &)
        import html
        name = html.unescape(raw_name)

        # Skip the home collection itself
        home_path = urllib.parse.urlparse(home_url).path.rstrip('/')
        if href.rstrip('/') == home_path:
            continue

        url = href if href.startswith('http') else CALDAV_BASE + href
        if not any(c['url'] == url for c in calendars):
            calendars.append({'name': name, 'url': url})

    return calendars


def list_calendars(home_url, username, password):
    """List all calendars in the home set."""
    body = """<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav" xmlns:cs="http://calendarserver.org/ns/">
  <d:prop>
    <d:displayname/>
    <d:resourcetype/>
    <cal:supported-calendar-component-set/>
    <cs:getctag/>
  </d:prop>
</d:propfind>"""

    for depth in ("1", "infinity"):
        status, response = caldav_request(
            "PROPFIND", home_url, body=body,
            headers={"Depth": depth},
            username=username, password=password
        )
        calendars = parse_caldav_response(response, home_url)
        if calendars:
            return calendars

    return []


# ── iCal parsing ───────────────────────────────────────────────────────────────

def fetch_calendar_events(cal_url, username, password, days_ahead=60):
    """Fetch events from a calendar via CalDAV calendar-query."""
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_ahead)

    start_str = now.strftime("%Y%m%dT%H%M%SZ")
    end_str = end.strftime("%Y%m%dT%H%M%SZ")

    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:getetag/>
    <c:calendar-data/>
  </d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{start_str}" end="{end_str}"/>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""

    status, response = caldav_request(
        "REPORT", cal_url,
        body=body, headers={"Depth": "1"},
        username=username, password=password
    )

    events = []
    # Extract VCALENDAR blocks
    for cal_data in re.findall(r"<.*?calendar-data[^>]*>(.*?)</.*?calendar-data>", response, re.DOTALL):
        # Unescape XML entities
        cal_data = cal_data.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        parsed = parse_ical_events(cal_data)
        events.extend(parsed)

    return events


def parse_ical_events(ical_text):
    """Parse VEVENT blocks from iCal text."""
    events = []

    for vevent in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", ical_text, re.DOTALL):
        event = {}

        def get_prop(name):
            m = re.search(rf"^{name}(?:;[^:]*)?:(.+)$", vevent, re.MULTILINE)
            return m.group(1).strip() if m else ""

        event["summary"] = get_prop("SUMMARY")
        event["description"] = get_prop("DESCRIPTION")
        event["location"] = get_prop("LOCATION")
        event["uid"] = get_prop("UID")

        # Parse DTSTART — handle DATE and DATETIME formats
        dtstart_raw = get_prop("DTSTART")
        event["dtstart_raw"] = dtstart_raw
        event["date"] = parse_ical_date(dtstart_raw)
        event["dtend_raw"] = get_prop("DTEND")
        event["all_day"] = len(dtstart_raw) == 8  # DATE format = all day

        # RRULE for recurring events
        event["rrule"] = get_prop("RRULE")

        if event["summary"] and event["date"]:
            events.append(event)

    return events


def parse_ical_date(date_str):
    """Parse iCal date string to date object."""
    if not date_str:
        return None
    date_str = date_str.strip().split("Z")[0].split("+")[0]
    try:
        if len(date_str) == 8:
            return datetime.strptime(date_str, "%Y%m%d").date()
        elif len(date_str) >= 15:
            return datetime.strptime(date_str[:15], "%Y%m%dT%H%M%S").date()
    except ValueError:
        pass
    return None


def fetch_all_calendar_events_unfiltered(cal_url, username, password):
    """
    Fetch ALL events from a calendar with no date filter.
    Used for birthday/anniversary calendars where events may have
    original dates in the past but recur annually.
    """
    body = """<?xml version="1.0" encoding="UTF-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:getetag/>
    <c:calendar-data/>
  </d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT"/>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""

    status, response = caldav_request(
        "REPORT", cal_url,
        body=body, headers={"Depth": "1"},
        username=username, password=password
    )
    events = []
    for cal_data in re.findall(r"<.*?calendar-data[^>]*>(.*?)</.*?calendar-data>", response, re.DOTALL):
        cal_data = cal_data.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        events.extend(parse_ical_events(cal_data))
    return events


def fetch_all_birthday_events(home_url, username, password):
    """
    Scan all calendars for events needing prep reminders.
    Priority calendars (Birthday calendar, Jon & Rin) are fetched
    without a date filter so past-dated recurring events are included.
    """
    calendars = list_calendars(home_url, username, password)
    seen_uids = set()
    prep_events = []

    for cal in calendars:
        cal_lower = cal["name"].lower()
        if "task" in cal_lower:
            continue

        is_priority_cal  = any(kw in cal_lower for kw in PRIORITY_CALENDARS)
        is_birthday_cal  = any(kw in cal_lower for kw in BIRTHDAY_CALENDARS)

        # Priority + birthday calendars: no date filter (catch past-dated recurring events)
        if is_priority_cal or is_birthday_cal:
            events = fetch_all_calendar_events_unfiltered(cal["url"], username, password)
        else:
            events = fetch_calendar_events(cal["url"], username, password, days_ahead=400)

        for e in events:
            summary_lower = e.get("summary", "").lower()
            is_prep        = any(kw in summary_lower for kw in PREP_KEYWORDS)
            is_important   = any(p in summary_lower for p in KNOWN_IMPORTANT_PEOPLE)

            # Decide whether to include and at what priority level
            if is_priority_cal:
                # Everything on Jon & Rin calendar — always include, always ⭐
                include, priority = True, True
            elif is_birthday_cal and (is_prep or is_important):
                # Birthday calendar — only include keyword/important matches
                include  = True
                priority = is_important  # ⭐ only for known important people
            elif is_prep:
                # Other calendars — keyword match only, no ⭐
                include, priority = True, False
            else:
                include, priority = False, False

            if include and e["uid"] not in seen_uids:
                e["calendar"] = cal["name"]
                e["priority"] = priority
                seen_uids.add(e["uid"])
                prep_events.append(e)

    return prep_events


# ── Prep task generation ───────────────────────────────────────────────────────

def next_annual_occurrence(event_date):
    """Return the next future date for an annual recurring event."""
    today = date.today()
    if event_date is None:
        return None
    try:
        candidate = event_date.replace(year=today.year)
        if candidate < today:
            candidate = event_date.replace(year=today.year + 1)
        return candidate
    except ValueError:
        return None  # Feb 29 in non-leap year


def days_until(event_date):
    """Days until next occurrence of this date (handles annual recurrence)."""
    today = date.today()
    if event_date is None:
        return None
    next_occ = next_annual_occurrence(event_date)
    if next_occ is None:
        return None
    return (next_occ - today).days


def prep_tasks_for_event(event):
    """Generate specific prep tasks based on event type and importance."""
    summary  = event.get("summary", "")
    days     = days_until(event.get("date"))
    priority = event.get("priority", False)
    if days is None or days <= 0:
        return []

    tasks = []
    summary_lower = summary.lower()
    is_important  = priority or any(p in summary_lower for p in KNOWN_IMPORTANT_PEOPLE)

    if "birthday" in summary_lower or "bday" in summary_lower:
        if is_important:
            # Full prep tasks for known important people
            if days <= 21:
                tasks.append(f"🎂 {summary} in {days} days — buy gift")
            if days <= 14:
                tasks.append(f"🎂 {summary} in {days} days — order cake or make reservation")
            if days <= 7:
                tasks.append(f"🎂 {summary} in {days} days — confirm plans")
        else:
            # Lightweight nudge for contacts — just a heads-up
            if days <= 3:
                tasks.append(f"🎂 {summary} tomorrow — send a message?")
            elif days <= 7:
                tasks.append(f"🎂 {summary} in {days} days — worth sending a message")

    elif "anniversary" in summary_lower:
        # Check if it's Jon's own anniversary
        is_own = any(x in summary_lower for x in ["jon", "rin", "our"])
        if is_own or priority:
            if days <= 21:
                tasks.append(f"💍 {summary} in {days} days — plan gift or experience")
            if days <= 14:
                tasks.append(f"💍 {summary} in {days} days — make dinner reservation if applicable")
            if days <= 7:
                tasks.append(f"💍 {summary} in {days} days — confirm everything is sorted")
        # Other people's anniversaries — no task, just display

    elif "valentine" in summary_lower:
        if days <= 21:
            tasks.append(f"💝 Valentine's Day in {days} days — plan gift + any reservations")
        if days <= 10:
            tasks.append(f"💝 Valentine's Day in {days} days — book restaurant NOW if not done")

    elif "women" in summary_lower:
        if days <= 21:
            tasks.append(f"🌸 Women's Day in {days} days — order gift for Rin")
        if days <= 7:
            tasks.append(f"🌸 Women's Day in {days} days — confirm gift is en route")

    elif priority:
        # Everything else on a priority calendar — generic nudge
        if days <= 14:
            tasks.append(f"📅 {summary} in {days} days — any prep needed?")

    return tasks


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_status(args):
    print("=== iCal Skill Status ===\n")
    username, password = get_credentials()
    print(f"  Username: {username}")
    print(f"  Credentials: found in keyring ✓")

    print("\n  Testing CalDAV connection...")
    try:
        principal = discover_principal(username, password)
        print(f"  Principal URL: {principal} ✓")
        home = get_calendar_home(principal, username, password)
        print(f"  Calendar home: {home} ✓")
        cals = list_calendars(home, username, password)
        print(f"  Calendars found: {len(cals)} ✓")
        print("\n✅ iCal connection working.")
    except Exception as e:
        print(f"\n❌ Connection failed: {e}")
        sys.exit(1)


def cmd_calendars(args):
    username, password = get_credentials()
    principal = discover_principal(username, password)
    home = get_calendar_home(principal, username, password)
    cals = list_calendars(home, username, password)

    print(f"\n{'─'*60}")
    print(f"  iCLOUD CALENDARS ({len(cals)} found)")
    print(f"{'─'*60}\n")
    for cal in cals:
        print(f"  📅 {cal['name']}")
        print(f"     {cal['url']}")
        print()


def cmd_events(args):
    days = 30
    calendar_filter = None
    i = 0
    while i < len(args):
        if args[i] == "--days" and i + 1 < len(args):
            days = int(args[i + 1]); i += 2
        elif args[i] == "--calendar" and i + 1 < len(args):
            calendar_filter = args[i + 1]; i += 2
        else:
            i += 1

    username, password = get_credentials()
    principal = discover_principal(username, password)
    home = get_calendar_home(principal, username, password)
    cals = list_calendars(home, username, password)

    if calendar_filter:
        cals = [c for c in cals if calendar_filter.lower() in c["name"].lower()]
        if not cals:
            print(f"No calendar matching '{calendar_filter}' found.")
            return

    all_events = []
    for cal in cals:
        events = fetch_calendar_events(cal["url"], username, password, days_ahead=days)
        for e in events:
            e["calendar"] = cal["name"]
        all_events.extend(events)

    # Sort by date
    all_events.sort(key=lambda e: e.get("date") or date.max)

    today = date.today()
    print(f"\n{'─'*70}")
    print(f"  UPCOMING EVENTS — next {days} days ({len(all_events)} found)")
    print(f"{'─'*70}\n")

    for e in all_events:
        d = e.get("date")
        if d:
            # Advance recurring annual events to next future occurrence
            display_date = next_annual_occurrence(d) if e.get("rrule") or (d < today) else d
            if display_date is None:
                continue
            delta = (display_date - today).days
            when = f"in {delta} days" if delta > 0 else "today" if delta == 0 else f"{abs(delta)} days ago"
            print(f"  📅 {display_date.strftime('%b %d')} ({when}) — {e['summary']}")
            print(f"     Calendar: {e['calendar']}")
            if e.get("location"):
                print(f"     Location: {e['location']}")
            print()


def cmd_upcoming(args):
    """Show events needing prep — birthdays, anniversaries, special occasions."""
    days = 60
    i = 0
    while i < len(args):
        if args[i] == "--days" and i + 1 < len(args):
            days = int(args[i + 1]); i += 2
        else:
            i += 1


    username, password = get_credentials()
    principal = discover_principal(username, password)
    home = get_calendar_home(principal, username, password)

    prep_events = fetch_all_birthday_events(home, username, password)

    today = date.today()
    upcoming = []
    for e in prep_events:
        d = e.get("date")
        if not d:
            continue
        delta = days_until(d)
        if delta is not None and 0 <= delta <= days:
            e["days_until"] = delta
            upcoming.append(e)

    upcoming.sort(key=lambda e: e["days_until"])

    print(f"\n{'─'*70}")
    print(f"  PREP-NEEDED EVENTS — next {days} days")
    print(f"{'─'*70}\n")

    if not upcoming:
        print("  Nothing requiring prep in this window.\n")
        return

    # Sort: priority calendar events first, then by urgency
    upcoming.sort(key=lambda e: (not e.get("priority", False), e["days_until"]))

    for e in upcoming:
        delta = e["days_until"]
        urgency = "🔴" if delta <= 7 else "🟡" if delta <= 14 else "🟢"
        star = "⭐ " if e.get("priority") else ""
        print(f"  {urgency} {star}{e['summary']} — in {delta} days ({e.get('date').strftime('%b %d') if e.get('date') else ''})")
        print(f"     Calendar: {e.get('calendar', 'unknown')}")

        tasks = prep_tasks_for_event(e)
        for t in tasks:
            print(f"     → {t}")
        print()


def cmd_birthdays(args):
    """List all birthday and anniversary events in the next year."""
    username, password = get_credentials()
    principal = discover_principal(username, password)
    home = get_calendar_home(principal, username, password)

    events = fetch_all_birthday_events(home, username, password)
    events.sort(key=lambda e: days_until(e.get("date")) or 999)

    print(f"\n{'─'*70}")
    print(f"  BIRTHDAYS & ANNIVERSARIES ({len(events)} found)")
    print(f"{'─'*70}\n")

    for e in events:
        d = e.get("date")
        delta = days_until(d)
        if delta is not None:
            urgency = "🔴" if delta <= 14 else "🟡" if delta <= 30 else "  "
            print(f"  {urgency} {e['summary']}")
            print(f"     Next: {d.replace(year=date.today().year).strftime('%b %d')} (in {delta} days)")
            print(f"     Calendar: {e.get('calendar', 'unknown')}")
            print()


# ── Entry point ───────────────────────────────────────────────────────────────

def cmd_dump_calendar(args):
    """Dump all events from a specific calendar by name for debugging."""
    if not args:
        print("Usage: python ical.py dump-calendar <name>")
        sys.exit(1)
    cal_name = args[0].lower()
    username, password = get_credentials()
    principal = discover_principal(username, password)
    home = get_calendar_home(principal, username, password)
    cals = list_calendars(home, username, password)

    matches = [c for c in cals if cal_name in c["name"].lower()]
    if not matches:
        print(f"No calendar matching '{cal_name}'. Available:")
        for c in cals:
            print(f"  {c['name']}")
        sys.exit(1)

    for cal in matches:
        print(f"\n=== {cal['name']} ===")
        # Fetch with wide window to catch all events
        events = fetch_calendar_events(cal["url"], username, password, days_ahead=400)
        if not events:
            print("  No events found in next 400 days.")
            # Try fetching without date filter
            body = """<?xml version="1.0" encoding="UTF-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:getetag/><c:calendar-data/></d:prop>
  <c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"/></c:comp-filter></c:filter>
</c:calendar-query>"""
            status, response = caldav_request("REPORT", cal["url"], body=body,
                headers={"Depth": "1"}, username=username, password=password)
            # Extract summaries and dates
            for m in re.finditer(r"SUMMARY:(.+)", response):
                print(f"  SUMMARY: {m.group(1).strip()}")
            for m in re.finditer(r"DTSTART[^:]*:(.+)", response):
                print(f"  DTSTART: {m.group(1).strip()}")
        else:
            for e in events:
                print(f"  [{e.get('date')}] {e.get('summary')} (rrule: {e.get('rrule', 'none')})")


def cmd_debug(args):
    """Dump raw CalDAV responses to diagnose calendar discovery."""
    username, password = get_credentials()
    print(f"Username: {username}")

    propfind_body = """<?xml version="1.0" encoding="UTF-8"?>
<d:propfind xmlns:d="DAV:" xmlns:cal="urn:ietf:params:xml:ns:caldav">
  <d:prop>
    <d:current-user-principal/>
    <d:displayname/>
    <d:resourcetype/>
    <cal:calendar-home-set/>
  </d:prop>
</d:propfind>"""

    # Step 1: hit root
    print("\n=== STEP 1: PROPFIND / (Depth 0) ===")
    status, resp = caldav_request("PROPFIND", CALDAV_BASE + "/",
        body=propfind_body, headers={"Depth": "0"}, username=username, password=password)
    print(f"Status: {status}")
    print(resp[:3000])

    # Step 2: hit principal URL
    principal = discover_principal(username, password)
    print(f"\n=== STEP 2: Principal = {principal} ===")
    status, resp = caldav_request("PROPFIND", principal,
        body=propfind_body, headers={"Depth": "0"}, username=username, password=password)
    print(f"Status: {status}")
    print(resp[:3000])

    # Step 3: try calendar-home-set from principal response
    home = get_calendar_home(principal, username, password)
    print(f"\n=== STEP 3: Calendar home = {home} ===")
    status, resp = caldav_request("PROPFIND", home,
        body=propfind_body, headers={"Depth": "1"}, username=username, password=password)
    print(f"Status: {status}")
    print(resp[:3000])


COMMANDS = {
    "status": cmd_status,
    "calendars": cmd_calendars,
    "events": cmd_events,
    "upcoming": cmd_upcoming,
    "birthdays": cmd_birthdays,
    "debug": cmd_debug,
    "dump-calendar": cmd_dump_calendar,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("Usage: python ical.py <command> [args]")
        print("Commands:", ", ".join(COMMANDS.keys()))
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])
