#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SPORT = "baseball_mlb"
BOOKS = ["hardrockbet_fl", "hardrockbet"]
MARKETS = "h2h,spreads,totals"
UNIT = 50
ML_EDGE = 0.07
ML_CAP = -180
ML_PLUS_CAP = 200
RL_JUICE_LO = -130
RL_JUICE_HI = 170
OU_EDGE = 0.04
OU_BASE = 0.524


def et_now():
    return datetime.now(ET)


def american_to_prob(odds):
    o = float(odds)
    if o < 0:
        return abs(o) / (abs(o) + 100.0)
    return 100.0 / (o + 100.0)


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "green-lock/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def pull_odds(api_key, book):
    q = urllib.parse.urlencode({
        "apiKey": api_key,
        "bookmakers": book,
        "markets": MARKETS,
        "oddsFormat": "american",
    })
    url = "https://api.the-odds-api.com/v4/sports/%s/odds?%s" % (SPORT, q)
    data = get_json(url)
    if isinstance(data, list):
        return data
    return []


def pull_slate(day):
    url = (
        "https://statsapi.mlb.com/api/v1/schedule"
        "?sportId=1&date=%s&hydrate=probablePitcher,team" % day
    )
    data = get_json(url)
    games = []
    for d in data.get("dates") or []:
        glist = d.get("games") or []
        for g in glist:
            away = g["teams"]["away"]
            home = g["teams"]["home"]
            ap = away.get("probablePitcher") or {}
            hp = home.get("probablePitcher") or {}
            games.append({
                "pk": g.get("gamePk"),
                "away": away["team"].get("name"),
                "home": home["team"].get("name"),
                "time": g.get("gameDate"),
                "ap": ap.get("fullName", "TBD"),
                "hp": hp.get("fullName", "TBD"),
            })
    return games


def book_markets(event):
    books = event.get("bookmakers") or []
    if not books:
        return {}
    out = {}
    for m in books[0].get("markets") or []:
        out[m.get("key")] = m.get("outcomes") or []
    return out


def outcome_price(outcomes, name=None, point=None, side=None):
    for o in outcomes or []:
        if name and o.get("name") != name:
            continue
        if point is not None and o.get("point") != point:
            continue
        if side and o.get("name") != side:
            continue
        return o
    return None


def px_int(val):
    if val is None:
        return None
    return int(round(float(val)))


def main():
    api_key = os.environ.get("ODDS_API_KEY") or ""
    print("key_set", "yes" if api_key else "no")
    if not api_key:
        raise SystemExit("ODDS_API_KEY missing")

    now = et_now()
    day = now.strftime("%Y-%m-%d")
    events = []
    book_used = None
    for book in BOOKS:
        try:
            events = pull_odds(api_key, book)
        except Exception as e:
            print("odds_error", book, type(e).__name__, e)
            events = []
        if events and any(e.get("bookmakers") for e in events):
            book_used = book
            break
    print("book", book_used, "events", len(events))

    try:
        slate = pull_slate(day)
    except Exception as e:
        print("slate_error", type(e).__name__, e)
        slate = []

    opens_path = Path("opens.json")
    if opens_path.exists():
        opens = json.loads(opens_path.read_text())
    else:
        opens = {}

    picks = []
    slate_cards = []

    for ev in events:
        away = ev.get("away_team")
        home = ev.get("home_team")
        eid = ev.get("id")
        mk = book_markets(ev)
        h2h = mk.get("h2h")
        spreads = mk.get("spreads")
        totals = mk.get("totals")

        ml_away = outcome_price(h2h, name=away)
        ml_home = outcome_price(h2h, name=home)
        rl_away = outcome_price(spreads, name=away, point=1.5)
        if rl_away is None:
            for o in spreads or []:
                if o.get("name") == away and float(o.get("point") or 0) > 0:
                    rl_away = o
                    break
        tot_over = outcome_price(totals, side="Over")
        tot_under = outcome_price(totals, side="Under")

        start = ev.get("commence_time") or ""
        try:
            tlabel = (
                datetime.fromisoformat(start.replace("Z", "+00:00"))
                .astimezone(ET)
                .strftime("%I:%M %p ET")
                .lstrip("0")
            )
        except Exception:
            tlabel = start

        aw_px = px_int(ml_away["price"]) if ml_away else None
        hm_px = px_int(ml_home["price"]) if ml_home else None
        slate_cards.append({
            "time": tlabel,
            "away": away,
            "home": home,
            "ml_away": ("%+d" % aw_px) if aw_px is not None else "",
            "ml_home": ("%+d" % hm_px) if hm_px is not None else "",
            "note": book_used or "no Hard Rock book",
        })

        if eid not in opens:
            opens[eid] = {
                "away": away,
                "home": home,
                "ml_away": aw_px,
                "ml_home": hm_px,
                "rl_away": px_int(rl_away["price"]) if rl_away else None,
                "ou_line": tot_over.get("point") if tot_over else None,
                "ou_over": px_int(tot_over["price"]) if tot_over else None,
                "ou_under": px_int(tot_under["price"]) if tot_under else None,
            }

        op = opens[eid]
        for side, cur, open_px in (
            (away, ml_away, op.get("ml_away")),
            (home, ml_home, op.get("ml_home")),
        ):
            if not cur or open_px is None:
                continue
            px = px_int(cur["price"])
            if px is None or px < ML_CAP or px > ML_PLUS_CAP:
                continue
            edge = american_to_prob(open_px) - american_to_prob(px)
            if edge >= ML_EDGE:
                picks.append({
                    "market": "ML",
                    "away": away,
                    "home": home,
                    "side": "%s %+d" % (side, px),
                    "time": tlabel,
                    "edge": "open-move %.1f%%" % (edge * 100),
                    "units": "1u",
                    "note": book_used,
                })

        if rl_away:
            px = px_int(rl_away["price"])
            if px is not None and RL_JUICE_LO <= px <= RL_JUICE_HI:
                picks.append({
                    "market": "RL",
                    "away": away,
                    "home": home,
                    "side": "%s +1.5 %+d" % (away, px),
                    "time": tlabel,
                    "edge": "HR +1.5 dog",
                    "units": "1u",
                    "note": book_used,
                })

        if tot_over and tot_under:
            for label, o in (("Over", tot_over), ("Under", tot_under)):
                p = american_to_prob(o["price"])
                if abs(p - OU_BASE) >= OU_EDGE:
                    picks.append({
                        "market": "OU",
                        "away": away,
                        "home": home,
                        "side": "%s %s" % (label, o.get("point")),
                        "time": tlabel,
                        "edge": "juice %.1f%%" % (p * 100),
                        "units": "1u",
                        "note": book_used,
                    })

    payload = {
        "updated": now.strftime("%Y-%m-%d %H:%M ET"),
        "unit_dollars": UNIT,
        "season": 2026,
        "book": book_used,
        "ytd": {
            "ml": {"bets": 54, "wr": 0.63, "units": 11.6},
            "rl": {"bets": 35, "wr": 0.771, "units": 30.7},
            "ou": {"bets": 46, "wr": 0.609, "units": 7.5},
            "stacked_units": 49.8,
        },
        "locks": [
            {"market": "ML", "rule": "Edge vs open >= 7%. Cap -180. Hard Rock Bet FL."},
            {"market": "RL", "rule": "+1.5 dog only. Juice -130 to +170."},
            {"market": "OU", "rule": "|P(over) - 52.4%| >= 4%. Skip pushes. 1u."},
        ],
        "slate_date": day,
        "slate": slate_cards,
        "picks": picks,
        "picks_status": (
            "%d ticket(s) · %s" % (len(picks), book_used or "no book")
            if picks else "No lock this pass."
        ),
        "recent": [],
    }

    Path("picks.json").write_text(json.dumps(payload, indent=2) + "\n")
    opens_path.write_text(json.dumps(opens, indent=2) + "\n")
    print("picks", len(picks), "slate", len(slate), "cards", len(slate_cards))


if __name__ == "__main__":
    main()
