"""Refresh cinemas.json: every BookMyShow cinema in the supported cities, so typed theatre names
("mani square", "south city") can be checked before a movie lists any shows.

Run now and then:  uv run cinemas.py
If BookMyShow refuses the script, open the URL below in a browser, save it, and run
uv run cinemas.py saved.json

Or scan what is showing now (the movie list page + one showtimes request per movie and format,
2 s apart), which also collects every format and screen name into screens.json:
uv run cinemas.py --scan bengaluru [saved-movie-list-page.html]
"""
import json
import re
import sys
import time

import requests

from chat import CITIES, UA

REGION_CODES = {"bengaluru": "BANG", "kolkata": "KOLK", "mumbai": "MUMBAI", "delhi-ncr": "NCR",
                "chennai": "CHEN", "hyderabad": "HYD", "pune": "PUNE", "kochi": "KOCH"}
URL = "https://in.bookmyshow.com/serv/getData?cmd=QUICKBOOK&type=MT"


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            data = json.load(f)
    else:
        r = requests.get(URL, headers={"User-Agent": UA}, timeout=60)
        r.raise_for_status()
        data = r.json()
    venues = data["cinemas"]["BookMyShow"]["aiVN"]["venues"]
    out = {city: sorted({v["VenueName"].strip() for v in venues if v.get("RegionCode") == REGION_CODES[city]})
           for city in CITIES}
    with open("cinemas.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print({c: len(v) for c, v in out.items()})


def scan(city, saved_page=None):
    """Theatres, formats and screens of every movie on the city's movie list page; merged into
    cinemas.json (nothing removed) and screens.json. If BookMyShow refuses the page, save
    https://in.bookmyshow.com/explore/movies-<city> from a browser and pass the file."""
    import main as checker
    if saved_page:
        with open(saved_page, encoding="utf-8") as f:
            text = f.read()
    else:
        page = requests.get(f"https://in.bookmyshow.com/explore/movies-{city}", headers={"User-Agent": UA}, timeout=30)
        page.raise_for_status()
        text = page.text
    codes = list(dict.fromkeys(re.findall(r"ET\d{8}", text)))
    region = checker.resolve_region(city)
    venues, formats, screens, asked = set(), set(), set(), set()
    for code in codes:
        first = checker.fetch_bms(code, "", *region)
        time.sleep(2)
        if not first:
            continue
        for fcode, fmt in checker.parse_formats(first, code):
            if fcode in asked:
                continue
            asked.add(fcode)
            data = first if fcode == code else checker.fetch_bms(fcode, "", *region)
            if fcode != code:
                time.sleep(2)
            if not data:
                continue
            if fmt:
                formats.add(fmt)
            for s in checker.parse_shows(data):
                venues.add(s.venue_name.strip())
                if s.screen_attr:
                    screens.add(s.screen_attr.strip())
    with open("cinemas.json", encoding="utf-8") as f:
        known = json.load(f)
    added = sorted(venues - set(known.get(city, [])))
    known[city] = sorted(set(known.get(city, [])) | venues)
    with open("cinemas.json", "w", encoding="utf-8") as f:
        json.dump(known, f, indent=1, ensure_ascii=False)
        f.write("\n")
    try:
        with open("screens.json", encoding="utf-8") as f:
            seen = json.load(f)
    except FileNotFoundError:
        seen = {}
    old = seen.get(city, {})
    seen[city] = {"formats": sorted(set(old.get("formats", [])) | formats),
                  "screens": sorted(set(old.get("screens", [])) | screens)}
    with open("screens.json", "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=1, ensure_ascii=False)
        f.write("\n")
    print(f"{len(codes)} movies, {len(asked)} format events, {len(venues)} theatres showing "
          f"({len(added)} new), {len(formats)} formats, {len(screens)} screens")
    for v in added:
        print("  new:", v)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--scan"]:
        scan(sys.argv[2] if len(sys.argv) > 2 else "bengaluru", sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        main()
