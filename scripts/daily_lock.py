#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import os
import re
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SPORT = "baseball_mlb"
BOOKS = ["hardrockbet_fl", "hardrockbet"]
MARKETS = "h2h,spreads,totals"
UNIT = 50
ML_EDGE, ML_CAP, ML_PLUS_CAP = 0.07, -180, 200
RL_JUICE_LO, RL_JUICE_HI = -130, 170
OU_EDGE, OU_BASE = 0.04, 0.524
RESEARCH = {
    "ml": {"bets": 54, "wr": 0.63, "units": 11.6},
    "rl": {"bets": 35, "wr": 0.771, "units": 30.7},
    "ou": {"bets": 46, "wr": 0.609, "units": 7.5},
    "stacked_units": 49.8,
}
PARK = {
    "Yankee Stadium": 1.02, "Fenway Park": 1.06, "Rogers Centre": 1.01,
    "Oriole Park at Camden Yards": 0.99, "Tropicana Field": 0.95,
    "Progressive Field": 0.98, "Comerica Park": 0.97, "Guaranteed Rate Field": 1.02,
    "Rate Field": 1.02, "Kauffman Stadium": 0.99, "Target Field": 1.00,
    "Minute Maid Park": 1.01, "Angel Stadium": 0.97, "Oakland Coliseum": 0.95,
    "Sutter Health Park": 1.00, "T-Mobile Park": 0.93, "Globe Life Field": 0.98,
    "Truist Park": 1.02, "loanDepot park": 0.96, "Citi Field": 0.96,
    "Nationals Park": 1.00, "Wrigley Field": 1.03, "American Family Field": 1.02,
    "Great American Ball Park": 1.08, "PNC Park": 0.96, "Busch Stadium": 0.97,
    "Coors Field": 1.28, "Chase Field": 1.03, "Petco Park": 0.94,
    "Oracle Park": 0.92, "Dodger Stadium": 0.96, "Citizens Bank Park": 1.05,
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

def key_name(s):
    return re.sub(r"[^a-z ]", "", (s or "").lower()).strip()

def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "green-lock/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def pull_odds(api_key, book):
    q = urllib.parse.urlencode({
        "apiKey": api_key, "bookmakers": book,
        "markets": MARKETS, "oddsFormat": "american",
    })
    data = get_json("https://api.the-odds-api.com/v4/sports/%s/odds?%s" % (SPORT, q))
    return data if isinstance(data, list) else []

def pull_schedule(start, end):
    url = (
        "https://statsapi.mlb.com/api/v1/schedule?sportId=1"
        "&startDate=%s&endDate=%s&hydrate=probablePitcher,team,linescore,weather,officials"
        % (start, end)
    )
    data = get_json(url)
    games = []
    for d in data.get("dates") or []:
        for g in d.get("games") or []:
            away, home = g["teams"]["away"], g["teams"]["home"]
            weather = g.get("weather") or {}
            officials = g.get("officials") or []
            hp = ""
            for o in officials:
                if (o.get("officialType") or "").lower() in ("home plate", "hp", "plate"):
                    hp = (o.get("official") or {}).get("fullName", "")
            games.append({
                "pk": g.get("gamePk"),
                "away": (away.get("team") or {}).get("name"),
                "home": (home.get("team") or {}).get("name"),
                "venue": (g.get("venue") or {}).get("name"),
                "time": g.get("gameDate"),
                "status": (g.get("status") or {}).get("detailedState", ""),
                "abstract": (g.get("status") or {}).get("abstractGameState", ""),
                "as": away.get("score"),
                "hs": home.get("score"),
                "ap": (away.get("probablePitcher") or {}).get("fullName", "TBD"),
                "hp": (home.get("probablePitcher") or {}).get("fullName", "TBD"),
                "temp": weather.get("temp"),
                "wind": weather.get("wind"),
                "ump": hp,
            })
    return games

def book_markets(event):
    books = event.get("bookmakers") or []
    if not books:
        return {}
    return {m.get("key"): (m.get("outcomes") or []) for m in books[0].get("markets") or []}

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
    return None if val is None else int(round(float(val)))

def load_json(path, default):
    return json.loads(path.read_text()) if path.exists() else default

def names_match(a, b):
    return bool(a and b) and a.strip().lower() == b.strip().lower()

def ticket_id(date, away, home, market):
    return "%s|%s|%s|%s" % (date, away, home, market)

def sigmoid(z):
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z))

def load_model():
    for p in (Path("models/model.json"), Path("/home/workdir/artifacts/repo_ship/models/model.json")):
        if p.exists():
            return json.loads(p.read_text())
    raise SystemExit("models/model.json missing")

def load_sp():
    rows = {}
    for p in (Path("models/sp_2026.csv"), Path("/home/workdir/artifacts/repo_ship/models/sp_2026.csv")):
        if not p.exists():
            continue
        with p.open() as f:
            for r in csv.DictReader(f):
                rows[r.get("k") or key_name(r.get("pitcher"))] = r
        break
    return rows

def team_form(games, through_date):
    box = defaultdict(list)
    for g in games:
        if g.get("abstract") != "Final" and "Final" not in (g.get("status") or ""):
            continue
        if (g.get("time") or "")[:10] >= through_date:
            continue
        if g.get("as") is None or g.get("hs") is None:
            continue
        aw, hm = int(g["as"]), int(g["hs"])
        box[g["away"]].append((aw, hm))
        box[g["home"]].append((hm, aw))
    out = {}
    for team, rows in box.items():
        sea_rs = sum(r[0] for r in rows)
        sea_ra = sum(r[1] for r in rows)
        wins = sum(1 for r in rows if r[0] > r[1])
        n = max(len(rows), 1)
        last = rows[-10:]
        ln = max(len(last), 1)
        rs = sum(r[0] for r in last) / ln
        ra = sum(r[1] for r in last) / ln
        out[team] = {
            "wp_sea": wins / n,
            "rs_L10": rs,
            "ra_L10": ra,
            "rd_L10": rs - ra,
            "bp_era": (sea_ra / max(n, 1)) * (9 / 9.0),
        }
    return out

def parse_wind(text):
    t = (text or "").lower()
    mph = 0.0
    m = re.search(r"(\d+)\s*mph", t)
    if m:
        mph = float(m.group(1))
    out = mph if "out" in t else 0.0
    return out, float(re.sub(r"[^0-9.]", "", str(text)) or 0) if False else mph

def score_row(model, featmap):
    xs = []
    for i, name in enumerate(model["features"]):
        v = featmap.get(name)
        if v is None or v == "":
            v = model["median_fill"][i]
        xs.append(float(v))
    z = []
    for i, x in enumerate(xs):
        z.append((x - model["mean"][i]) / (model["std"][i] or 1.0))
    def apply(pack):
        s = pack["b"]
        for wi, zi in zip(pack["w"], z):
            s += wi * zi
        return sigmoid(s)
    return {
        "p_home_ml": apply(model["ml"]),
        "p_over": apply(model["ou"]),
        "p_away_rl": apply(model["rl_away_plus"]),
    }

def grade_ticket(t, games):
    if t.get("status") not in (None, "", "pending"):
        return t
    for g in games:
        if not names_match(g.get("away"), t.get("away")) or not names_match(g.get("home"), t.get("home")):
            continue
        if g.get("abstract") != "Final" and "Final" not in (g.get("status") or ""):
            t["live"] = "%s %s-%s %s" % (t.get("away"), g.get("as"), g.get("hs"), t.get("home"))
            return t
        aw, hm = g.get("as"), g.get("hs")
        if aw is None or hm is None:
            return t
        aw, hm = int(aw), int(hm)
        market, side, odds = t.get("market"), t.get("side") or "", t.get("odds")
        won, push = None, False
        if market == "ML":
            picking_away = t.get("away") in side
            won = (aw > hm) if picking_away else (hm > aw)
            push = aw == hm
        elif market == "RL":
            won = (aw - hm) >= -1
        elif market == "OU":
            line = t.get("ou_line")
            if line is None:
                return t
            line = float(line)
            total = aw + hm
            if total == line:
                push = True
            elif "Under" in side:
                won = total < line
            else:
                won = total > line
        if push:
            t["status"], t["result"], t["pnl"] = "push", "P", 0.0
        elif won is True:
            t["status"], t["result"], t["pnl"] = "win", "W", round(profit_units(odds or -110), 3)
        elif won is False:
            t["status"], t["result"], t["pnl"] = "loss", "L", -1.0
        t["final"] = "%s %s-%s %s" % (t.get("away"), aw, hm, t.get("home"))
        return t
    return t

def ytd_from_ledger(ledger):
    live = {k: {"bets": 0, "wins": 0, "units": 0.0} for k in ("ml", "rl", "ou")}
    for t in ledger:
        if t.get("status") not in ("win", "loss"):
            continue
        key = (t.get("market") or "").lower()
        if key not in live:
            continue
        live[key]["bets"] += 1
        live[key]["units"] += float(t.get("pnl") or 0)
        if t.get("status") == "win":
            live[key]["wins"] += 1
    out, stacked_live = {}, 0.0
    for k in ("ml", "rl", "ou"):
        bets, units = live[k]["bets"], round(live[k]["units"], 2)
        stacked_live += units
        wr = (live[k]["wins"] / bets) if bets else RESEARCH[k]["wr"]
        if bets:
            wr = (RESEARCH[k]["wr"] * RESEARCH[k]["bets"] + live[k]["wins"]) / (RESEARCH[k]["bets"] + bets)
        out[k] = {
            "bets": RESEARCH[k]["bets"] + bets,
            "wr": round(wr, 3),
            "units": round(RESEARCH[k]["units"] + units, 2),
            "live_bets": bets,
            "live_units": units,
        }
    out["stacked_units"] = round(RESEARCH["stacked_units"] + stacked_live, 2)
    out["live_units"] = round(stacked_live, 2)
    return out

def main():
    api_key = os.environ.get("ODDS_API_KEY") or ""
    print("key_set", "yes" if api_key else "no")
    if not api_key:
        raise SystemExit("ODDS_API_KEY missing")

    model = load_model()
    sp_map = load_sp()
    now = et_now()
    day = now.strftime("%Y-%m-%d")
    start = (now - timedelta(days=40)).strftime("%Y-%m-%d")

    events, book_used = [], None
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
        hist = pull_schedule(start, day)
    except Exception as e:
        print("slate_error", type(e).__name__)
        hist = []
    form = team_form(hist, day)
    today_games = [g for g in hist if (g.get("time") or "").startswith(day) or True]
    # restrict today's cards to this date in ET when possible
    slate_today = []
    for g in hist:
        try:
            gd = datetime.fromisoformat((g.get("time") or "").replace("Z", "+00:00")).astimezone(ET).strftime("%Y-%m-%d")
        except Exception:
            gd = (g.get("time") or "")[:10]
        if gd == day:
            slate_today.append(g)

    opens_path, ledger_path = Path("opens.json"), Path("ledger.json")
    opens = load_json(opens_path, {})
    ledger = load_json(ledger_path, [])
    by_id = {t.get("id"): t for t in ledger if t.get("id")}
    slate_cards, new_tickets = [], []

    def add_ticket(away, home, tlabel, market, side, odds, extra=None):
        tid = ticket_id(day, away, home, market)
        if tid in by_id:
            return
        row = {
            "id": tid, "date": day, "market": market, "away": away, "home": home,
            "side": side, "odds": odds, "units": "1u", "time": tlabel,
            "status": "pending", "result": "", "pnl": 0.0, "note": book_used, "book": book_used,
        }
        if extra:
            row.update(extra)
        by_id[tid] = row
        new_tickets.append(row)

    mlb_by_match = {}
    for g in slate_today:
        mlb_by_match[(g.get("away"), g.get("home"))] = g

    for ev in events:
        away, home, eid = ev.get("away_team"), ev.get("home_team"), ev.get("id")
        mk = book_markets(ev)
        h2h, spreads, totals = mk.get("h2h"), mk.get("spreads"), mk.get("totals")
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
        start_t = ev.get("commence_time") or ""
        try:
            tlabel = datetime.fromisoformat(start_t.replace("Z", "+00:00")).astimezone(ET).strftime("%I:%M %p ET").lstrip("0")
        except Exception:
            tlabel = start_t
        aw_px = px_int(ml_away["price"]) if ml_away else None
        hm_px = px_int(ml_home["price"]) if ml_home else None
        slate_cards.append({
            "time": tlabel, "away": away, "home": home,
            "ml_away": ("%+d" % aw_px) if aw_px is not None else "",
            "ml_home": ("%+d" % hm_px) if hm_px is not None else "",
            "note": book_used or "no Hard Rock book",
        })
        if eid not in opens:
            opens[eid] = {
                "away": away, "home": home, "ml_away": aw_px, "ml_home": hm_px,
                "rl_away": px_int(rl_away["price"]) if rl_away else None,
                "ou_line": tot_over.get("point") if tot_over else None,
                "ou_over": px_int(tot_over["price"]) if tot_over else None,
                "ou_under": px_int(tot_under["price"]) if tot_under else None,
            }
        op = opens[eid]
        g = mlb_by_match.get((away, home)) or {}
        hf = form.get(home, {})
        af = form.get(away, {})
        hsp = sp_map.get(key_name(g.get("hp"))) or {}
        asp = sp_map.get(key_name(g.get("ap"))) or {}
        wind_txt = g.get("wind") or ""
        wind_out, wind_mph = 0.0, 0.0
        m = re.search(r"(\d+)\s*mph", wind_txt.lower())
        if m:
            wind_mph = float(m.group(1))
            if "out" in wind_txt.lower():
                wind_out = wind_mph
        def num(row, *keys):
            for k in keys:
                if row.get(k) not in (None, ""):
                    try:
                        return float(str(row.get(k)).replace("%", ""))
                    except Exception:
                        pass
            return None
        feat = {
            "home_implied_close": american_to_prob(hm_px) if hm_px else None,
            "away_implied_close": american_to_prob(aw_px) if aw_px else None,
            "home_ml_move": (american_to_prob(hm_px) - american_to_prob(op["ml_home"])) if hm_px and op.get("ml_home") else 0.0,
            "away_ml_move": (american_to_prob(aw_px) - american_to_prob(op["ml_away"])) if aw_px and op.get("ml_away") else 0.0,
            "home_wp_sea": hf.get("wp_sea"),
            "away_wp_sea": af.get("wp_sea"),
            "home_ra_L10": hf.get("ra_L10"),
            "away_ra_L10": af.get("ra_L10"),
            "home_rd_L10": hf.get("rd_L10"),
            "away_rd_L10": af.get("rd_L10"),
            "home_rs_L10": hf.get("rs_L10"),
            "away_rs_L10": af.get("rs_L10"),
            "home_sp_siera_prior": num(hsp, "siera"),
            "away_sp_siera_prior": num(asp, "siera"),
            "home_sp_xfip_prior": num(hsp, "xfip"),
            "away_sp_xfip_prior": num(asp, "xfip"),
            "park_factor": PARK.get(g.get("venue") or "", 1.0),
            "home_bp_era_L30": hf.get("bp_era"),
            "away_bp_era_L30": af.get("bp_era"),
            "home_sp_kbb_prior": num(hsp, "KBB", "K-BB%"),
            "away_sp_kbb_prior": num(asp, "KBB", "K-BB%"),
            "home_sp_stuff_prior": num(hsp, "stuff_plus"),
            "away_sp_stuff_prior": num(asp, "stuff_plus"),
            "wind_out_mph": wind_out,
            "temp_f": float(g["temp"]) if g.get("temp") not in (None, "") else None,
        }
        preds = score_row(model, feat)
        p_home, p_away = preds["p_home_ml"], 1.0 - preds["p_home_ml"]
        p_over, p_away_rl = preds["p_over"], preds["p_away_rl"]

        if ml_away and ml_home and op.get("ml_away") is not None and op.get("ml_home") is not None:
            for side, px, p_model, open_px in (
                (away, aw_px, p_away, op.get("ml_away")),
                (home, hm_px, p_home, op.get("ml_home")),
            ):
                if px is None or px < ML_CAP or px > ML_PLUS_CAP:
                    continue
                edge = p_model - american_to_prob(open_px)
                if edge >= ML_EDGE:
                    add_ticket(away, home, tlabel, "ML", "%s %+d" % (side, px), px,
                               {"edge": "model-open %.1f%%" % (edge * 100)})

        if rl_away:
            px = px_int(rl_away["price"])
            if px is not None and RL_JUICE_LO <= px <= RL_JUICE_HI:
                impl = american_to_prob(px)
                edge = p_away_rl - impl
                if edge >= 0.03:
                    add_ticket(away, home, tlabel, "RL", "%s +1.5 %+d" % (away, px), px,
                               {"edge": "cover-edge %.1f%%" % (edge * 100)})

        if tot_over and tot_under:
            ov, un = px_int(tot_over["price"]), px_int(tot_under["price"])
            line = tot_over.get("point")
            if line is not None and abs(p_over - OU_BASE) >= OU_EDGE:
                if p_over >= OU_BASE:
                    add_ticket(away, home, tlabel, "OU", "Over %s %+d" % (line, ov or -110), ov or -110,
                               {"edge": "P(over) %.1f%%" % (p_over * 100), "ou_line": line})
                else:
                    add_ticket(away, home, tlabel, "OU", "Under %s %+d" % (line, un or -110), un or -110,
                               {"edge": "P(over) %.1f%%" % (p_over * 100), "ou_line": line})

    ledger = list(by_id.values())
    graded = [grade_ticket(t, hist) for t in ledger]
    graded.sort(key=lambda t: t.get("date", ""), reverse=True)
    today_picks = []
    for t in graded:
        if t.get("date") != day:
            continue
        today_picks.append({
            "market": t.get("market"), "away": t.get("away"), "home": t.get("home"),
            "side": t.get("side"), "time": t.get("time"), "edge": t.get("edge"),
            "units": t.get("units"), "note": t.get("note"),
            "status": t.get("status") or "pending", "result": t.get("result") or "",
            "pnl": t.get("pnl"), "final": t.get("final") or t.get("live") or "", "odds": t.get("odds"),
        })
    recent = []
    for t in graded:
        if t.get("status") not in ("win", "loss", "push"):
            continue
        recent.append({
            "date": t.get("date"), "game": "%s @ %s" % (t.get("away"), t.get("home")),
            "market": t.get("market"), "side": t.get("side"), "result": t.get("result"),
            "units": t.get("pnl"), "final": t.get("final"),
        })
        if len(recent) >= 25:
            break
    payload = {
        "updated": now.strftime("%Y-%m-%d %H:%M ET"),
        "unit_dollars": UNIT, "season": 2026, "book": book_used,
        "ytd": ytd_from_ledger(graded),
        "locks": [
            {"market": "ML", "rule": "Frozen logreg. model_p − open ≥ 7%. Cap −180 to +200."},
            {"market": "RL", "rule": "+1.5 dog only. Juice −130 to +170. P(cover) − implied ≥ 3%."},
            {"market": "OU", "rule": "One side. |P(over) − 52.4%| ≥ 4%. Skip pushes."},
        ],
        "slate_date": day, "slate": slate_cards, "picks": today_picks,
        "picks_status": ("%d model ticket(s) · %s" % (len(today_picks), book_used or "no book") if today_picks else "No model lock this pass."),
        "recent": recent,
    }
    Path("picks.json").write_text(json.dumps(payload, indent=2) + "\n")
    opens_path.write_text(json.dumps(opens, indent=2) + "\n")
    ledger_path.write_text(json.dumps(graded, indent=2) + "\n")
    print("picks", len(today_picks), "ledger", len(graded), "new", len(new_tickets), "model_yes")

if __name__ == "__main__":
    main()
