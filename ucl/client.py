"""HTTP client for the public UCL Fantasy feeds.

Polite by design: identifies itself, caches responses on disk, rate-limits,
and retries a little on transient errors. See docs/endpoints.md.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

import requests

from .config import CACHE_DIR

BASE = "https://gaming.uefa.com/en/uclfantasy/services/feeds/"
UA = "ucl-fantasy-personal/0.1 (personal analysis tool)"


class FeedClient:
    def __init__(self, tour_id: int = 90, min_interval: float = 0.25, cache_dir: Path = CACHE_DIR):
        self.tour = tour_id
        self.min_interval = min_interval
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.s = requests.Session()
        self.s.headers["User-Agent"] = UA
        self._lock = threading.Lock()
        self._last = 0.0

    # -- low level -------------------------------------------------------
    def _throttle(self) -> None:
        with self._lock:
            wait = self._last + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def fetch(self, url: str, max_age: float = 3600, as_text: bool = False):
        """GET any URL with the disk cache. JSON is returned parsed (raw payload), text as str."""
        key = hashlib.sha1(url.encode()).hexdigest()[:16]
        cpath = self.cache_dir / f"{key}.{'txt' if as_text else 'json'}"
        if cpath.exists() and time.time() - cpath.stat().st_mtime < max_age:
            raw = cpath.read_text(encoding="utf-8")
            return raw if as_text else json.loads(raw)
        for attempt in range(3):
            self._throttle()
            try:
                r = self.s.get(url, timeout=30)
            except requests.RequestException:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                if as_text:
                    r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else "utf-8-sig"
                    out = r.text.lstrip("\ufeff")
                    cpath.write_text(out, encoding="utf-8")
                    return out
                out = r.json()
                cpath.write_text(json.dumps(out))
                return out
            if r.status_code in (403, 404):
                return None
            time.sleep(2 ** attempt)
        return None

    def get(self, path: str, max_age: float = 3600) -> dict | list | None:
        """GET a fantasy feed path; returns data.value. Cached for max_age seconds."""
        payload = self.fetch(BASE + path, max_age=max_age)
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"].get("value")
        return payload

    # -- feeds -----------------------------------------------------------
    def constraints(self):
        return self.get(f"constraints/constraints_{self.tour}.json", max_age=600)

    def teams(self):
        return self.get(f"teams/teams_{self.tour}_en.json", max_age=6 * 3600)

    def fixtures(self):
        return self.get(f"fixtures/fixtures_{self.tour}_en.json", max_age=1800)

    def players(self, md_id: int):
        v = self.get(f"players/players_{self.tour}_en_{md_id}.json", max_age=1800)
        return v["playerList"] if isinstance(v, dict) else v

    def popupstats(self, player_id: str, max_age: float = 12 * 3600):
        return self.get(f"popupstats/popupstats_{self.tour}_{player_id}.json", max_age=max_age)

    def match_player_stats(self, match_id: str, max_age: float = 30 * 86400):
        """UEFA per-player match statistics (shots, key passes, ...). Finished matches don't change."""
        return self.fetch(f"https://matchstats.uefa.com/v2/player-statistics/{match_id}", max_age=max_age)

    def football_data_csv(self, season_code: str, div: str, max_age: float = 12 * 3600):
        """football-data.co.uk league results with team xG + odds, e.g. ('2627', 'E0')."""
        return self.fetch(f"https://www.football-data.co.uk/mmz4281/{season_code}/{div}.csv",
                          max_age=max_age, as_text=True)

    def football_data_extra(self, country: str, max_age: float = 12 * 3600):
        """football-data.co.uk 'extra' leagues (all seasons in one file, odds but no xG), e.g. 'NOR'."""
        return self.fetch(f"https://www.football-data.co.uk/new/{country}.csv", max_age=max_age, as_text=True)

    def uefa_matches(self, competition: int, season_year: int, offset: int = 0, limit: int = 100,
                     max_age: float = 12 * 3600):
        """UEFA match list (all rounds incl. qualifiers). season_year = year the season ends."""
        return self.fetch(f"https://match.uefa.com/v5/matches?competitionId={competition}&seasonYear={season_year}"
                          f"&offset={offset}&limit={limit}&order=ASC", max_age=max_age)

    def domestic_form(self, player_id: str, max_age: float = 12 * 3600):
        return self.get(f"domesticform/details_{self.tour}_{player_id}.json", max_age=max_age)
