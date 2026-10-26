"""Refresh cinemas.json: every BookMyShow cinema in the supported cities, so typed theatre names
("mani square", "south city") can be checked before a movie lists any shows.

Run now and then:  uv run cinemas.py
If BookMyShow refuses the script, open the URL below in a browser, save it, and run
uv run cinemas.py saved.json
"""
import json
import sys

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


if __name__ == "__main__":
    main()
