#!/usr/bin/env python3
"""Diagnose why OddsPortal AJAX returns 0 rows on your network.

Run with:
    PYTHONPATH=src python3 scripts/diagnose_oddsportal.py

This script will:
1. Open ATP_WIMBLEDON in Playwright (warms CF cookies)
2. Extract the page_id from the rendered DOM
3. Hit the AJAX archive endpoint from inside the browser
4. Print the response: length, first chars, CF-challenge status, JSON parse status, parser row count
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from racketfactory.oddsportal import (
    _ajax_archive_url,
    _extract_page_id,
    _is_cloudflare_challenge,
    _is_error_page,
    _parse_ajax_payload,
    fetch_rendered_html,
)


def main() -> int:
    url = "https://www.oddsportal.com/tennis/united-kingdom/atp-wimbledon/results/"
    print(f"[1] Fetching {url} with Playwright (this warms CF cookies)...")
    html = fetch_rendered_html(url, timeout_ms=90000)
    print(f"    HTML length: {len(html)}")
    if _is_cloudflare_challenge(html):
        print("    !! HTML is a CF challenge page. Try ODDSPORTAL_IMPERSONATE=chrome124 or use a VPN.")
        return 1
    if _is_error_page(html):
        print("    !! HTML is a 404/5xx error page.")
        return 1
    page_id = _extract_page_id(html)
    print(f"[2] Extracted page_id: {page_id!r}")
    if not page_id:
        print("    !! No page_id found in rendered HTML. Check HANDOVER.md regex patterns.")
        return 1

    # Now manually do an AJAX call inside Playwright to see the raw response
    from playwright.sync_api import sync_playwright

    ajax_url = _ajax_archive_url(page_id, page_num=1, year=2024)
    print(f"\n[3] Hitting AJAX: {ajax_url}")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ])
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 1200},
            locale="en-GB",
            timezone_id="UTC",
        )
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.navigator.chrome = {runtime: {}};
        """)
        page = context.new_page()
        # Load the tournament page first to set cookies
        print(f"    Opening {url} in browser...")
        page.goto(url, wait_until="commit", timeout=90000)
        # Wait for real content
        import time
        time.sleep(5)

        # Now do the AJAX fetch in-browser
        print(f"    Issuing in-browser fetch to {ajax_url[:80]}...")
        result = page.evaluate("""async (url) => {
            try {
                const resp = await fetch(url, {
                    credentials: 'include',
                    headers: {
                        'x-requested-with': 'XMLHttpRequest',
                        'accept': '*/*',
                    },
                });
                const text = await resp.text();
                return {
                    status: resp.status,
                    headers: Object.fromEntries(resp.headers.entries()),
                    body: text,
                };
            } catch (e) {
                return {error: String(e)};
            }
        }""", ajax_url)

        browser.close()

    if "error" in result:
        print(f"    !! Fetch error: {result['error']}")
        return 1

    print(f"\n[4] AJAX response:")
    print(f"    HTTP status: {result['status']}")
    body = result["body"]
    print(f"    Body length: {len(body)}")
    print(f"    First 300 chars: {body[:300]!r}")
    print(f"    Last 200 chars: {body[-200:]!r}")

    # Classify the response
    if _is_cloudflare_challenge(body):
        print("    !! Body is a CF challenge page.")
    elif _is_error_page(body):
        print("    !! Body is a 404/5xx error page.")
    else:
        # Try parsing
        try:
            payload = json.loads(body)
            print(f"    Body parses as JSON. Top-level keys: {list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__}")
            if isinstance(payload, dict):
                d = payload.get("d") or payload
                if isinstance(d, dict):
                    html_frag = d.get("html") or d.get("result") or ""
                    print(f"    d.html length: {len(html_frag)}")
                    if html_frag:
                        print(f"    d.html first 200 chars: {html_frag[:200]!r}")
        except json.JSONDecodeError:
            print(f"    Body is NOT JSON — looks like raw HTML")

        # Try the actual parser
        rows = _parse_ajax_payload(body, tour="ATP", tournament="Wimbledon", default_year=2024, oddsportal_url=url)
        print(f"    Parser extracted {len(rows)} rows")
        if rows:
            print(f"    First row: {rows[0]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
