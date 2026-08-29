#!/usr/bin/env python3
"""Daily GREEN lock pass: MLB slate + Hard Rock Bet odds + locked filters."""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SPORT = "baseball_mlb"
BOOKS = ["hardrockbet_fl", "hardrockbet"]
MARKETS = "h2h,spreads,totals"
UNIT = 50

# Locked filters
ML_EDGE = 0.07
ML_CAP = -180
ML_PLUS_CAP = 200
RL_EDGE = 0.03
RL_JUICE_LO, RL_JUICE_HI = -130, 170
OU_EDGE = 0.04
OU_BASE = 0.524


def et_now():
    return datetime.now(ET)


def american_to_prob(odds: float) -> float:
    o = float(odds)
    if o < 0:
        return abs(o) / (abs(o) + 100.0)
    return 100.0 / (o + 100.0)


def get_json(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": "green-lock/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def pull_odds(api_key: str, book: str):
    q = urllib.parse.urlencode(
        {
            "apiKey": api_key,
            "bookmakers": book,
            "markets": MARKETS,
            "oddsFormat": "american",
        }
    )
    url = f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds?{q}"
    data = get_json(url)
    return data if isinstance(data, list) else []


def pull_slate(day: str):
    url = (
        "https://statsapi.mlb.com/api/v1/schedule"
        f"?sportId=1&date={day}&hydrate=probablePitcher,team"
    )
    data = get_json(url)
    games = []
    for d in data.get("dates") or []:
        for g in d.get("games") or 
