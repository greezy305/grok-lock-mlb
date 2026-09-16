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
    """Season WP + L10 RS/RA/RD from MLB schedule API."""
    from datetime import datetime, timedelta

    end = datetime.strptime(day_iso, "%Y-%m-%d").date()
    start = end - timedelta(days=lookback_days)
    # season start roughly
    season_start = f"{end.year}-03-20"
    url = (
        "https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate=%s&endDate=%s"
        "&hydrate=linescore,team"
    ) % (season_start, day_iso)
    try:
        data = fetch_json(url, timeout=45)
    except Exception as e:
        print("team_form_error", type(e).__name__, e)
        return {}

    # per team list of results before day_iso (finals only)
    games_by = {}
    season_w = {}
    season_n = {}
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

    out = {}
    for ab, games in games_by.items():
        games = sorted(games, key=lambda x: x[0])[-10:]
        if not games:
            continue
        rs = sum(g[1] for g in games) / len(games)
        ra = sum(g[2] for g in games) / len(games)
        n = season_n.get(ab, 0) or 1
        out[ab] = {
            "wp_sea": season_w.get(ab, 0) / n,
            "rs_L10": rs,
            "ra_L10": ra,
            "rd_L10": rs - ra,
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
):
    """Feature dict for frozen model. Uses current ML as close proxy at lock time."""
    aa = team_to_abbr(away_name)
    ha = team_to_abbr(home_name)
    fa = form.get(aa) or {}
    fh = form.get(ha) or {}
    pinfo = pitchers.get((aa, ha)) or {}
    asp = sp_lookup(priors, pinfo.get("away_sp"))
    hsp = sp_lookup(priors, pinfo.get("home_sp"))

    # Odds features — current as close proxy
    home_imp = american_to_prob(ml_home)
    away_imp = american_to_prob(ml_away)
    # moves in American points (current - open)
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
        "home_bp_era_L30": None,  # median fill until live BP feed
        "away_bp_era_L30": None,
        "home_sp_kbb_prior": hsp.get("kbb"),
        "away_sp_kbb_prior": asp.get("kbb"),
        "home_sp_stuff_prior": hsp.get("stuff"),
        "away_sp_stuff_prior": asp.get("stuff"),
        "wind_out_mph": None,  # median fill
        "temp_f": None,
    }
    meta = {
        "away_abbr": aa,
        "home_abbr": ha,
        "away_sp": pinfo.get("away_sp") or "",
        "home_sp": pinfo.get("home_sp") or "",
        "away_sp_matched": bool(asp),
        "home_sp_matched": bool(hsp),
    }
    return feats, meta
