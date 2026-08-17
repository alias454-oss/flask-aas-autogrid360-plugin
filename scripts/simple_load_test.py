#!/usr/bin/env python3
# scripts/simple_load_test.py
"""Profile realistic read-only AutoGrid360 demo browsing traffic.

This is intentionally a small, dependency-free traffic profiler rather than a
requests-per-second benchmark. Each simulated visitor:

1. opens the public inventory page;
2. optionally opens one or more operator-supplied extra paths (for example a
   copied search/filter URL);
3. discovers listing links from the returned HTML;
4. opens a configurable number of listing detail pages;
5. downloads a configurable number of images discovered on each listing;
6. opens one seller profile when a seller link is discovered.

Only same-origin GET requests are made. No forms are submitted and no
authenticated/admin routes are intentionally requested.

Examples:

    python scripts/simple_load_test.py \
        --base-url http://127.0.0.1:5000 \
        --users 100 --concurrency 1

    python scripts/simple_load_test.py \
        --base-url http://127.0.0.1:5000 \
        --users 100 --concurrency 5 \
        --extra-path '/autogrid360/?make=Chevrolet'

    python scripts/simple_load_test.py \
        --base-url http://127.0.0.1:5000 \
        --users 100 --concurrency 20 \
        --listings-per-user 3 --images-per-listing 4 \
        --json-out /tmp/autogrid360-profile.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, build_opener


USER_AGENT = "AutoGrid360-Demo-Profiler/1.0"
DEFAULT_INVENTORY_PATH = "/autogrid360/"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


class LinkParser(HTMLParser):
    """Collect href/src attributes without external parser dependencies."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a" and values.get("href"):
            self.links.append(values["href"] or "")
        elif tag == "img" and values.get("src"):
            self.images.append(values["src"] or "")


@dataclass
class RequestResult:
    url: str
    category: str
    status: int | None
    elapsed_ms: float
    body_bytes: int
    content_type: str
    error: str | None = None
    html: str | None = None


@dataclass
class SessionResult:
    visitor_id: int
    requests: list[RequestResult] = field(default_factory=list)


class Collector:
    """Thread-safe aggregate request collector."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests: list[RequestResult] = []

    def add_many(self, values: Iterable[RequestResult]) -> None:
        with self._lock:
            self.requests.extend(values)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simulate realistic read-only AutoGrid360 demo visitors."
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:5000",
        help="Application origin, e.g. http://127.0.0.1:5000",
    )
    parser.add_argument(
        "--inventory-path",
        default=DEFAULT_INVENTORY_PATH,
        help=f"Public inventory path (default: {DEFAULT_INVENTORY_PATH})",
    )
    parser.add_argument(
        "--extra-path",
        action="append",
        default=[],
        help=(
            "Additional same-origin public path/URL visited once per session. "
            "Repeat for copied search/filter URLs."
        ),
    )
    parser.add_argument("--users", type=int, default=100, help="Total visitor sessions")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Concurrent visitor sessions, not raw request concurrency",
    )
    parser.add_argument(
        "--listings-per-user",
        type=int,
        default=2,
        help="Listing detail pages opened by each visitor",
    )
    parser.add_argument(
        "--images-per-listing",
        type=int,
        default=4,
        help="Listing images downloaded from each opened listing",
    )
    parser.add_argument(
        "--seller-pages-per-user",
        type=int,
        choices=(0, 1),
        default=1,
        help="Open one discovered seller page per visitor (default: 1)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout in seconds",
    )
    parser.add_argument(
        "--think-min",
        type=float,
        default=0.0,
        help="Minimum delay between page actions in seconds",
    )
    parser.add_argument(
        "--think-max",
        type=float,
        default=0.0,
        help="Maximum delay between page actions in seconds",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=454,
        help="Deterministic random seed",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="Optional path for machine-readable summary/results",
    )
    return parser.parse_args()


def validate_args(args: argparse.Namespace) -> None:
    parsed = urlparse(args.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SystemExit("--base-url must be an absolute http:// or https:// URL")
    if args.users < 1:
        raise SystemExit("--users must be >= 1")
    if args.concurrency < 1:
        raise SystemExit("--concurrency must be >= 1")
    if args.concurrency > 200:
        raise SystemExit("--concurrency is capped at 200 for this profiling tool")
    if args.listings_per_user < 0 or args.images_per_listing < 0:
        raise SystemExit("listing/image counts must be >= 0")
    if args.timeout <= 0:
        raise SystemExit("--timeout must be > 0")
    if args.think_min < 0 or args.think_max < 0:
        raise SystemExit("think times must be >= 0")
    if args.think_min > args.think_max:
        raise SystemExit("--think-min cannot exceed --think-max")


def canonical_origin(base_url: str) -> tuple[str, str, int | None]:
    parsed = urlparse(base_url)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port


def same_origin(url: str, base_url: str) -> bool:
    target = urlparse(urljoin(base_url, url))
    return (target.scheme.lower(), (target.hostname or "").lower(), target.port) == canonical_origin(
        base_url
    )


def absolute_url(base_url: str, current_url: str, candidate: str) -> str | None:
    if not candidate or candidate.startswith(("data:", "mailto:", "tel:", "javascript:")):
        return None
    value = urljoin(current_url, candidate)
    if not same_origin(value, base_url):
        return None
    parsed = urlparse(value)
    # Fragments do not cause a different HTTP request.
    return parsed._replace(fragment="").geturl()


def is_listing_url(url: str) -> bool:
    path = urlparse(url).path.rstrip("/")
    parts = path.split("/")
    # Canonical shape is /autogrid360/listings/<id>/<slug>; allow a slightly
    # broader match so pre-release route refinements do not break the profiler.
    return "/autogrid360/listings/" in path and len(parts) >= 4


def is_seller_url(url: str) -> bool:
    return "/autogrid360/sellers/" in urlparse(url).path


def parse_document(html: str, current_url: str, base_url: str) -> tuple[list[str], list[str]]:
    parser = LinkParser()
    try:
        parser.feed(html)
    except Exception:
        return [], []

    links: list[str] = []
    images: list[str] = []

    for candidate in parser.links:
        value = absolute_url(base_url, current_url, candidate)
        if value and value not in links:
            links.append(value)

    for candidate in parser.images:
        value = absolute_url(base_url, current_url, candidate)
        if value and value not in images:
            images.append(value)

    return links, images


def fetch(opener, url: str, category: str, timeout: float) -> RequestResult:
    request = Request(
        url,
        method="GET",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,image/*,*/*;q=0.8",
            "Connection": "close",
        },
    )
    started = time.perf_counter()
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if len(body) > MAX_RESPONSE_BYTES:
                return RequestResult(
                    url=url,
                    category=category,
                    status=getattr(response, "status", None),
                    elapsed_ms=elapsed_ms,
                    body_bytes=len(body),
                    content_type=response.headers.get("Content-Type", ""),
                    error=f"response exceeded {MAX_RESPONSE_BYTES} byte safety limit",
                )

            content_type = response.headers.get("Content-Type", "")
            html = None
            if "text/html" in content_type.lower():
                charset = response.headers.get_content_charset() or "utf-8"
                html = body.decode(charset, errors="replace")

            return RequestResult(
                url=url,
                category=category,
                status=getattr(response, "status", None),
                elapsed_ms=elapsed_ms,
                body_bytes=len(body),
                content_type=content_type,
                html=html,
            )
    except HTTPError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        try:
            body = exc.read(MAX_RESPONSE_BYTES)
        except Exception:
            body = b""
        return RequestResult(
            url=url,
            category=category,
            status=exc.code,
            elapsed_ms=elapsed_ms,
            body_bytes=len(body),
            content_type=exc.headers.get("Content-Type", "") if exc.headers else "",
            error=f"HTTP {exc.code}: {exc.reason}",
        )
    except (URLError, TimeoutError, OSError) as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return RequestResult(
            url=url,
            category=category,
            status=None,
            elapsed_ms=elapsed_ms,
            body_bytes=0,
            content_type="",
            error=str(exc),
        )


def maybe_think(rng: random.Random, args: argparse.Namespace) -> None:
    if args.think_max <= 0:
        return
    delay = rng.uniform(args.think_min, args.think_max)
    if delay > 0:
        time.sleep(delay)


def choose_distinct(rng: random.Random, values: list[str], count: int) -> list[str]:
    if count <= 0 or not values:
        return []
    if count >= len(values):
        values = values.copy()
        rng.shuffle(values)
        return values
    return rng.sample(values, count)


def simulate_visitor(visitor_id: int, args: argparse.Namespace) -> SessionResult:
    rng = random.Random(args.seed + visitor_id)
    opener = build_opener()
    result = SessionResult(visitor_id=visitor_id)

    inventory_url = urljoin(args.base_url.rstrip("/") + "/", args.inventory_path.lstrip("/"))
    inventory = fetch(opener, inventory_url, "inventory", args.timeout)
    result.requests.append(inventory)

    discovered_listing_urls: list[str] = []
    discovered_seller_urls: list[str] = []

    if inventory.html:
        links, _ = parse_document(inventory.html, inventory.url, args.base_url)
        discovered_listing_urls.extend(url for url in links if is_listing_url(url))
        discovered_seller_urls.extend(url for url in links if is_seller_url(url))

    maybe_think(rng, args)

    # Extra paths let the operator model exact current search/filter URLs without
    # hard-coding unstable pre-release query parameter names into this script.
    for extra in args.extra_path:
        extra_url = absolute_url(args.base_url, inventory_url, extra)
        if not extra_url:
            result.requests.append(
                RequestResult(
                    url=extra,
                    category="extra",
                    status=None,
                    elapsed_ms=0,
                    body_bytes=0,
                    content_type="",
                    error="extra path is not same-origin",
                )
            )
            continue
        extra_result = fetch(opener, extra_url, "extra", args.timeout)
        result.requests.append(extra_result)
        if extra_result.html:
            links, _ = parse_document(extra_result.html, extra_result.url, args.base_url)
            for url in links:
                if is_listing_url(url) and url not in discovered_listing_urls:
                    discovered_listing_urls.append(url)
                if is_seller_url(url) and url not in discovered_seller_urls:
                    discovered_seller_urls.append(url)
        maybe_think(rng, args)

    listing_urls = choose_distinct(rng, discovered_listing_urls, args.listings_per_user)

    for listing_url in listing_urls:
        listing = fetch(opener, listing_url, "listing", args.timeout)
        result.requests.append(listing)
        if listing.html:
            links, images = parse_document(listing.html, listing.url, args.base_url)
            for url in links:
                if is_seller_url(url) and url not in discovered_seller_urls:
                    discovered_seller_urls.append(url)

            for image_url in choose_distinct(rng, images, args.images_per_listing):
                image_result = fetch(opener, image_url, "image", args.timeout)
                result.requests.append(image_result)
        maybe_think(rng, args)

    if args.seller_pages_per_user and discovered_seller_urls:
        seller_url = rng.choice(discovered_seller_urls)
        result.requests.append(fetch(opener, seller_url, "seller", args.timeout))
        maybe_think(rng, args)

    return result


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * pct
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def human_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.2f} {unit}"
        amount /= 1024
    return f"{amount:.2f} TiB"


def build_summary(requests: list[RequestResult], elapsed_s: float, args: argparse.Namespace) -> dict:
    latencies = [r.elapsed_ms for r in requests]
    total_bytes = sum(r.body_bytes for r in requests)
    failures = [r for r in requests if r.error is not None or not r.status or r.status >= 400]
    statuses = Counter(str(r.status) if r.status is not None else "error" for r in requests)
    categories = Counter(r.category for r in requests)
    bytes_by_category = Counter()
    for request in requests:
        bytes_by_category[request.category] += request.body_bytes

    return {
        "base_url": args.base_url,
        "users": args.users,
        "concurrency": args.concurrency,
        "elapsed_seconds": elapsed_s,
        "requests": len(requests),
        "failures": len(failures),
        "requests_per_second": len(requests) / elapsed_s if elapsed_s else 0.0,
        "total_body_bytes": total_bytes,
        "bytes_per_visitor": total_bytes / args.users if args.users else 0.0,
        "latency_ms": {
            "mean": statistics.fmean(latencies) if latencies else 0.0,
            "p50": percentile(latencies, 0.50),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "max": max(latencies) if latencies else 0.0,
        },
        "status_counts": dict(sorted(statuses.items())),
        "category_counts": dict(sorted(categories.items())),
        "body_bytes_by_category": dict(sorted(bytes_by_category.items())),
    }


def print_summary(summary: dict, requests: list[RequestResult]) -> None:
    print("\nAutoGrid360 demo traffic profile")
    print("=" * 36)
    print(f"Visitors:              {summary['users']}")
    print(f"Visitor concurrency:   {summary['concurrency']}")
    print(f"Elapsed:               {summary['elapsed_seconds']:.2f} s")
    print(f"Requests:              {summary['requests']}")
    print(f"Failures:              {summary['failures']}")
    print(f"Requests/sec:          {summary['requests_per_second']:.2f}")
    print(f"Response body bytes:   {human_bytes(summary['total_body_bytes'])}")
    print(f"Bytes/visitor:         {human_bytes(int(summary['bytes_per_visitor']))}")

    latency = summary["latency_ms"]
    print(
        "Latency:               "
        f"mean={latency['mean']:.1f} ms  "
        f"p50={latency['p50']:.1f} ms  "
        f"p95={latency['p95']:.1f} ms  "
        f"p99={latency['p99']:.1f} ms  "
        f"max={latency['max']:.1f} ms"
    )
    print(f"Statuses:              {summary['status_counts']}")
    print(f"Request categories:    {summary['category_counts']}")

    category_bytes = {
        key: human_bytes(value) for key, value in summary["body_bytes_by_category"].items()
    }
    print(f"Bytes by category:     {category_bytes}")

    failures = [r for r in requests if r.error is not None or not r.status or r.status >= 400]
    if failures:
        print("\nFirst failures:")
        for request in failures[:10]:
            print(
                f"  [{request.category}] status={request.status!s:<5} "
                f"{request.url} -- {request.error or 'HTTP failure'}"
            )

    print(
        "\nNote: byte counts are HTTP response-body bytes observed by this script; "
        "they exclude response headers/TCP/TLS overhead. Running directly against "
        "the application also intentionally gives you an origin-side baseline before "
        "a CDN/cache is introduced."
    )


def main() -> int:
    args = parse_args()
    validate_args(args)

    collector = Collector()
    started = time.perf_counter()

    with ThreadPoolExecutor(max_workers=min(args.concurrency, args.users)) as executor:
        futures = {
            executor.submit(simulate_visitor, visitor_id, args): visitor_id
            for visitor_id in range(1, args.users + 1)
        }
        completed = 0
        for future in as_completed(futures):
            visitor_id = futures[future]
            try:
                session = future.result()
            except Exception as exc:  # keep a profiling run alive on one worker failure
                session = SessionResult(
                    visitor_id=visitor_id,
                    requests=[
                        RequestResult(
                            url=args.base_url,
                            category="session",
                            status=None,
                            elapsed_ms=0,
                            body_bytes=0,
                            content_type="",
                            error=f"visitor worker failed: {exc}",
                        )
                    ],
                )
            collector.add_many(session.requests)
            completed += 1
            if args.users >= 20 and (completed % max(1, args.users // 10) == 0 or completed == args.users):
                print(f"Completed visitors: {completed}/{args.users}", file=sys.stderr)

    elapsed_s = time.perf_counter() - started
    summary = build_summary(collector.requests, elapsed_s, args)
    print_summary(summary, collector.requests)

    if args.json_out:
        payload = {
            "summary": summary,
            "requests": [asdict(request) for request in collector.requests],
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nJSON written to: {args.json_out}")

    return 1 if summary["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())