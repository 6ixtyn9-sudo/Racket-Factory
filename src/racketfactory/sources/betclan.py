"""
BetClan Predictor Adapter
Forward-capture of daily AI predictions.
"""
from __future__ import annotations
import logging
import re
import time
import json
import os
from pathlib import Path
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from typing import Any, Optional
from datetime import date, datetime, timedelta
from curl_cffi import requests

from racketfactory.entities import normalize_player
from racketfactory.kickoff import BETCLAN_TZ, SAST, parse_source_timestamp, to_sast_parts
from racketfactory.sources._page_odds import PageFetchError

logger = logging.getLogger(__name__)


def _run_status_path() -> Path:
    return Path(__file__).resolve().parents[3] / "localdata" / "fetch_cache" / "betclan_run_status.json"


def current_run_status(run_as_of: str) -> str:
    """A cache hit alone does not establish successful current-run collection."""
    if not run_as_of:
        return "unknown"
    try:
        data = json.loads(_run_status_path().read_text())
        if data.get("run_as_of") != run_as_of:
            return "unknown"
        return data.get("status") if data.get("status") in {"complete", "partial", "unknown", "failed"} else "unknown"
    except (OSError, ValueError, AttributeError):
        return "unknown"


def _record_run_status(status: str, count: int, profile: dict) -> None:
    run_as_of = os.getenv("RACKET_FACTORY_RUN_AS_OF", "").strip()
    if not run_as_of:
        return
    try:
        path = _run_status_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"run_as_of": run_as_of, "status": status, "rows": count,
                                    "completed_at": datetime.now(SAST).isoformat(), "profile": profile}))
    except OSError:
        logger.warning("Could not persist BetClan run status; completeness remains unknown")


def extract_kickoff_sast(page_text: str, target_date: str) -> tuple[str, str]:
    """First kickoff stamp on a BetClan page, normalised to SAST.

    BetClan stamps kickoffs with no explicit offset and they measured
    exactly UTC+1 on 2026-09-24/25 (Betway's SAST time minus 1h on every
    singles checked), so naive stamps are read as BETCLAN_TZ (fixed
    UTC+1). When a stamp does carry an offset ("Z" / "+HH:MM") it is
    honoured instead. Falls back to the SAST target date at midnight when
    the page shows no stamp at all.
    """
    m = re.search(
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)",
        page_text or "",
    )
    if m:
        ts = parse_source_timestamp(m.group(1), default_tz=BETCLAN_TZ)
        if ts is not None:
            return to_sast_parts(ts)
    return target_date, "00:00"

class BetClanPredictor:
    def __init__(self):
        self.base_url = "https://www.betclan.com"
        self._request_stats = None
        self._request_urls = set()
        self._odds_subpage_cache = {}
        self._odds_restricted = False

    def _get(self, url: str, category: str, *, timeout: int):
        started = time.monotonic()
        stats = self._request_stats
        by_category = stats["by_category"][category] if stats is not None else None
        status_key = None
        transport_failed = False
        if stats is not None:
            stats[category + "_requests"] += 1
            by_category["requests"] += 1
            if url in self._request_urls:
                stats["repeated_url_requests"] += 1
            self._request_urls.add(url)
        try:
            # Do not follow a discovered optional link to a different host or event.
            kwargs = {"allow_redirects": False} if category == "odds_subpage" else {}
            response = requests.get(url, impersonate="chrome133a", timeout=timeout, **kwargs)
            if stats is not None:
                code = str(response.status_code)
                codes = by_category["http_statuses"]
                code = code if code in codes or len(codes) < 16 else "other"
                status_key = code
                codes[code] = codes.get(code, 0) + 1
                if response.status_code != 200:
                    stats["http_failures"] += 1
                    by_category["http_failures"] += 1
            return response
        except Exception:
            transport_failed = True
            if stats is not None:
                stats["transport_failures"] += 1
                by_category["transport_failures"] += 1
            raise
        finally:
            if stats is not None:
                elapsed = time.monotonic() - started
                stats["request_seconds"] += elapsed
                by_category["request_seconds"] += elapsed
                if status_key is not None:
                    durations = by_category["http_status_seconds"]
                    durations[status_key] = durations.get(status_key, 0.0) + elapsed
                elif transport_failed:
                    by_category["transport_failure_seconds"] += elapsed

    def _clean_text(self, value: str) -> str:
        return " ".join(str(value or "").split()).strip()

    @staticmethod
    def _extract_odds_from_soup(soup: BeautifulSoup) -> tuple[Optional[float], Optional[float]]:
        """
        Best-effort decimal odds extraction from any BetClan page. Returns
        (odds_home, odds_away). Either may be None if not found.

        Strategies, in order:
          1. Iterate elements with class containing odds/odd/price/etc.
          2. Walk every <table> row and look for two decimals per row.
          3. Fall back to a global regex over the entire page text.
        The function stops at the first strategy that yields two numeric odds
        because once we have a pair, deeper guesses are more likely wrong than
        right. Single-odds matches are accumulated separately.
        """
        odds_home: Optional[float] = None
        odds_away: Optional[float] = None

        # Strategy 1: class-driven scanning — broader class set than the
        # historical inline scrape, including 'price', 'coef', 'decimal',
        # 'quote' (BetClan variant) and the Portuguese/Spanish 'cuota'.
        # Use a separator on get_text so adjacent spans/divs don't fuse their
        # numbers into one malformed token like "1.852.10".
        for tag in soup.find_all(["div", "span", "td", "button", "a", "b", "i", "strong", "em"]):
            text = tag.get_text(" ", strip=True)
            cls_str = " ".join(str(c) for c in (tag.get("class") or [])).lower()
            id_str = str(tag.get("id") or "").lower()
            haystack = cls_str + " " + id_str
            if not any(w in haystack for w in (
                "odds", "odd", "odd-val", "bet-odd", "box-odds", "price",
                "val", "bet", "book", "market", "quote", "coef", "decimal",
                "payout", "1x2", "moneyline",
            )):
                continue
            matches = re.findall(r"\b([1-9]\.\d{1,3})\b", text)
            if len(matches) >= 2:
                try:
                    return float(matches[0]), float(matches[1])
                except (TypeError, ValueError):
                    continue
            if len(matches) == 1:
                try:
                    v = float(matches[0])
                    if odds_home is None:
                        odds_home = v
                    elif odds_away is None and abs(v - odds_home) > 0.02:
                        odds_away = v
                except (TypeError, ValueError):
                    continue

        # Strategy 2: walk every <table> row for two-decimal patterns.
        for table in soup.find_all("table"):
            for tr in table.find_all("tr"):
                row_text = tr.get_text(" ", strip=True)
                row_matches = re.findall(r"\b([1-9]\.\d{1,3})\b", row_text)
                if len(row_matches) >= 2:
                    try:
                        return float(row_matches[0]), float(row_matches[1])
                    except (TypeError, ValueError):
                        continue

        # Strategy 3: global regex fallback. Only act if we found a clean pair.
        if odds_home is None or odds_away is None:
            full_text = soup.get_text(" ", strip=True)
            full_matches = re.findall(r"\b([1-9]\.\d{1,3})\b", full_text)
            # Heuristic: the first two decimal odds near the match header are
            # usually the home/away odds; subsequent ones are other markets.
            if len(full_matches) >= 2:
                try:
                    if odds_home is None:
                        odds_home = float(full_matches[0])
                    if odds_away is None:
                        odds_away = float(full_matches[1])
                except (TypeError, ValueError):
                    pass

        return odds_home, odds_away

    def _find_odds_subpage(self, prediction_soup: BeautifulSoup, prediction_url: str) -> list[str]:
        """Only follow explicit same-host links bound to the same event slug.

        Link text alone is not identity. Reject query strings and redirects:
        no guessed parallel routes, tracking parameters or cross-event prices.
        """
        parsed = urlparse(prediction_url)
        match = re.fullmatch(r"/tennis/predictionsdetails/([A-Za-z0-9_-]+)/?", parsed.path)
        if (parsed.scheme != "https" or parsed.netloc.lower() != "www.betclan.com"
                or parsed.query or parsed.fragment or not match):
            return []
        slug = match.group(1)
        result = []
        seen = set()
        for anchor in prediction_soup.find_all("a", href=True):
            href = str(anchor.get("href") or "").strip()
            try:
                url = urljoin(prediction_url, href)
                link = urlparse(url)
                parts = link.path.strip("/").split("/")
                if (link.scheme != "https" or link.netloc.lower() != "www.betclan.com"
                        or link.query or link.fragment or len(parts) < 3 or parts[0] != "tennis"
                        or parts[-1] != slug or any(x in {".", ".."} for x in parts)
                        or not re.fullmatch(r"/[A-Za-z0-9_/-]+/?", link.path)
                        or not any(k in '/'.join(parts[1:-1]).lower() for k in
                                   ("odds", "bookmaker", "market", "prices"))):
                    continue
                key = link.path.rstrip('/')
                if key not in seen:
                    seen.add(key)
                    # Keep the exact discovered path, including its trailing slash.
                    result.append(f"https://www.betclan.com{link.path}")
            except ValueError:
                continue
        return result

    def _fetch_odds_subpage(self, url: str) -> Optional[BeautifulSoup]:
        """Fetch a verified optional link once per run; restriction stops this path."""
        if self._odds_restricted:
            if self._request_stats is not None:
                self._request_stats["odds_subpage_restriction_skips"] += 1
            return None
        if url in self._odds_subpage_cache:
            if self._request_stats is not None:
                self._request_stats["odds_subpage_duplicate_skips"] += 1
            return self._odds_subpage_cache[url]
        soup = None
        try:
            resp = self._get(url, "odds_subpage", timeout=15)
            if resp.status_code in (401, 403, 429):
                self._odds_restricted = True
            elif resp.status_code == 200:
                text = resp.text
                if any(marker in text[:6000].lower() for marker in
                       ("cf-chl", "just a moment", "attention required", "verify you are human")):
                    self._odds_restricted = True
                else:
                    soup = BeautifulSoup(text, "html.parser")
        except Exception:
            logger.debug("BetClan optional odds request failed")
        self._odds_subpage_cache[url] = soup
        return soup

    def _extract_context(self, soup: BeautifulSoup) -> tuple[str, str, str]:
        """Extract focused BetClan context with minimal bleed from nearby widgets."""
        primary_selectors = [
            ("span", "titleh2page"),
            ("div", "portlet-title"),
            ("h1", None),
            ("h2", None),
        ]
        fallback_selectors = [
            ("div", "caption"),
            ("div", "breadcrumb"),
            ("ul", "breadcrumb"),
        ]

        candidates: list[str] = []
        for tag_name, class_name in primary_selectors + fallback_selectors:
            found = soup.find(tag_name, class_=class_name) if class_name else soup.find(tag_name)
            if found:
                text = self._clean_text(found.get_text(" ", strip=True))
                if text:
                    candidates.append(text)

        title_text = self._clean_text(soup.title.get_text(" ", strip=True) if soup.title else "")
        if title_text:
            candidates.append(title_text)

        deduped = list(dict.fromkeys(candidates))
        tournament = deduped[0] if deduped else ""
        event_text = tournament
        category_text = " | ".join(
            text
            for text in deduped
            if re.search(
                r"\b(?:ATP|WTA|ITF|Challenger|Men(?:'s)?|Women(?:'s)?|Boys|Girls|Doubles|Singles|WD|MD)\b",
                text,
                flags=re.IGNORECASE,
            )
        )
        return tournament, event_text, category_text

    def fetch_daily(self) -> list[dict[str, Any]]:
        results = []
        started = time.monotonic()
        self._request_urls = set()
        self._odds_subpage_cache = {}
        self._odds_restricted = False
        self._request_stats = {"listing_requests": 0, "detail_requests": 0, "odds_subpage_requests": 0,
                               "repeated_url_requests": 0, "http_failures": 0, "transport_failures": 0,
                               "request_seconds": 0.0,
                               "detail_duplicate_skips": 0, "today_detail_reused": 0,
                               "odds_subpage_duplicate_skips": 0, "odds_subpage_restriction_skips": 0,
                               "by_category": {category: {"requests": 0, "http_failures": 0,
                                   "transport_failures": 0, "request_seconds": 0.0, "http_statuses": {},
                                   "http_status_seconds": {}, "transport_failure_seconds": 0.0}
                                   for category in ("listing", "detail", "odds_subpage")}}
        today_ok = False
        today_links = 0
        today_failed = 0
        today_attempted = 0
        today_succeeded = 0
        today_failure_kinds = {"http": 0, "participant_markup": 0, "winner_markup": 0,
                               "winner_ambiguous": 0, "exception": 0}
        today_empty_verified = False
        detail_cache = {}  # same observation, including failures, within this collection only
        # Fetch today and tomorrow
        for day_offset, endpoint in [(-1, "yesterdays-tennis-predictions"), (0, "todays-tennis-predictions"), (1, "tomorrows-tennis-predictions")]:
            target_date = (datetime.now(SAST).date() + timedelta(days=day_offset)).strftime("%Y-%m-%d")
            url = f"{self.base_url}/{endpoint}/"
            try:
                resp = self._get(url, "listing", timeout=20)
                if resp.status_code != 200:
                    logger.warning("BetClan %s HTTP %s", endpoint, resp.status_code)
                    continue
            except Exception as e:
                logger.error(f"BetClan request failed: {e}")
                continue
            if day_offset == 0:
                today_ok = True

            links = re.findall(r'https://www\.betclan\.com/tennis/predictionsdetails/[^"\']+', resp.text)
            if day_offset == 0:
                today_links = len(set(links))
                today_empty_verified = not links and any(marker in resp.text.lower() for marker in ("no matches", "no events", "no predictions"))

            for match_url in set(links):
                if day_offset == 0:
                    today_attempted += 1
                try:
                    if match_url in detail_cache:
                        self._request_stats["detail_duplicate_skips"] += 1
                        if day_offset == 0:
                            self._request_stats["today_detail_reused"] += 1
                        r, error = detail_cache[match_url]
                    else:
                        try:
                            r = self._get(match_url, "detail", timeout=20)
                            error = None
                        except Exception as exc:
                            r, error = None, exc
                        detail_cache[match_url] = (r, error)
                    if error is not None:
                        raise error
                    if r.status_code != 200:
                        if day_offset == 0:
                            today_failed += 1
                            today_failure_kinds["http"] += 1
                        continue
                    s = BeautifulSoup(r.text, 'html.parser')

                    home_div = s.find('div', class_='teamtophome')
                    away_div = s.find('div', class_='teamtopaway')
                    if not home_div or not away_div:
                        if day_offset == 0:
                            today_failed += 1
                            today_failure_kinds["participant_markup"] += 1
                        continue

                    p1 = home_div.text.strip()
                    p2 = away_div.text.strip()

                    winner_tag = s.find(lambda tag: tag.name == "h4" and "winner" in tag.text.lower())
                    if not winner_tag:
                        if day_offset == 0:
                            today_failed += 1
                            today_failure_kinds["winner_markup"] += 1
                        continue
                    next_sib = winner_tag.find_next_sibling("h5")
                    winner_name = next_sib.text.strip() if next_sib else ""

                    vote_container = s.find('div', class_='cell__section vote__team js-vote-stats-bar')
                    x_container = s.find('div', class_='cell__section vote__x js-vote-stats-bar')

                    prob1, prob2 = None, None
                    if vote_container and 'width' in vote_container.get('style', ''):
                        m = re.search(r"width:\s*(\d+)%", str(vote_container.get('style', '')))
                        if m:
                            prob1 = int(m.group(1))
                    if x_container and 'width' in x_container.get('style', ''):
                        m = re.search(r"width:\s*(\d+)%", str(x_container.get('style', '')))
                        if m:
                            prob2 = int(m.group(1))

                    # Robustly determine predicted winner using probabilities or clean name matching
                    # FIX: Normalize to player_a/player_b canonical form (was 1/2, caused Disagree with Forebet/Bzzoiro)
                    if prob1 is not None and prob2 is not None:
                        pred_win = "player_a" if prob1 >= prob2 else "player_b"
                    else:
                        clean_w = re.sub(r"[^a-z]", "", winner_name.lower())
                        clean_p1 = re.sub(r"[^a-z]", "", p1.lower())
                        clean_p2 = re.sub(r"[^a-z]", "", p2.lower())
                        home_match = bool(clean_w and clean_p1 and (clean_p1 in clean_w or clean_w in clean_p1))
                        away_match = bool(clean_w and clean_p2 and (clean_p2 in clean_w or clean_w in clean_p2))
                        if home_match == away_match:
                            if day_offset == 0:
                                today_failed += 1
                                today_failure_kinds["winner_ambiguous"] += 1
                            continue
                        pred_win = "player_a" if home_match else "player_b"

                    # Robustly extract bookmaker odds from the prediction page itself.
                    # The prediction may contain odds; an optional subpage is
                    # considered only when the page explicitly links it.
                    odds_home, odds_away = self._extract_odds_from_soup(s)

                    # Optional odds only: no guessed routes, at most two
                    # validated page-derived links per match.
                    if odds_home is None or odds_away is None:
                        for sub_url in self._find_odds_subpage(s, match_url)[:2]:
                            sub_soup = self._fetch_odds_subpage(sub_url)
                            if sub_soup is None:
                                continue
                            sub_home, sub_away = self._extract_odds_from_soup(sub_soup)
                            if sub_home is not None and sub_away is not None:
                                odds_home, odds_away = sub_home, sub_away
                                break

                    page_text = s.get_text(" ", strip=True)
                    tournament, event_text, category_text = self._extract_context(s)

                    surface = ""
                    surface_m = re.search(r'\b(Grass|Clay|Hard)\b', page_text, re.IGNORECASE)
                    if surface_m:
                        surface = surface_m.group(1).strip()

                    match_date, match_time = extract_kickoff_sast(r.text, target_date)

                    if not any(
                        str(existing.get("match_date", "")) == match_date
                        and str(existing.get("player_home", "")).strip().lower() == p1.strip().lower()
                        and str(existing.get("player_away", "")).strip().lower() == p2.strip().lower()
                        for existing in results
                    ):
                        results.append({
                            "match_date": match_date,
                            "match_time": match_time,
                            "player_home": p1,
                            "player_away": p2,
                            "prob_home": prob1,
                            "prob_away": prob2,
                            "odds_home": odds_home,
                            "odds_away": odds_away,
                            "predicted_winner": pred_win,
                            "predicted_winner_name": winner_name,
                            "tournament": tournament,
                            "surface": surface,
                            "event_text": event_text or tournament,
                            "category": category_text,
                            "source": "BetClan",
                            "source_url": match_url,
                        })
                    if day_offset == 0:
                        today_succeeded += 1
                except Exception as e:
                    if day_offset == 0:
                        today_failed += 1
                        today_failure_kinds["exception"] += 1
                    logger.warning(f"Error parsing BetClan match {match_url}: {e}")

        self._request_stats.update({"today_listing_ok": today_ok,
                                    "today_discovered_unique": today_links,
                                    "today_detail_attempted": today_attempted - self._request_stats["today_detail_reused"],
                                    "today_detail_processed": today_attempted,
                                    "today_detail_parsed": today_succeeded,
                                    "today_detail_failed": today_failed,
                                    "today_detail_failure_kinds": today_failure_kinds})
        for category in self._request_stats["by_category"].values():
            category["request_seconds"] = round(category["request_seconds"], 2)
            category["transport_failure_seconds"] = round(category["transport_failure_seconds"], 2)
            category["http_status_seconds"] = {key: round(value, 2)
                                                for key, value in category["http_status_seconds"].items()}
        self._request_stats["request_seconds"] = round(self._request_stats["request_seconds"], 2)
        self._request_stats["elapsed_seconds"] = round(time.monotonic() - started, 2)
        self._request_stats["unique_urls"] = len(self._request_urls)
        self.last_request_stats = dict(self._request_stats)
        logger.info("BetClan collection profile: %s", self.last_request_stats)
        self._request_stats = None
        if not today_ok:
            self.last_collection_status = "failed"
            _record_run_status(self.last_collection_status, len(results), self.last_request_stats)
            raise PageFetchError("transport", "BetClan today listing unavailable")
        self.last_collection_status = ("partial" if today_failed else
            "complete" if today_links or today_empty_verified else "unknown")
        _record_run_status(self.last_collection_status, len(results), self.last_request_stats)
        return results