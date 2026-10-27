"""Erzeugt data/stream-plan.json mit ausschließlich Terminen der aktuellen Woche (Mo–So).

Quelle ist der öffentliche Google-Kalender. Die Datei wird von GitHub Actions stündlich neu
erzeugt. Vergangene Termine und Termine aus anderen Wochen werden nicht ausgegeben.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen
from pathlib import Path
import json
import re
import calendar

ICS = "https://calendar.google.com/calendar/ical/7a763ec1a63233ec8c0dc0b8c3dcb06e6d1dc0bce8862b89330240bc11b1373c%40group.calendar.google.com/public/basic.ics"
TZ = ZoneInfo("Europe/Berlin")
MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]
DAYS = ["MO", "DI", "MI", "DO", "FR", "SA", "SO"]
DAY_CODES = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def unescape(value):
    return value.replace(r"\n", " ").replace(r"\N", " ").replace(r"\,", ",").replace(r"\;", ";").replace(r"\\", "\\")


def parse_property(line):
    left, value = line.split(":", 1)
    bits = left.split(";")
    params = {}
    for bit in bits[1:]:
        if "=" in bit:
            key, val = bit.split("=", 1)
            params[key.upper()] = val.strip('"')
    return bits[0].upper(), params, unescape(value)


def parse_dt(value, params):
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value[:8], "%Y%m%d").replace(tzinfo=TZ)
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ)
    fmt = "%Y%m%dT%H%M%S" if len(value) >= 15 else "%Y%m%dT%H%M"
    dt = datetime.strptime(value, fmt)
    try:
        return dt.replace(tzinfo=ZoneInfo(params["TZID"])).astimezone(TZ) if params.get("TZID") else dt.replace(tzinfo=TZ)
    except Exception:
        return dt.replace(tzinfo=TZ)


def parse_ics(raw):
    lines = []
    for line in raw.replace("\r\n", "\n").split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    events, current = [], None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT" and current is not None:
            events.append(current)
            current = None
        elif current is not None and ":" in line:
            key, params, value = parse_property(line)
            current.setdefault(key, []).append((params, value))
    return events


def first(event, key, default=None):
    return event[key][0] if key in event else default


def generate_occurrences(start, rule):
    rule_parts = dict(part.split("=", 1) for part in rule.split(";") if "=" in part)
    freq = rule_parts.get("FREQ", "")
    interval = max(1, int(rule_parts.get("INTERVAL", "1") or 1))
    if freq == "WEEKLY":
        days = sorted(DAY_CODES[d[-2:]] for d in rule_parts.get("BYDAY", "").split(",") if d[-2:] in DAY_CODES) or [start.weekday()]
        week_start = (start - timedelta(days=start.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        index = 0
        while True:
            current_week = week_start + timedelta(weeks=interval * index)
            for day in days:
                occurrence = (current_week + timedelta(days=day)).replace(hour=start.hour, minute=start.minute, second=start.second)
                if occurrence >= start:
                    yield occurrence
            index += 1
    elif freq == "DAILY":
        index = 0
        while True:
            yield start + timedelta(days=interval * index)
            index += 1
    elif freq == "MONTHLY":
        index = 0
        while True:
            month_index = start.month - 1 + interval * index
            year = start.year + month_index // 12
            month = month_index % 12 + 1
            try:
                yield start.replace(year=year, month=month)
            except ValueError:
                pass
            index += 1
    elif freq == "YEARLY":
        index = 0
        while True:
            try:
                yield start.replace(year=start.year + interval * index)
            except ValueError:
                pass
            index += 1
    else:
        yield start


def weekly_occurrences(start, rule, monday, next_monday):
    parts = dict(part.split("=", 1) for part in rule.split(";") if "=" in part)
    count = int(parts["COUNT"]) if parts.get("COUNT") else None
    until = parse_dt(parts["UNTIL"], {}) if parts.get("UNTIL") else None
    if until and len(parts["UNTIL"]) == 8:
        until += timedelta(days=1)
    occurrences, index = [], 0
    for occurrence in generate_occurrences(start, rule):
        index += 1
        if (until and occurrence > until) or (count and index > count) or occurrence >= next_monday or index > 20000:
            break
        if occurrence >= monday:
            occurrences.append(occurrence)
    return occurrences


def build_plan(raw, now):
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    next_monday = monday + timedelta(days=7)
    events = parse_ics(raw)

    # Einzeltermine mit RECURRENCE-ID ersetzen die entsprechende Serieninstanz.
    replaced = {}
    for event in events:
        recurrence_id = first(event, "RECURRENCE-ID")
        if recurrence_id:
            uid = first(event, "UID", ({}, ""))[1]
            replaced.setdefault(uid, set()).add(parse_dt(recurrence_id[1], recurrence_id[0]))

    items = {}

    def add(start, end, title, all_day):
        title = title.strip() or "Stream"
        if title.isupper() and " " not in title and len(title) > 4:
            title = title.capitalize()
        if all_day:
            day = start.date()
            last_day = (end.date() - timedelta(days=1)) if end and end > start else day
            while day <= max(last_day, start.date()):
                if monday.date() <= day < next_monday.date():
                    items[(day, "", title.casefold())] = (day, None, None, title)
                day += timedelta(days=1)
        elif monday <= start < next_monday:
            # Schlüssel verhindert identische doppelte Einträge am selben Tag zur selben Zeit.
            items[(start.date(), start.strftime("%H:%M"), title.casefold())] = (start.date(), start, end, title)

    for event in events:
        try:
            if first(event, "STATUS", ({}, ""))[1].upper() == "CANCELLED" or "DTSTART" not in event:
                continue
            start_params, start_value = first(event, "DTSTART")
            start = parse_dt(start_value, start_params)
            all_day = start_params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", start_value) is not None
            end_prop = first(event, "DTEND")
            end = parse_dt(end_prop[1], end_prop[0]) if end_prop else start + (timedelta(days=1) if all_day else timedelta(hours=1))
            title = first(event, "SUMMARY", ({}, "Stream"))[1]
            uid = first(event, "UID", ({}, ""))[1]
            rule = first(event, "RRULE", ({}, ""))[1]
            if rule and not first(event, "RECURRENCE-ID"):
                excluded = set(replaced.get(uid, set()))
                for params, value in event.get("EXDATE", []):
                    for excluded_value in value.split(","):
                        if excluded_value.strip():
                            excluded.add(parse_dt(excluded_value.strip(), params))
                for occurrence in weekly_occurrences(start, rule, monday, next_monday):
                    if occurrence not in excluded:
                        add(occurrence, occurrence + (end - start), title, all_day)
            else:
                add(start, end, title, all_day)
        except Exception as error:
            print("Termin übersprungen:", error)

    rows = []
    for index, day_code in enumerate(DAYS):
        date = (monday + timedelta(days=index)).date()
        todays = sorted((item for item in items.values() if item[0] == date), key=lambda item: (item[1] is None, item[1] or now))
        free_rows = [item for item in todays if item[1] is None and re.search(r"frei", item[3], re.I)]
        todays = [item for item in todays if item not in free_rows]
        for _, start, end, title in todays:
            rows.append({"day": day_code, "time": f"{start:%H:%M} – {end:%H:%M}" if start else "", "name": title})
        if free_rows:
            rows.append({"day": day_code, "time": "", "name": "Streamfrei"})
        if not todays and not free_rows:
            rows.append({"day": day_code, "time": "", "name": "Keine Termine"})

    return {"week": monday.isocalendar().week, "month": f"{MONTHS[monday.month - 1]} {monday.year}", "rows": rows}


def main():
    request = Request(ICS, headers={"User-Agent": "Nyla-Streamplan/1.0"})
    with urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8", "replace")
    data = build_plan(raw, datetime.now(TZ))
    output = Path("data/stream-plan.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Streamplan aktualisiert: KW {data['week']}, {data['month']}")
    for row in data["rows"]:
        print(f"  {row['day']} {row['time']:<15} {row['name']}")


if __name__ == "__main__":
    main()
