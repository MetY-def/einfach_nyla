"""Erzeugt data/stream-plan.json (nur die aktuelle Woche Mo-So) aus dem öffentlichen Google-Kalender.

Beachtet: Ende von Serien (UNTIL/COUNT), gelöschte Einzeltermine (EXDATE), geänderte/abgesagte
Einzeltermine (RECURRENCE-ID, STATUS:CANCELLED), mehrere Wochentage (BYDAY) und ganztägige Termine.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen
from pathlib import Path
import json, re, calendar

ICS = "https://calendar.google.com/calendar/ical/7a763ec1a63233ec8c0dc0b8c3dcb06e6d1dc0bce8862b89330240bc11b1373c%40group.calendar.google.com/public/basic.ics"
TZ = ZoneInfo("Europe/Berlin")
MONTHS = ["Januar","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"]
DAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def unescape(s):
    return s.replace(r"\n", " ").replace(r"\N", " ").replace(r"\,", ",").replace(r"\;", ";").replace(r"\\", "\\")


def is_date_only(value, params):
    return params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value) is not None


def parse_dt(value, params):
    if is_date_only(value, params):
        return datetime.strptime(value[:8], "%Y%m%d").replace(tzinfo=TZ)
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ)
    dt = datetime.strptime(value, "%Y%m%dT%H%M%S" if len(value) >= 15 else "%Y%m%dT%H%M")
    tzid = params.get("TZID")
    try:
        return dt.replace(tzinfo=ZoneInfo(tzid)).astimezone(TZ) if tzid else dt.replace(tzinfo=TZ)
    except Exception:
        return dt.replace(tzinfo=TZ)


def parse_ics(raw):
    """Liefert eine Liste von Terminen; jede Eigenschaft ist eine Liste von (Parameter, Wert)."""
    unfolded = []
    for line in raw.replace("\r\n", "\n").split("\n"):
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)
    events, cur = [], None
    for line in unfolded:
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT" and cur is not None:
            events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            left, val = line.split(":", 1)
            bits = left.split(";")
            params = {}
            for bit in bits[1:]:
                if "=" in bit:
                    k, v = bit.split("=", 1)
                    params[k.upper()] = v.strip('"')
            cur.setdefault(bits[0].upper(), []).append((params, unescape(val)))
    return events


def first(ev, key, default=None):
    return ev[key][0] if key in ev else default


def generate(st, rule):
    """Alle Wiederholungen einer Serie in zeitlicher Reihenfolge (ohne Ende-Prüfung)."""
    r = dict(p.split("=", 1) for p in rule.split(";") if "=" in p)
    freq = r.get("FREQ", "")
    step = int(r.get("INTERVAL", "1") or 1)
    if freq == "WEEKLY":
        days = sorted(DAYS[d[-2:]] for d in r.get("BYDAY", "").split(",") if d[-2:] in DAYS) or [st.weekday()]
        base = (st - timedelta(days=st.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        k = 0
        while True:
            wk = base + timedelta(weeks=step * k)
            for d in days:
                o = (wk + timedelta(days=d)).replace(hour=st.hour, minute=st.minute, second=st.second)
                if o >= st:
                    yield o
            k += 1
    elif freq == "DAILY":
        k = 0
        while True:
            yield st + timedelta(days=step * k)
            k += 1
    elif freq in ("MONTHLY", "YEARLY"):
        k = 0
        while True:
            m = st.month - 1 + (step * k if freq == "MONTHLY" else 0)
            y = st.year + m // 12 + (step * k if freq == "YEARLY" else 0)
            try:
                yield st.replace(year=y, month=m % 12 + 1)
            except ValueError:
                pass
            k += 1
    else:
        yield st


def week_occurrences(st, rule, monday, sunday):
    r = dict(p.split("=", 1) for p in rule.split(";") if "=" in p)
    count = int(r["COUNT"]) if r.get("COUNT") else None
    until = None
    if r.get("UNTIL"):
        until = parse_dt(r["UNTIL"], {})
        if len(r["UNTIL"]) == 8:
            until += timedelta(days=1)
    out, n = [], 0
    for o in generate(st, rule):
        n += 1
        if (until and o > until) or (count and n > count) or o >= sunday or n > 20000:
            break
        if o >= monday:
            out.append(o)
    return out


def build_plan(raw, now):
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    sunday = monday + timedelta(days=7)
    events = parse_ics(raw)

    # Geänderte oder abgesagte Einzeltermine einer Serie: die Original-Wiederholung wird ersetzt
    replaced = {}
    for ev in events:
        rid = first(ev, "RECURRENCE-ID")
        if rid:
            uid = first(ev, "UID", ({}, ""))[1]
            replaced.setdefault(uid, set()).add(parse_dt(rid[1], rid[0]))

    items = {}

    def add(st, en, title, allday):
        title = title.strip() or "Stream"
        if title.isupper() and " " not in title and len(title) > 4:
            title = title.capitalize()
        if allday:
            d, last = st.date(), (en.date() - timedelta(days=1)) if en and en > st else st.date()
            last = max(last, d)
            while d <= last:
                if monday.date() <= d < sunday.date():
                    items[(d, "", title.lower())] = (d, None, None, title)
                d += timedelta(days=1)
        elif monday <= st < sunday:
            items[(st.date(), st.strftime("%H:%M"), title.lower())] = (st.date(), st, en, title)

    for ev in events:
        try:
            if first(ev, "STATUS", ({}, ""))[1].upper() == "CANCELLED" or "DTSTART" not in ev:
                continue
            sp, sv = first(ev, "DTSTART")
            st = parse_dt(sv, sp)
            allday = is_date_only(sv, sp)
            ep = first(ev, "DTEND")
            en = parse_dt(ep[1], ep[0]) if ep else st + (timedelta(days=1) if allday else timedelta(hours=1))
            title = first(ev, "SUMMARY", ({}, "Stream"))[1]
            uid = first(ev, "UID", ({}, ""))[1]
            rule = first(ev, "RRULE", ({}, ""))[1]
            if rule and not first(ev, "RECURRENCE-ID"):
                excluded = set(replaced.get(uid, set()))
                for params, val in ev.get("EXDATE", []):
                    for v in val.split(","):
                        if v.strip():
                            excluded.add(parse_dt(v.strip(), params))
                for o in week_occurrences(st, rule, monday, sunday):
                    if o not in excluded:
                        add(o, o + (en - st), title, allday)
            else:
                add(st, en, title, allday)
        except Exception as exc:  # ein kaputter Termin soll nicht alles stoppen
            print("Termin übersprungen:", exc)

    rows = []
    for i, day in enumerate(["MO", "DI", "MI", "DO", "FR", "SA", "SO"]):
        d = (monday + timedelta(days=i)).date()
        todays = sorted((x for x in items.values() if x[0] == d), key=lambda x: (x[1] is None, x[1] or now))
        free = [x for x in todays if x[1] is None and re.search("frei", x[3], re.I)]
        todays = [x for x in todays if x not in free]
        for s, e, name in [(x[1], x[2], x[3]) for x in todays]:
            rows.append({"day": day, "time": f"{s:%H:%M} – {e:%H:%M}" if s else "", "name": name})
        if free:
            rows.append({"day": day, "time": "", "name": "Streamfrei"})
        if not todays and not free:
            rows.append({"day": day, "time": "", "name": "Keine Termine"})
    return {"week": monday.isocalendar().week, "month": f"{MONTHS[monday.month - 1]} {monday.year}", "rows": rows}


def main():
    req = Request(ICS, headers={"User-Agent": "DJ-Nyla-Streamplan/1.0"})
    with urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8", "replace")
    data = build_plan(raw, datetime.now(TZ))
    Path("data").mkdir(exist_ok=True)
    Path("data/stream-plan.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Streamplan-Daten aktualisiert: KW {data['week']}, {data['month']}")
    for row in data["rows"]:
        print(f"  {row['day']}  {row['time']:<15} {row['name']}")


if __name__ == "__main__":
    main()
