"""Frozen standardized logreg for ML / RL / OU — pure Python (no sklearn)."""
from __future__ import annotations

import json
import math
import re
import urllib.request
from pathlib import Path

MODEL_PATHS = [
    Path("models/model.json"),
    Path("repo_ship/models/model.json"),
]


def _load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def find_model():
    for p in MODEL_PATHS:
        if p.exists():
            return json.loads(p.read_text())
    return None


def american_to_prob(odds):
    try:
        o = float(odds)
    except (TypeError, ValueError):
        return None
    if o < 0:
        return abs(o) / (abs(o) + 100.0)
    return 100.0 / (o + 100.0)


def sigmoid(z):
    if z >= 0:
        ez = math.exp(-z)
        return 1.0 / (1.0 + ez)
    ez = math.exp(z)
    return ez / (1.0 + ez)


def score_market(model, market, features_dict):
    """Return P(event) for market in {ml, ou, rl_away_plus}. ml = P(home win)."""
    if not model or market not in model:
        return None
    names = model["features"]
    mean = model["mean"]
    std = model["std"]
    fill = model["median_fill"]
    pack = model[market]
    w = pack["w"]
    b = pack["b"]
    zdot = 0.0
    for i, name in enumerate(names):
        raw = features_dict.get(name)
        if raw is None or (isinstance(raw, float) and math.isnan(raw)):
            raw = fill[i]
        try:
            raw = float(raw)
        except (TypeError, ValueError):
            raw = fill[i]
        s = std[i] if std[i] else 1.0
        z = (raw - mean[i]) / s
        zdot += w[i] * z
    return sigmoid(b + zdot)


def norm_pitcher(name):
    s = str(name or "").lower().strip()
    s = s.replace(".", "").replace("'", "")
    s = re.sub(r"\s+jr\.?$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


TEAM_ABBR = {
    "arizona diamondbacks": "ARI", "atlanta braves": "ATL", "baltimore orioles": "BAL",
    "boston red sox": "BOS", "chicago cubs": "CHC", "chicago white sox": "CHW",
    "cincinnati reds": "CIN", "cleveland guardians": "CLE", "colorado rockies": "COL",
    "detroit tigers": "DET", "houston astros": "HOU", "kansas city royals": "KC",
    "los angeles angels": "LAA", "los angeles dodgers": "LAD", "miami marlins": "MIA",
    "milwaukee brewers": "MIL", "minnesota twins": "MIN", "new york mets": "NYM",
    "new york yankees": "NYY", "athletics": "OAK", "oakland athletics": "OAK",
    "philadelphia phillies": "PHI", "pittsburgh pirates": "PIT", "san diego padres": "SD",
    "san francisco giants": "SF", "seattle mariners": "SEA", "st. louis cardinals": "STL",
    "tampa bay rays": "TB", "texas rangers": "TEX", "toronto blue jays": "TOR",
    "washington nationals": "WSH",
}


def team_to_abbr(name):
    k = str(name or "").lower().strip()
    if k in TEAM_ABBR:
        return TEAM_ABBR[k]
    for full, ab in TEAM_ABBR.items():
        if k in full or full in k:
            return ab
    return str(name or "").upper()[:3]


def fetch_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "grok-lock-mlb/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def load_sp_priors():
    for p in [Path("models/sp_priors_2026.json"), Path("repo_ship/models/sp_priors_2026.json")]:
        if p.exists():
            return _load_json(p, {})
    return {}


def load_park_factors():
    for p in [Path("models/park_factors.json"), Path("repo_ship/models/park_factors.json")]:
        if p.exists():
            return _load_json(p, {})
    return {}


def sp_lookup(priors, pitcher_name):
    key = norm_pitcher(pitcher_name)
    if key in priors:
        return priors[key]
    # last name fallback
    parts = key.split()
    if not parts:
        return {}
    last = parts[-1]
    hits = [(k, v) for k, v in priors.items() if k.endswith(last)]
    if len(hits) == 1:
        return hits[0][1]
    if hits:
        hits.sort(key=lambda kv: kv[1].get("ip") or 0, reverse=True)
        return hits[0][1]
    return {}


def build_team_form(day_iso, lookback_days=25):
    """Season WP + L10/L3 RS/RA/RD + rest_days from MLB schedule API (strictly before day)."""
    from datetime import datetime, timedelta

    end = datetime.strptime(day_iso, "%Y-%m-%d").date()
    season_start = "%d-03-20" % end.year
    url = (
        "https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=%s&endDate=%s"
        "&hydrate=linescore,team"
    ) % (season_start, day_iso)
    try:
        data = fetch_json(url, timeout=45)
    except Exception as e:
        print("team_form_error", type(e).__name__, e)
        return {}

    games_by = {}
    season_w = {}
    season_n = {}
    last_date = {}
    for d in data.get("dates") or []:
        ds = d.get("date") or ""
        for g in d.get("games") or []:
            st = (g.get("status") or {}).get("abstractGameState") or ""
            if st != "Final":
                continue
            if ds >= day_iso:
                continue
            away = ((g.get("teams") or {}).get("away") or {}).get("team") or {}
            home = ((g.get("teams") or {}).get("home") or {}).get("team") or {}
            an = away.get("name") or ""
            hn = home.get("name") or ""
            ar = ((g.get("teams") or {}).get("away") or {}).get("score")
            hr = ((g.get("teams") or {}).get("home") or {}).get("score")
            if ar is None or hr is None:
                continue
            ar, hr = int(ar), int(hr)
            for name, rs, ra, won in (
                (an, ar, hr, ar > hr),
                (hn, hr, ar, hr > ar),
            ):
                ab = team_to_abbr(name)
                season_w[ab] = season_w.get(ab, 0) + (1 if won else 0)
                season_n[ab] = season_n.get(ab, 0) + 1
                games_by.setdefault(ab, []).append((ds, rs, ra))
                prev = last_date.get(ab)
                if prev is None or ds > prev:
                    last_date[ab] = ds

    out = {}
    for ab, games in games_by.items():
        games_sorted = sorted(games, key=lambda x: x[0])
        last10 = games_sorted[-10:]
        last3 = games_sorted[-3:]
        if not last10:
            continue
        rs = sum(g[1] for g in last10) / len(last10)
        ra = sum(g[2] for g in last10) / len(last10)
        ra3 = sum(g[2] for g in last3) / len(last3) if last3 else ra
        rs3 = sum(g[1] for g in last3) / len(last3) if last3 else rs
        n = season_n.get(ab, 0) or 1
        rest = 3.0
        ld = last_date.get(ab)
        if ld:
            try:
                rest = float((end - datetime.strptime(ld, "%Y-%m-%d").date()).days)
            except Exception:
                rest = 3.0
        out[ab] = {
            "wp_sea": season_w.get(ab, 0) / n,
            "rs_L10": rs,
            "ra_L10": ra,
            "rd_L10": rs - ra,
            "rs_L3": rs3,
            "ra_L3": ra3,
            "rd_L3": rs3 - ra3,
            "rest_days": rest,
            "last_game_date": ld,
        }
    return out


def _best_of_from_series(series_desc, game_type):
    s = str(series_desc or "").lower()
    gt = str(game_type or "").upper()
    if "wild card" in s or gt == "F":
        return 3 if "series" in s else 1
    if "division" in s or gt == "D":
        return 5
    if "championship" in s or "lcs" in s or gt == "L":
        return 7
    if "world series" in s or gt == "W":
        return 7
    return 5


def build_series_context(day_iso):
    """Playoff series state for games on day_iso: game number, wins, elimination flags.

    Uses MLB schedule from season start through day_iso. Only Finals before today
    count toward series score; today's game is not included in prior wins.
    """
    from datetime import datetime

    end = datetime.strptime(day_iso, "%Y-%m-%d").date()
    season_start = "%d-03-20" % end.year
    url = (
        "https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=%s&endDate=%s"
        "&hydrate=team"
    ) % (season_start, day_iso)
    try:
        data = fetch_json(url, timeout=45)
    except Exception as e:
        print("series_context_error", type(e).__name__, e)
        return {}

    # series_key -> list of completed games (date, away_ab, home_ab, away_score, home_score)
    completed = {}
    today_games = []
    for d in data.get("dates") or []:
        ds = d.get("date") or ""
        for g in d.get("games") or []:
            gt = (g.get("gameType") or "R").upper()
            if gt not in ("F", "D", "L", "W"):
                continue
            series = g.get("seriesDescription") or ""
            away = ((g.get("teams") or {}).get("away") or {}).get("team") or {}
            home = ((g.get("teams") or {}).get("home") or {}).get("team") or {}
            ak = team_to_abbr(away.get("name") or "")
            hk = team_to_abbr(home.get("name") or "")
            pair = tuple(sorted([ak, hk]))
            sk = (gt, series, pair)
            st = (g.get("status") or {}).get("abstractGameState") or ""
            ar = ((g.get("teams") or {}).get("away") or {}).get("score")
            hr = ((g.get("teams") or {}).get("home") or {}).get("score")
            if ds < day_iso and st == "Final" and ar is not None and hr is not None:
                completed.setdefault(sk, []).append((ds, ak, hk, int(ar), int(hr)))
            if ds == day_iso:
                today_games.append((sk, ak, hk, gt, series, g.get("gamePk")))

    out = {}
    for sk, ak, hk, gt, series, pk in today_games:
        prior = sorted(completed.get(sk) or [], key=lambda x: x[0])
        wins = {ak: 0, hk: 0}
        for _ds, a, h, ar, hr in prior:
            if ar > hr:
                wins[a] = wins.get(a, 0) + 1
            else:
                wins[h] = wins.get(h, 0) + 1
        series_game = len(prior) + 1
        best_of = _best_of_from_series(series, gt)
        need = best_of // 2 + 1
        away_elim = series_game > 1 and wins.get(hk, 0) >= need - 1
        home_elim = series_game > 1 and wins.get(ak, 0) >= need - 1
        out[(ak, hk)] = {
            "series_game": series_game,
            "best_of": best_of,
            "series_wins_away": wins.get(ak, 0),
            "series_wins_home": wins.get(hk, 0),
            "is_series_game1": series_game == 1,
            "is_elimination_game": bool(away_elim or home_elim),
            "away_elim_if_lose": bool(away_elim),
            "home_elim_if_lose": bool(home_elim),
            "series_description": series,
            "game_type": gt,
            "game_pk": pk,
        }
    return out


def probable_pitchers(day_iso):
    url = (
        "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=%s"
        "&hydrate=probablePitcher,team"
    ) % day_iso
    try:
        data = fetch_json(url, timeout=25)
    except Exception as e:
        print("probable_error", type(e).__name__, e)
        return {}
    out = {}
    for d in data.get("dates") or []:
        for g in d.get("games") or []:
            away = ((g.get("teams") or {}).get("away") or {}).get("team") or {}
            home = ((g.get("teams") or {}).get("home") or {}).get("team") or {}
            ap = ((g.get("teams") or {}).get("away") or {}).get("probablePitcher") or {}
            hp = ((g.get("teams") or {}).get("home") or {}).get("probablePitcher") or {}
            ak = team_to_abbr(away.get("name") or "")
            hk = team_to_abbr(home.get("name") or "")
            out[(ak, hk)] = {
                "away_sp": ap.get("fullName") or ap.get("lastName") or "",
                "home_sp": hp.get("fullName") or hp.get("lastName") or "",
            }
    return out


def build_game_features(
    away_name,
    home_name,
    ml_away,
    ml_home,
    open_away,
    open_home,
    form,
    pitchers,
    priors,
    parks,
    series_context=None,
):
    """Feature dict for frozen model. Uses current ML as close proxy at lock time.

    series_context: optional dict from build_series_context(day) keyed by (away_abbr, home_abbr).
    Rest / L3 / series flags are attached to meta for playoff logic and future model versions.
    BP ERA uses recent RA as live proxy until true bullpen IP feed exists.
    """
    aa = team_to_abbr(away_name)
    ha = team_to_abbr(home_name)
    fa = form.get(aa) or {}
    fh = form.get(ha) or {}
    pinfo = pitchers.get((aa, ha)) or {}
    asp = sp_lookup(priors, pinfo.get("away_sp"))
    hsp = sp_lookup(priors, pinfo.get("home_sp"))

    home_imp = american_to_prob(ml_home)
    away_imp = american_to_prob(ml_away)
    home_move = None
    away_move = None
    try:
        if open_home is not None and ml_home is not None:
            home_move = float(ml_home) - float(open_home)
        if open_away is not None and ml_away is not None:
            away_move = float(ml_away) - float(open_away)
    except (TypeError, ValueError):
        pass

    pf = parks.get(ha)
    if pf is None:
        pf = parks.get(str(ha).upper())

    # Live bullpen proxy: recent runs allowed (L10 primary, L3 fallback)
    home_bp = fh.get("ra_L10")
    if home_bp is None:
        home_bp = fh.get("ra_L3")
    away_bp = fa.get("ra_L10")
    if away_bp is None:
        away_bp = fa.get("ra_L3")

    feats = {
        "home_implied_close": home_imp,
        "away_implied_close": away_imp,
        "home_ml_move": home_move,
        "away_ml_move": away_move,
        "home_wp_sea": fh.get("wp_sea"),
        "away_wp_sea": fa.get("wp_sea"),
        "home_ra_L10": fh.get("ra_L10"),
        "away_ra_L10": fa.get("ra_L10"),
        "home_rd_L10": fh.get("rd_L10"),
        "away_rd_L10": fa.get("rd_L10"),
        "home_rs_L10": fh.get("rs_L10"),
        "away_rs_L10": fa.get("rs_L10"),
        "home_sp_siera_prior": hsp.get("siera"),
        "away_sp_siera_prior": asp.get("siera"),
        "home_sp_xfip_prior": hsp.get("xfip"),
        "away_sp_xfip_prior": asp.get("xfip"),
        "park_factor": pf,
        "home_bp_era_L30": home_bp,
        "away_bp_era_L30": away_bp,
        "home_sp_kbb_prior": hsp.get("kbb"),
        "away_sp_kbb_prior": asp.get("kbb"),
        "home_sp_stuff_prior": hsp.get("stuff"),
        "away_sp_stuff_prior": asp.get("stuff"),
        "wind_out_mph": None,
        "temp_f": None,
    }

    sx = (series_context or {}).get((aa, ha)) or {}
    meta = {
        "away_abbr": aa,
        "home_abbr": ha,
        "away_sp": pinfo.get("away_sp") or "",
        "home_sp": pinfo.get("home_sp") or "",
        "away_sp_matched": bool(asp),
        "home_sp_matched": bool(hsp),
        "away_rest_days": fa.get("rest_days"),
        "home_rest_days": fh.get("rest_days"),
        "away_ra_L3": fa.get("ra_L3"),
        "home_ra_L3": fh.get("ra_L3"),
        "series_game": sx.get("series_game"),
        "best_of": sx.get("best_of"),
        "is_series_game1": sx.get("is_series_game1"),
        "is_elimination_game": sx.get("is_elimination_game"),
        "series_wins_away": sx.get("series_wins_away"),
        "series_wins_home": sx.get("series_wins_home"),
        "series_description": sx.get("series_description"),
        "game_type": sx.get("game_type"),
    }
    return feats, meta


def _parse_ip(s):
    """MLB inningsPitched: 5.1 = 5 + 1/3, 5.2 = 5 + 2/3."""
    if s is None:
        return 0.0
    try:
        x = float(s)
    except (TypeError, ValueError):
        return 0.0
    whole = int(x)
    frac = x - whole
    if abs(frac - 0.1) < 0.01:
        return whole + 1.0 / 3.0
    if abs(frac - 0.2) < 0.01:
        return whole + 2.0 / 3.0
    return float(x)


def _box_pitching_ip(game_pk):
    """Return {abbr: {relief_ip, starter_ip}} for a completed game."""
    try:
        data = fetch_json(
            "https://statsapi.mlb.com/api/v1.1/game/%s/feed/live" % int(game_pk),
            timeout=25,
        )
    except Exception:
        return {}
    gd = data.get("gameData") or {}
    teams_meta = gd.get("teams") or {}
    box = (data.get("liveData") or {}).get("boxscore") or {}
    bteams = box.get("teams") or {}
    out = {}
    for side in ("away", "home"):
        tname = ((teams_meta.get(side) or {}).get("team") or {}).get("name") or ""
        ab = team_to_abbr(tname)
        players = (bteams.get(side) or {}).get("players") or {}
        pitchers = (bteams.get(side) or {}).get("pitchers") or []
        relief_ip = 0.0
        starter_ip = 0.0
        for i, pid in enumerate(pitchers):
            pl = players.get("ID%s" % pid) or {}
            stats = ((pl.get("stats") or {}).get("pitching") or {})
            ip = _parse_ip(stats.get("inningsPitched"))
            if i == 0:
                starter_ip = ip
            else:
                relief_ip += ip
        out[ab] = {
            "relief_ip": round(relief_ip, 3),
            "starter_ip": round(starter_ip, 3),
        }
    return out


def build_live_bullpen_context(day_iso, team_abbrs=None):
    """Live relief IP L3d + previous-start length for teams on today's slate.

    Returns dict abbr -> {
      bp_ip_L3d, prev_starter_ip, prev_short_start (1 if prior start <= 5 IP)
    }
    Strictly uses Finals before day_iso only (no lookahead).
    """
    from datetime import datetime, timedelta

    end = datetime.strptime(day_iso, "%Y-%m-%d").date()
    start = end - timedelta(days=4)
    url = (
        "https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=%s&endDate=%s"
        "&hydrate=team"
    ) % (start.isoformat(), day_iso)
    try:
        data = fetch_json(url, timeout=40)
    except Exception as e:
        print("bullpen_context_error", type(e).__name__, e)
        return {}

    want = None
    if team_abbrs:
        want = set(str(t).upper() for t in team_abbrs)

    # Collect finals before today for relevant teams
    games = []  # (date, pk, away_ab, home_ab)
    for d in data.get("dates") or []:
        ds = d.get("date") or ""
        if ds >= day_iso:
            continue
        for g in d.get("games") or []:
            if (g.get("status") or {}).get("abstractGameState") != "Final":
                continue
            pk = g.get("gamePk")
            if not pk:
                continue
            away = ((g.get("teams") or {}).get("away") or {}).get("team") or {}
            home = ((g.get("teams") or {}).get("home") or {}).get("team") or {}
            aa = team_to_abbr(away.get("name") or "")
            ha = team_to_abbr(home.get("name") or "")
            if want and aa not in want and ha not in want:
                continue
            games.append((ds, int(pk), aa, ha))

    # Fetch boxes
    by_team = {}  # ab -> list of (date, relief_ip, starter_ip)
    seen_pk = set()
    for ds, pk, aa, ha in games:
        if pk in seen_pk:
            continue
        seen_pk.add(pk)
        box = _box_pitching_ip(pk)
        for ab in (aa, ha):
            info = box.get(ab) or {}
            if not info and ab:
                # try any key match
                for k, v in box.items():
                    if k == ab:
                        info = v
                        break
            by_team.setdefault(ab, []).append(
                (
                    ds,
                    float(info.get("relief_ip") or 0),
                    float(info.get("starter_ip") or 0),
                )
            )

    out = {}
    for ab, rows in by_team.items():
        rows = sorted(rows, key=lambda x: x[0])
        lo3 = (end - timedelta(days=3)).isoformat()
        lo1 = (end - timedelta(days=1)).isoformat()
        l3 = [r for r in rows if r[0] >= lo3]
        l1 = [r for r in rows if r[0] >= lo1]
        # series proxy: last up to 5 appearances (playoff series length)
        series_rows = rows[-5:] if rows else []
        bp_l3 = sum(r[1] for r in l3)
        bp_l1 = sum(r[1] for r in l1)
        series_bp = sum(r[1] for r in series_rows)
        prev = rows[-1] if rows else None
        prev_starter = float(prev[2]) if prev else None
        prev_short = 1.0 if prev is not None and prev[2] <= 5.0 else 0.0
        out[ab] = {
            "bp_ip_L3d": round(bp_l3, 3),
            "bp_ip_L1d": round(bp_l1, 3),
            "series_bp_ip": round(series_bp, 3),
            "prev_starter_ip": prev_starter,
            "prev_short_start": prev_short,
            "games_L3d": len(l3),
        }
    return out


# Playoff OU rich_bp coefficients (fit ≤2021 only).
# Live 2026 PS was 100% Overs with inflated gaps (+2–3 vs holdout ~+0.5) → 2-5 −3.07u.
# Fix: residual shrink toward market line + asymmetric gaps (stricter on Overs).
PLAYOFF_OU_BETA = {
    "intercept": 5.736813462659192,
    "line_f": 0.5353685815786897,
    "sp_siera_avg": 0.7274290128021123,
    "offense_L10": -0.18926188974508282,
    "bp_L3": -0.0559612440594952,
    "bp_L1": 0.06413794400171527,
    "series_bp": 0.024101174209405503,
    "short_starts": -0.015356276067296315,
    "prev_starter_avg": -0.3189115825173517,
    "park": -1.9426530619391378,
}
PLAYOFF_OU_GAP = 0.5  # legacy symmetric (unused when asymmetric set)
PLAYOFF_OU_GAP_OVER = 1.0   # need exp_cal - line >= this for Over
PLAYOFF_OU_GAP_UNDER = 0.75  # need line - exp_cal >= this for Under
PLAYOFF_OU_SHRINK = 0.40     # only trust 40% of (exp - line); rest = market
PLAYOFF_OU_SERIES_BP_CAP = 20.0  # per-team series BP IP proxy cap
PLAYOFF_OU_JUICE_MAX_FAV = -120  # do not lay worse than -120


def _fnum(x, default):
    try:
        if x is None:
            return default
        v = float(x)
        if v != v:
            return default
        return v
    except (TypeError, ValueError):
        return default


def _starter_ip_or_default(x, default=5.5):
    """Treat missing/zero prior-start IP as unknown (default), not true 0."""
    v = _fnum(x, default)
    if v <= 0.05:
        return default
    return v


def playoff_ou_expected_runs_rich_bp(
    line,
    away_siera,
    home_siera,
    away_rs_l10,
    home_rs_l10,
    away_bp_l3,
    home_bp_l3,
    away_bp_l1,
    home_bp_l1,
    away_series_bp,
    home_series_bp,
    away_short,
    home_short,
    away_prev_starter,
    home_prev_starter,
    park,
):
    """rich_bp raw expected total (dev-fit). Apply PLAYOFF_OU_SHRINK at decision time."""
    line_f = _fnum(line, 7.5)
    sp_siera_avg = (_fnum(away_siera, 4.0) + _fnum(home_siera, 4.0)) / 2.0
    offense_L10 = (_fnum(away_rs_l10, 4.5) + _fnum(home_rs_l10, 4.5)) / 2.0
    bp_L3 = _fnum(away_bp_l3, 0.0) + _fnum(home_bp_l3, 0.0)
    bp_L1 = _fnum(away_bp_l1, 0.0) + _fnum(home_bp_l1, 0.0)
    # Cap series BP proxy — live last-5 sum was inflating vs true series load
    cap = PLAYOFF_OU_SERIES_BP_CAP
    series_bp = min(_fnum(away_series_bp, 0.0), cap) + min(_fnum(home_series_bp, 0.0), cap)
    short_starts = _fnum(away_short, 0.0) + _fnum(home_short, 0.0)
    prev_starter_avg = (
        _starter_ip_or_default(away_prev_starter) + _starter_ip_or_default(home_prev_starter)
    ) / 2.0
    park_f = _fnum(park, 1.0)
    if park_f < 0.5 or park_f > 1.6:
        park_f = 1.0
    b = PLAYOFF_OU_BETA
    return (
        b["intercept"]
        + b["line_f"] * line_f
        + b["sp_siera_avg"] * sp_siera_avg
        + b["offense_L10"] * offense_L10
        + b["bp_L3"] * bp_L3
        + b["bp_L1"] * bp_L1
        + b["series_bp"] * series_bp
        + b["short_starts"] * short_starts
        + b["prev_starter_avg"] * prev_starter_avg
        + b["park"] * park_f
    )


def playoff_ou_rule_sides(
    away_name,
    home_name,
    line,
    over_px,
    under_px,
    form,
    pitchers,
    priors,
    parks,
    bullpen_context,
    gap_min=None,
    juice_max_fav=None,
):
    """Playoff OU rich_bp v2: shrunk exp, asymmetric gaps, juice >= -120.

    Returns list of (side_label, price, meta) — at most one side.
    """
    # gap_min legacy arg: if caller passes a single number, use it both sides;
    # otherwise asymmetric PLAYOFF_OU_GAP_OVER / _UNDER.
    use_symmetric = gap_min is not None
    if juice_max_fav is None:
        juice_max_fav = PLAYOFF_OU_JUICE_MAX_FAV
    if line is None or over_px is None or under_px is None:
        return []
    try:
        line_f = float(line)
        ov = int(over_px)
        un = int(under_px)
    except (TypeError, ValueError):
        return []

    aa = team_to_abbr(away_name)
    ha = team_to_abbr(home_name)
    fa = (form or {}).get(aa) or {}
    fh = (form or {}).get(ha) or {}
    pinfo = (pitchers or {}).get((aa, ha)) or {}
    asp = sp_lookup(priors or {}, pinfo.get("away_sp"))
    hsp = sp_lookup(priors or {}, pinfo.get("home_sp"))
    ba = (bullpen_context or {}).get(aa) or {}
    bh = (bullpen_context or {}).get(ha) or {}
    pf = (parks or {}).get(ha)
    if pf is None:
        pf = (parks or {}).get(str(ha).upper())

    exp_raw = playoff_ou_expected_runs_rich_bp(
        line_f,
        asp.get("siera"),
        hsp.get("siera"),
        fa.get("rs_L10"),
        fh.get("rs_L10"),
        ba.get("bp_ip_L3d"),
        bh.get("bp_ip_L3d"),
        ba.get("bp_ip_L1d"),
        bh.get("bp_ip_L1d"),
        ba.get("series_bp_ip"),
        bh.get("series_bp_ip"),
        ba.get("prev_short_start"),
        bh.get("prev_short_start"),
        ba.get("prev_starter_ip"),
        bh.get("prev_starter_ip"),
        pf,
    )
    # Shrink residual toward market — live gaps were 4–6× holdout mean
    shrink = PLAYOFF_OU_SHRINK
    exp = line_f + shrink * (exp_raw - line_f)
    gap = exp - line_f
    if use_symmetric:
        gap_over = gap_under = float(gap_min)
    else:
        gap_over = PLAYOFF_OU_GAP_OVER
        gap_under = PLAYOFF_OU_GAP_UNDER
    if gap >= gap_over:
        side, px, label = "over", ov, "Over"
    elif gap <= -gap_under:
        side, px, label = "under", un, "Under"
    else:
        return []
    if px < juice_max_fav:
        return []
    if abs(px) < 100 or abs(px) > 200:
        return []
    return [
        (
            "%s %s %+d" % (label, line_f, px),
            px,
            {
                "rule": "playoff_ou_rich_bp_v2",
                "edge": "exp %.2f (raw %.2f) vs line %.1f · gap %+.2f" % (exp, exp_raw, line_f, gap),
                "exp_runs": round(exp, 3),
                "exp_raw": round(exp_raw, 3),
                "ou_line": line_f,
                "gap": round(gap, 3),
                "ou_side": side,
                "shrink": shrink,
            },
        )
    ]


def playoff_ml_rule_a_sides(away_name, home_name, ml_away_px, ml_home_px, series_context, bullpen_context):
    """Playoff ML Rule A: elim + opp short start + bp_adv>=1 + price -180..+180.

    Returns list of (side_name, price, meta_dict) for sides that qualify.
    Does not use RS logreg edge.
    """
    aa = team_to_abbr(away_name)
    ha = team_to_abbr(home_name)
    sx = (series_context or {}).get((aa, ha)) or {}
    if not sx.get("is_elimination_game"):
        return []

    ba = (bullpen_context or {}).get(aa) or {}
    bh = (bullpen_context or {}).get(ha) or {}
    away_bp = float(ba.get("bp_ip_L3d") or 0)
    home_bp = float(bh.get("bp_ip_L3d") or 0)
    away_short = float(ba.get("prev_short_start") or 0) >= 1
    home_short = float(bh.get("prev_short_start") or 0) >= 1

    out = []
    # Side qualifies if: opponent had short start, we have fresher pen (bp_adv>=1), price in band
    for side_name, px, our_bp, opp_bp, opp_short in (
        (away_name, ml_away_px, away_bp, home_bp, home_short),
        (home_name, ml_home_px, home_bp, away_bp, away_short),
    ):
        if px is None:
            continue
        try:
            px = int(px)
        except (TypeError, ValueError):
            continue
        if px < -180 or px > 180:
            continue
        if not opp_short:
            continue
        bp_adv = opp_bp - our_bp
        if bp_adv < 1.0:
            continue
        out.append(
            (
                side_name,
                px,
                {
                    "rule": "playoff_ml_rule_a",
                    "edge": "elim · opp short start · bp_adv %.1f" % bp_adv,
                    "bp_adv": round(bp_adv, 3),
                    "our_bp_ip_L3d": round(our_bp, 3),
                    "opp_bp_ip_L3d": round(opp_bp, 3),
                    "is_elimination_game": True,
                    "series_game": sx.get("series_game"),
                },
            )
        )
    return out
