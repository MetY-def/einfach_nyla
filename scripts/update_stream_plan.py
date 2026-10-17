from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen
from pathlib import Path
import json, re, calendar

ICS = "https://calendar.google.com/calendar/ical/7a763ec1a63233ec8c0dc0b8c3dcb06e6d1dc0bce8862b89330240bc11b1373c%40group.calendar.google.com/public/basic.ics"
TZ = ZoneInfo("Europe/Berlin")
MONTHS = ["Januar","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"]

def unescape(s):
    return s.replace(r"\n", " ").replace(r"\,", ",").replace(r"\;", ";").replace(r"\\", "\\")

def parse_dt(value, params):
    if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value[:8], "%Y%m%d").replace(tzinfo=TZ)
    if value.endswith("Z"):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=ZoneInfo("UTC")).astimezone(TZ)
    fmt = "%Y%m%dT%H%M%S" if len(value) >= 15 else "%Y%m%dT%H%M"
    dt = datetime.strptime(value, fmt)
    tzid = params.get("TZID")
    try:
        return dt.replace(tzinfo=ZoneInfo(tzid)).astimezone(TZ) if tzid else dt.replace(tzinfo=TZ)
    except Exception:
        return dt.replace(tzinfo=TZ)

def parse_ics(raw):
    unfolded=[]
    for line in raw.replace("\r\n","\n").split("\n"):
        if line.startswith((" ","\t")) and unfolded: unfolded[-1]+=line[1:]
        else: unfolded.append(line)
    events=[]; cur=None
    for line in unfolded:
        if line=="BEGIN:VEVENT": cur={}
        elif line=="END:VEVENT" and cur is not None: events.append(cur);cur=None
        elif cur is not None and ":" in line:
            left,val=line.split(":",1);bits=left.split(";");params={}
            for bit in bits[1:]:
                if "=" in bit:
                    k,v=bit.split("=",1);params[k.upper()]=v.strip('"')
            cur[bits[0].upper()]=(params,unescape(val))
    return events

def main():
    req=Request(ICS,headers={"User-Agent":"DJ-Nyla-Streamplan/1.0"})
    with urlopen(req,timeout=30) as r: raw=r.read().decode("utf-8","replace")
    now=datetime.now(TZ); monday=(now-timedelta(days=now.weekday())).replace(hour=0,minute=0,second=0,microsecond=0); sunday=monday+timedelta(days=7)
    items=[]
    for ev in parse_ics(raw):
        try:
            st=parse_dt(ev["DTSTART"][1],ev["DTSTART"][0])
            endprop=ev.get("DTEND"); en=parse_dt(endprop[1],endprop[0]) if endprop else st+timedelta(hours=1)
            title=ev.get("SUMMARY",({}, "Stream"))[1]
            starts=[]
            if monday<=st<sunday: starts.append(st)
            rule=ev.get("RRULE",({}, ""))[1]
            if rule:
                freqm=re.search(r"(?:^|;)FREQ=([^;]+)",rule); stepm=re.search(r"(?:^|;)INTERVAL=(\d+)",rule)
                freq=freqm.group(1) if freqm else ""; step=int(stepm.group(1)) if stepm else 1
                cur=st
                for _ in range(1000):
                    if cur>=sunday: break
                    if cur>=monday and cur not in starts: starts.append(cur)
                    if freq=="WEEKLY": cur+=timedelta(weeks=step)
                    elif freq=="DAILY": cur+=timedelta(days=step)
                    elif freq=="MONTHLY":
                        m=cur.month-1+step;y=cur.year+m//12;m=m%12+1
                        cur=cur.replace(year=y,month=m,day=min(cur.day,calendar.monthrange(y,m)[1]))
                    else: break
            for s in starts:
                if monday<=s<sunday: items.append((s,s+(en-st),title))
        except Exception: continue
    items=sorted(set(items),key=lambda x:x[0])
    rows=[]
    for i,day in enumerate(["MO","DI","MI","DO","FR","SA","SO"]):
        d=(monday+timedelta(days=i)).date()
        today=[x for x in items if x[0].date()==d]
        if today:
            rows.extend({"day":day,"time":f"{s:%H:%M} – {e:%H:%M}","name":name} for s,e,name in today)
        else: rows.append({"day":day,"time":"","name":"Keine Termine"})
    data={"week":monday.isocalendar().week,"month":f"{MONTHS[monday.month-1]} {monday.year}","rows":rows}
    Path("data").mkdir(exist_ok=True)
    Path("data/stream-plan.json").write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    print("Streamplan-Daten aktualisiert.")
if __name__=="__main__": main()
