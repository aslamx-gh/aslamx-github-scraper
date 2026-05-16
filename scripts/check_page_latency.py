"""
Smoke-check page latency for the running app.

Usage:
    python3 scripts/check_page_latency.py [--base-url http://0.0.0.0:8000] [--timeout 5]

Exit code 0 if all routes respond within the timeout, 1 if any hang or error.

Designed for local debugging, not CI micro-benchmarking.
"""
from __future__ import annotations

import argparse
import sys
import time

import httpx

ROUTES = [
    # (path, max_expected_seconds, description)
    ("/",                    1.0, "dashboard"),
    ("/failures",            1.0, "failures page"),
    ("/settings",            1.0, "settings page"),
    ("/niches",              1.0, "niches page"),
    ("/schedules",           1.0, "schedules page"),
    ("/github",              1.0, "github diagnostics page"),
    ("/api/runs",            1.0, "runs API"),
    ("/api/github/status",   8.0, "github status (network, may be slow)"),
]


def check(base_url: str, request_timeout: float) -> bool:
    all_ok = True
    print(f"\nChecking {base_url}  (hard timeout per request: {request_timeout}s)\n")
    print(f"{'Route':<32} {'Time':>8}  {'Status':>6}  {'Result'}")
    print("-" * 70)

    for path, warn_threshold, desc in ROUTES:
        url = base_url.rstrip("/") + path
        t0 = time.perf_counter()
        try:
            r = httpx.get(url, timeout=request_timeout, follow_redirects=False)
            elapsed = time.perf_counter() - t0
            status = r.status_code
            slow = elapsed > warn_threshold
            tag = "SLOW" if slow else "ok"
            if status >= 400 or slow:
                all_ok = False
            print(f"{path:<32} {elapsed:>7.3f}s  {status:>6}  {tag}  ({desc})")
        except httpx.TimeoutException:
            elapsed = time.perf_counter() - t0
            all_ok = False
            print(f"{path:<32} {elapsed:>7.3f}s  TIMEOUT        FAIL  ({desc})")
        except httpx.ConnectError as e:
            all_ok = False
            print(f"{path:<32}   ERROR    CONN   FAIL  ({e})")

    print()
    if all_ok:
        print("All routes responded within expected thresholds.")
    else:
        print("One or more routes were slow, timed out, or returned an error.")
    return all_ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Page latency smoke check")
    parser.add_argument("--base-url", default="http://0.0.0.0:8000",
                        help="Base URL of the running app (default: http://0.0.0.0:8000)")
    parser.add_argument("--timeout", type=float, default=12.0,
                        help="Hard per-request timeout in seconds (default: 12)")
    args = parser.parse_args()

    ok = check(args.base_url, args.timeout)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
