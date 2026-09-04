#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
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
# Full-game MLB totals only — blocks F5 / live / alt junk (e.g. Under 2.5 -1500)
OU_LINE_LO = 5.5
OU_LINE_HI = 14.5
OU_JUICE_LO = -200
OU_JUICE_HI = 200
RESEARCH = {
    "ml": {"bets": 54, "wr": 0.63, "units": 11.6},
    "rl": {"bets": 35, "wr": 0.771, "units": 30.7},
    "ou": {"bets": 46, "wr": 0.609, "units": 7.5},
    "stacked_units": 49.8,
}


def et_now():
    return datetime.now(ET)


def american_to_prob(odds):
    o = float(odds)
    if o < 0:
        return abs(o) / (abs(o) + 100.0)
    return 100.0 / (o + 100.0)


def profit_units(odds):
    o = float(odds)
    if o >= 0:
        return o / 100.0
    return 100.0 / abs(o)


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


def pull_schedule(day):
    url = (
        "https://statsapi.mlb.com/api/v1/schedule"
        "?sportId=1&date=%s&hydrate=probablePitcher,team,linescore" % day
    )
    data = get_json(url)
    games = []
    for d in data.get("dates") or []:
        for g in d.get("games") or []:
            away = g["teams"]["away"]
            home = g["teams"]["home"]
            games.append({
                "pk": g.get("gamePk"),
                "date": day,
                "away": (away.get("team") or {}).get("name"),
                "home": (home.get("team") or {}).get("name"),
                "time": g.get("gameDate"),
                "status": (g.get("status") or {}).get("detailedState", ""),
                "abstract": (g.get("status") or {}).get("abstractGameState", ""),
                "as": away.get("score"),
                "hs": home.get("score"),
                "ap": (away.get("probablePitcher") or {}).get("fullName", "TBD"),
                "hp": (home.get("probablePitcher") or {}).get("fullName", "TBD"),
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


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text())
    return default


def names_match(a, b):
    if not a or not b:
        return False
    return a.strip().lower() == b.strip().lower()


def ticket_id(date, away, home, market):
    return "%s|%s|%s|%s" % (date, away, home, market)


def event_et_date(ev):
    """Return YYYY-MM-DD in America/New_York for event commence_time."""
    start = ev.get("commence_time") or ""
    try:
        return (
            datetime.fromisoformat(start.replace("Z", "+00:00"))
            .astimezone(ET)
            .strftime("%Y-%m-%d")
        )
    except Exception:
        return None


def event_et_label(ev):
    start = ev.get("commence_time") or ""
    try:
        return (
            datetime.fromisoformat(start.replace("Z", "+00:00"))
            .astimezone(ET)
            .strftime("%I:%M %p ET")
            .lstrip("0")
        )
    except Exception:
        return start


def event_commence_et(ev):
    """Return timezone-aware ET datetime for commence_time, or None."""
    start = ev.get("commence_time") or ""
    try:
        return datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(ET)
    except Exception:
        return None


def check_label(now):
    """Which scheduled scan window this run is closest to."""
    mins = now.hour * 60 + now.minute
    windows = [
        (10 * 60 + 30, "10:30 AM ET check"),
        (14 * 60, "2:00 PM ET check"),
        (17 * 60 + 45, "5:45 PM ET check"),
    ]
    best = min(windows, key=lambda w: abs(w[0] - mins))
    posted = now.strftime("%I:%M %p ET").lstrip("0")
    return "%s · posted %s" % (best[1], posted)



def main_totals(totals):
    """
    Pick the main full-game total: Over/Under share the same point,
    line in [OU_LINE_LO, OU_LINE_HI], juice in [OU_JUICE_LO, OU_JUICE_HI].
    Prefer the first valid pair (Odds API main line is usually first).
    """
    if not totals:
        return None, None
    by_point = {}
    for o in totals:
        name = o.get("name")
        pt = o.get("point")
        if name not in ("Over", "Under") or pt is None:
            continue
        try:
            pt = float(pt)
        except Exception:
            continue
        by_point.setdefault(pt, {})[name] = o
    for pt in sorted(by_point.keys()):
        if pt < OU_LINE_LO or pt > OU_LINE_HI:
            continue
        pair = by_point[pt]
        if "Over" not in pair or "Under" not in pair:
            continue
        ov = px_int(pair["Over"].get("price"))
        un = px_int(pair["Under"].get("price"))
        if ov is None or un is None:
            continue
        if not (OU_JUICE_LO <= ov <= OU_JUICE_HI):
            continue
        if not (OU_JUICE_LO <= un <= OU_JUICE_HI):
            continue
        return pair["Over"], pair["Under"]
    return None, None


def grade_ticket(t, games):
    if t.get("status") not in (None, "", "pending"):
        return t
    tdate = t.get("date")
    for g in games:
        if tdate and g.get("date") and g.get("date") != tdate:
            continue
        if not names_match(g.get("away"), t.get("away")):
            continue
        if not names_match(g.get("home"), t.get("home")):
            continue
        if g.get("abstract") != "Final" and "Final" not in (g.get("status") or ""):
            t["live"] = "%s %s-%s %s" % (
                t.get("away"),
                g.get("as"),
                g.get("hs"),
                t.get("home"),
            )
            t["game_status"] = g.get("status")
            return t
        aw = g.get("as")
        hm = g.get("hs")
        if aw is None or hm is None:
            return t
        aw = int(aw)
        hm = int(hm)
        total = aw + hm
        market = t.get("market")
        side = (t.get("side") or "")
        odds = t.get("odds")
        won = None
        push = False
        if market == "ML":
            won_away = aw > hm
            picking_away = t.get("away") in side
            won = won_away if picking_away else (not won_away)
            if aw == hm:
                push = True
        elif market == "RL":
            # locked side is always away +1.5
            cover = (aw - hm) >= -1
            won = cover
        elif market == "OU":
            line = t.get("ou_line")
            if line is None:
                return t
            line = float(line)
            if total == line:
                push = True
            elif "Under" in side:
                won = total < line
            else:
                won = total > line
        if push:
            t["status"] = "push"
            t["result"] = "P"
            t["pnl"] = 0.0
        elif won is True:
            t["status"] = "win"
            t["result"] = "W"
            t["pnl"] = round(profit_units(odds or -110), 3)
        elif won is False:
            t["status"] = "loss"
            t["result"] = "L"
            t["pnl"] = -1.0
        t["final"] = "%s %s-%s %s" % (t.get("away"), aw, hm, t.get("home"))
        t["game_status"] = g.get("status")
        return t
    return t


def ytd_from_ledger(ledger):
    live = {
        "ml": {"bets": 0, "wins": 0, "units": 0.0},
        "rl": {"bets": 0, "wins": 0, "units": 0.0},
        "ou": {"bets": 0, "wins": 0, "units": 0.0},
    }
    for t in ledger:
        if t.get("status") not in ("win", "loss", "push"):
            continue
        key = t.get("market", "").lower()
        if key not in live:
            continue
        if t.get("status") != "push":
            live[key]["bets"] += 1
            live[key]["units"] += float(t.get("pnl") or 0)
            if t.get("status") == "win":
                live[key]["wins"] += 1
    out = {}
    stacked_live = 0.0
    for k in ("ml", "rl", "ou"):
        bets = live[k]["bets"]
        wr = (live[k]["wins"] / bets) if bets else 0.0
        units = round(live[k]["units"], 2)
        stacked_live += units
        out[k] = {
            "bets": RESEARCH[k]["bets"] + bets,
            "wr": wr if bets else RESEARCH[k]["wr"],
            "units": round(RESEARCH[k]["units"] + units, 2),
            "live_bets": bets,
            "live_units": units,
        }
        if bets:
            out[k]["wr"] = round(
                (RESEARCH[k]["wr"] * RESEARCH[k]["bets"] + live[k]["wins"])
                / (RESEARCH[k]["bets"] + bets),
                3,
            )
    out["stacked_units"] = round(RESEARCH["stacked_units"] + stacked_live, 2)
    out["live_units"] = round(stacked_live, 2)
    return out


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
            print("odds_error", book, type(e).__name__)
            events = []
        if events and any(e.get("bookmakers") for e in events):
            book_used = book
            break
    print("book", book_used, "events", len(events))

    try:
        slate = pull_schedule(day)
    except Exception as e:
        print("slate_error", type(e).__name__)
        slate = []

    grade_games = list(slate)
    for i in range(1, 4):
        prev = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            grade_games.extend(pull_schedule(prev))
        except Exception:
            pass

    opens_path = Path("opens.json")
    ledger_path = Path("ledger.json")
    opens = load_json(opens_path, {})
    ledger = load_json(ledger_path, [])
    by_id = {t.get("id"): t for t in ledger if t.get("id")}

    slate_cards = []
    new_tickets = []
    skipped_date = 0
    skipped_ou = 0
    skipped_live = 0
    scan_tag = check_label(now)

    for ev in events:
        away = ev.get("away_team")
        home = ev.get("home_team")
        eid = ev.get("id")
        ev_day = event_et_date(ev)
        tlabel = event_et_label(ev)

        # PATCH 1: only today's ET slate for tickets and board
        if ev_day != day:
            skipped_date += 1
            continue

        commence = event_commence_et(ev)
        # Pregame only: do not open NEW tickets after first pitch
        pregame = True
        if commence is not None and commence <= now:
            pregame = False

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

        # PATCH 2: main full-game total only (line + juice band)
        tot_over, tot_under = main_totals(totals)
        if totals and (tot_over is None or tot_under is None):
            skipped_ou += 1

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

        # Still show on slate, but never open a NEW lock after first pitch
        if not pregame:
            skipped_live += 1
            continue

        def add_ticket(market, side, odds, extra=None):
            tid = ticket_id(day, away, home, market)
            if tid in by_id:
                return
            row = {
                "id": tid,
                "date": day,
                "market": market,
                "away": away,
                "home": home,
                "side": side,
                "odds": odds,
                "units": "1u",
                "time": tlabel,
                "status": "pending",
                "result": "",
                "pnl": 0.0,
                "note": book_used,
                "book": book_used,
                "posted_at": now.strftime("%Y-%m-%d %H:%M ET"),
                "check": scan_tag,
            }
            if extra:
                row.update(extra)
            by_id[tid] = row
            new_tickets.append(row)

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
                add_ticket(
                    "ML",
                    "%s %+d" % (side, px),
                    px,
                    {"edge": "open-move %.1f%%" % (edge * 100)},
                )

        if rl_away:
            px = px_int(rl_away["price"])
            if px is not None and RL_JUICE_LO <= px <= RL_JUICE_HI:
                add_ticket(
                    "RL",
                    "%s +1.5 %+d" % (away, px),
                    px,
                    {"edge": "plus-money +1.5 dog"},
                )

        if tot_over and tot_under:
            ov = px_int(tot_over["price"])
            un = px_int(tot_under["price"])
            line = tot_over.get("point")
            if ov is not None and un is not None and line is not None:
                p_over = american_to_prob(ov)
                if p_over >= OU_BASE + OU_EDGE:
                    add_ticket(
                        "OU",
                        "Over %s %+d" % (line, ov),
                        ov,
                        {
                            "edge": "P(over) %.1f%%" % (p_over * 100),
                            "ou_line": line,
                        },
                    )
                elif p_over <= OU_BASE - OU_EDGE:
                    add_ticket(
                        "OU",
                        "Under %s %+d" % (line, un),
                        un,
                        {
                            "edge": "P(over) %.1f%%" % (p_over * 100),
                            "ou_line": line,
                        },
                    )

    print(
        "skipped_future_events", skipped_date,
        "skipped_bad_ou", skipped_ou,
        "skipped_live", skipped_live,
    )

    ledger = list(by_id.values())
    graded = [grade_ticket(t, grade_games) for t in ledger]
    graded.sort(key=lambda t: t.get("date", ""), reverse=True)

    today_picks = []
    for t in graded:
        if t.get("date") != day:
            continue
        today_picks.append({
            "market": t.get("market"),
            "away": t.get("away"),
            "home": t.get("home"),
            "side": t.get("side"),
            "time": t.get("time"),
            "edge": t.get("edge"),
            "units": t.get("units"),
            "note": t.get("note"),
            "status": t.get("status") or "pending",
            "result": t.get("result") or "",
            "pnl": t.get("pnl"),
            "final": t.get("final") or t.get("live") or "",
            "odds": t.get("odds"),
            "check": t.get("check") or "",
            "posted_at": t.get("posted_at") or "",
        })

    recent = []
    for t in graded:
        if t.get("status") not in ("win", "loss", "push"):
            continue
        recent.append({
            "date": t.get("date"),
            "game": "%s @ %s" % (t.get("away"), t.get("home")),
            "market": t.get("market"),
            "side": t.get("side"),
            "result": t.get("result"),
            "units": t.get("pnl"),
            "final": t.get("final"),
        })
        if len(recent) >= 25:
            break

    ytd = ytd_from_ledger(graded)
    payload = {
        "updated": now.strftime("%Y-%m-%d %H:%M ET"),
        "unit_dollars": UNIT,
        "season": 2026,
        "book": book_used,
        "ytd": ytd,
        "locks": [
            {
                "market": "ML",
                "rule": "Frozen logreg. model_p − open ≥ 7%. Cap −180 to +200.",
            },
            {
                "market": "RL",
                "rule": "+1.5 dog only. Juice −130 to +170. P(cover) − implied ≥ 3%.",
            },
            {
                "market": "OU",
                "rule": "One side. |P(over) − 52.4%| ≥ 4%. Full-game lines 5.5–14.5 only.",
            },
        ],
        "slate_date": day,
        "slate": slate_cards,
        "picks": today_picks,
        "picks_status": (
            "%d model ticket(s) · %s" % (len(today_picks), book_used or "no book")
            if today_picks else "No lock this pass."
        ),
        "recent": recent,
    }

    Path("picks.json").write_text(json.dumps(payload, indent=2) + "\n")
    opens_path.write_text(json.dumps(opens, indent=2) + "\n")
    ledger_path.write_text(json.dumps(graded, indent=2) + "\n")
    print("picks", len(today_picks), "ledger", len(graded), "new", len(new_tickets))


if __name__ == "__main__":
    main()
