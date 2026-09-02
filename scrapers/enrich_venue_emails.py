"""Enrich venue rows with email addresses from the venue's own website contact pages."""

from __future__ import annotations

import json
import re
import time
import csv
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from scrapers.contact_fields import EMAIL_RE, row_phone_email, save_json_csv
from scrapers.enrich_nyc_contacts import is_nyc_row
from scrapers.location_merge import norm_name

USER_AGENT = "comp-drink/0.1 (+venue email enrichment)"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
CACHE_PATH = DATA_DIR / "email_enrichment_cache.json"
REVIEW_CSV_PATH = DATA_DIR / "email_enrichment_review.csv"

DDG_HTML = "https://html.duckduckgo.com/html/"

REJECT_DOMAIN_PARTS = frozenset(
    {
        "yelp",
        "opentable",
        "tripadvisor",
        "resy",
        "tock",
        "facebook",
        "instagram",
        "foursquare",
        "michelin",
        "restaurantji",
        "chamberofcommerce",
        "toasttab",
        "zoominfo",
        "contactout",
        "thevendry",
        "mercato",
        "nooklyn",
    }
)

CONTACT_PATHS = (
    "/",
    "/contact",
    "/contact-us",
    "/about",
    "/hours-location",
    "/events",
    "/private-events",
    "/reservations",
)

EMAIL_PREFIX_PRIORITY = ("hello@", "info@", "contact@", "events@", "reservations@")

MAILTO_RE = re.compile(r"mailto:([^?#'\"\s>]+)", re.I)

JUNK_EMAIL_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".js", ".css")
JUNK_EMAIL_DOMAINS = frozenset(
    {
        "example.com",
        "domain.com",
        "email.com",
        "sentry.io",
        "wixpress.com",
        "users.noreply.github.com",
        "googletagmanager.com",
        "facebook.com",
        "instagram.com",
        "twitter.com",
        "youtube.com",
    }
)

# Hand-verified website + email (or JS-rendered flag). Keys are norm_name().
# email_source_url is required whenever email is set — never infer addresses.
EMAIL_VERIFIED_SEEDS: dict[str, dict[str, Any]] = {
    "ASKA": {
        "website": "https://askanyc.com",
        "email": "reservations@askanyc.com",
        "email_source_url": "https://askanyc.com/contact",
    },
    "FOUR HORSEMEN": {
        "website": "https://fourhorsemenbk.com",
        "email": "info@fourhorsemenbk.com",
        "email_source_url": "https://fourhorsemenbk.com/contact",
    },
    "RUFFIAN": {
        "website": "https://ruffiannyc.com",
        "email": "ruffiannyc@gmail.com",
        "email_source_url": "https://ruffiannyc.com/contact",
    },
    "HANA MAKGEOLLI": {
        "website": "https://hanamakgeolli.com",
        "email": "contact@hanamakgeolli.com",
        "email_source_url": "https://hanamakgeolli.com/contact",
    },
    "PEOPLES WINE": {
        "website": "https://peoples.wine",
        "email": "delivery@peoples.wine",
        "email_source_url": "https://peoples.wine/contact",
    },
    "PEOPLE'S WINE": {
        "website": "https://peoples.wine",
        "email": "delivery@peoples.wine",
        "email_source_url": "https://peoples.wine/contact",
    },
    "COVENHOVEN": {
        "website": "https://covenhovennyc.com",
        "email": "covenhoven@gmail.com",
        "email_source_url": "https://covenhovennyc.com/contact",
    },
    "CHERRY ON TOP": {
        "website": "https://cherryontopnyc.com",
        "email": "hello@cherryontopnyc.com",
        "email_source_url": "https://cherryontopnyc.com/contact",
    },
    "BAR MERIDIAN": {
        "website": "https://barmeridian.com",
        "email": "",
        "email_needs_headless": True,
    },
}


@dataclass
class PageFetch:
    path: str
    status: int
    html: str
    empty_shell: bool


@dataclass
class EmailEnrichmentResult:
    website: str = ""
    email: str = ""
    email_source_url: str = ""
    email_source: str = ""
    email_needs_headless: bool = False
    search_query: str = ""
    fetched_paths: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)


def host_of(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def is_rejected_domain(url: str) -> bool:
    host = host_of(url)
    if not host:
        return True
    for part in REJECT_DOMAIN_PARTS:
        if part in host:
            return True
    return False


def unwrap_ddg_href(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "uddg" in parsed.query:
        uddg = parse_qs(parsed.query).get("uddg", [""])[0]
        if uddg:
            return unquote(uddg)
    return href


def _soup(html: str) -> BeautifulSoup:
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


def normalize_site_root(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc or host_of(url)
    if not host and parsed.path:
        host = parsed.path.split("/")[0]
    if not host:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return f"https://{host}"


def is_junk_email(email: str) -> bool:
    lower = email.lower().strip()
    if not lower or len(lower) > 80:
        return True
    if any(lower.endswith(suffix) for suffix in JUNK_EMAIL_SUFFIXES):
        return True
    if "@" not in lower:
        return True
    local, _, domain = lower.rpartition("@")
    if not local or not domain or "." not in domain:
        return True
    if domain in JUNK_EMAIL_DOMAINS:
        return True
    if local in {"noreply", "no-reply", "donotreply", "test", "username", "yourname"}:
        return True
    if "example" in domain:
        return True
    return False


def email_local_domain(email: str) -> tuple[str, str]:
    lower = email.lower().strip()
    local, _, domain = lower.partition("@")
    return local, domain


def email_domain_matches_site(email: str, site_host: str) -> bool:
    _, domain = email_local_domain(email)
    if not site_host:
        return False
    if domain == site_host:
        return True
    if site_host.endswith("." + domain) or domain.endswith("." + site_host):
        return True
    site_base = site_host.split(".")[0]
    local, _ = email_local_domain(email)
    if site_base and site_base in local:
        return True
    return False


def prefix_rank(email: str) -> int:
    lower = email.lower()
    for i, prefix in enumerate(EMAIL_PREFIX_PRIORITY):
        if lower.startswith(prefix):
            return i
    return len(EMAIL_PREFIX_PRIORITY)


def pick_best_email(email_sources: dict[str, str], site_host: str) -> tuple[str, str]:
    """Pick one extracted email and the page URL it was found on. Never infer."""
    clean = {e: url for e, url in email_sources.items() if not is_junk_email(e)}
    if not clean:
        return "", ""

    def sort_key(email: str) -> tuple[int, int, str]:
        on_site = 0 if email_domain_matches_site(email, site_host) else 1
        return (prefix_rank(email), on_site, email.lower())

    best = sorted(clean.keys(), key=sort_key)[0]
    return best, clean[best]


def is_empty_shell_html(html: str) -> bool:
    stripped = (html or "").strip()
    if len(stripped) < 120:
        return True
    soup = _soup(stripped)
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    return len(text) < 80


def extract_emails_from_html(html: str) -> set[str]:
    found: set[str] = set()
    if not html:
        return found

    for match in MAILTO_RE.finditer(html):
        raw = unescape(match.group(1)).strip()
        if raw:
            found.add(raw.split(",")[0].strip())

    soup = _soup(html)
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if href.lower().startswith("mailto:"):
            raw = unescape(href[7:]).strip()
            if raw:
                found.add(raw.split(",")[0].strip())

    for match in EMAIL_RE.finditer(html):
        found.add(match.group(0))

    return {e for e in found if not is_junk_email(e)}


def extract_emails_with_sources(html: str, page_url: str) -> dict[str, str]:
    """Map extracted email -> exact page URL. No construction or inference."""
    return {email: page_url for email in extract_emails_from_html(html)}


class WebSearchClient:
    def __init__(self, pause: float = 1.2):
        self.pause = pause
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
            }
        )

    def search_urls(self, query: str) -> list[str]:
        time.sleep(self.pause)
        try:
            res = self.session.post(DDG_HTML, data={"q": query, "b": ""}, timeout=40)
            res.raise_for_status()
        except requests.RequestException:
            return []

        soup = _soup(res.text)
        urls: list[str] = []
        for anchor in soup.select("a.result__a"):
            href = unwrap_ddg_href(anchor.get("href") or "")
            if href.startswith("http"):
                urls.append(href)
        return urls


class SiteFetcher:
    def __init__(self, pause: float = 0.35):
        self.pause = pause
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
            }
        )

    def fetch(self, url: str) -> tuple[int, str]:
        time.sleep(self.pause)
        try:
            res = self.session.get(url, timeout=35, allow_redirects=True)
            return res.status_code, res.text or ""
        except requests.RequestException:
            return 0, ""


def first_venue_domain(search_urls: list[str]) -> str | None:
    for url in search_urls:
        if is_rejected_domain(url):
            continue
        root = normalize_site_root(url)
        if root:
            return root
    return None


def discover_site(
    row: dict[str, Any],
    search: WebSearchClient,
) -> tuple[str, str, str]:
    """Return (site_root, source, search_query)."""
    existing = (row.get("website") or "").strip()
    if existing and not is_rejected_domain(existing):
        return normalize_site_root(existing), "existing_website", ""

    name = (row.get("name") or "").strip()
    address = (row.get("address") or "").strip()
    query = f"{name} {address}".strip()
    if not query:
        return "", "no_query", query

    urls = search.search_urls(query)
    root = first_venue_domain(urls)
    if root:
        return root, "search", query
    return "", "search_miss", query


def fetch_contact_pages(site_root: str, fetcher: SiteFetcher) -> list[PageFetch]:
    pages: list[PageFetch] = []
    for path in CONTACT_PATHS:
        url = urljoin(site_root + "/", path.lstrip("/"))
        status, html = fetcher.fetch(url)
        pages.append(
            PageFetch(
                path=path,
                status=status,
                html=html,
                empty_shell=is_empty_shell_html(html),
            )
        )
    return pages


def enrich_from_site(site_root: str, pages: list[PageFetch]) -> EmailEnrichmentResult:
    site_host = host_of(site_root)
    email_sources: dict[str, str] = {}
    fetched_paths: list[str] = []
    all_empty = bool(pages)

    for page in pages:
        page_url = urljoin(site_root + "/", page.path.lstrip("/"))
        if page.status and page.html:
            fetched_paths.append(page.path)
            for email, source_url in extract_emails_with_sources(page.html, page_url).items():
                if email not in email_sources:
                    email_sources[email] = source_url
        if not page.empty_shell:
            all_empty = False

    email, email_source_url = pick_best_email(email_sources, site_host)
    result = EmailEnrichmentResult(
        website=site_root,
        email=email,
        email_source_url=email_source_url,
        email_source="venue_site" if email else "",
        email_needs_headless=all_empty and bool(pages),
        fetched_paths=fetched_paths,
        candidates=sorted(email_sources.keys()),
    )
    return result


def apply_seed(row: dict[str, Any]) -> EmailEnrichmentResult | None:
    key = norm_name(row.get("name") or "")
    seed = EMAIL_VERIFIED_SEEDS.get(key)
    if not seed:
        return None

    result = EmailEnrichmentResult(
        website=seed.get("website") or "",
        email=seed.get("email") or "",
        email_source_url=seed.get("email_source_url") or "",
        email_source="verified_seed",
        email_needs_headless=bool(seed.get("email_needs_headless")),
    )
    return result


def email_without_source_is_invalid(email: str, source_url: str) -> bool:
    """Non-empty email must always have a source page URL."""
    return bool((email or "").strip()) and not (source_url or "").strip()


def apply_result_to_row(
    row: dict[str, Any],
    result: EmailEnrichmentResult,
    force: bool = False,
) -> bool:
    if email_without_source_is_invalid(result.email, result.email_source_url):
        return False

    changed = False
    if result.website and (force or not (row.get("website") or "").strip()):
        row["website"] = result.website
        changed = True

    is_verified_seed = result.email_source == "verified_seed"

    if result.email:
        if is_verified_seed:
            if force or not (row.get("email") or "").strip():
                row["email"] = result.email
                row["email_source_url"] = result.email_source_url
                changed = True
        else:
            row["staging_email"] = result.email
            row["staging_email_source_url"] = result.email_source_url
            changed = True

    if result.email_source:
        row["email_source"] = result.email_source
        changed = True
    if result.email_needs_headless:
        row["email_needs_headless"] = True
        changed = True
    if result.search_query:
        row["email_search_query"] = result.search_query
    if result.fetched_paths:
        row["email_fetched_paths"] = ",".join(result.fetched_paths)
    return changed


def _cache_key(row: dict[str, Any]) -> str:
    return "|".join(
        [
            row.get("competitor") or "",
            row.get("name") or "",
            row.get("address") or "",
        ]
    ).lower()


def _needs_email(row: dict[str, Any]) -> bool:
    _, email = row_phone_email(row)
    return not email


def enrich_row(
    row: dict[str, Any],
    search: WebSearchClient,
    fetcher: SiteFetcher,
    cache: dict[str, Any],
    force: bool = False,
) -> EmailEnrichmentResult | None:
    seed = apply_seed(row)
    if seed:
        return seed

    if not force and not _needs_email(row):
        return None

    key = _cache_key(row)
    if key in cache and not force:
        cached = cache[key]
        if cached is None:
            return None
        return EmailEnrichmentResult(**cached)

    site_root, _, query = discover_site(row, search)
    if not site_root:
        cache[key] = None
        return EmailEnrichmentResult(search_query=query, email_source="search_miss")

    pages = fetch_contact_pages(site_root, fetcher)
    result = enrich_from_site(site_root, pages)
    result.search_query = query
    if result.email:
        result.email_source = "venue_site"

    cache[key] = {
        "website": result.website,
        "email": result.email,
        "email_source_url": result.email_source_url,
        "email_source": result.email_source,
        "email_needs_headless": result.email_needs_headless,
        "search_query": result.search_query,
        "fetched_paths": result.fetched_paths,
        "candidates": result.candidates,
    }
    return result


def load_cache() -> dict[str, Any]:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def save_cache(cache: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def review_row_from_location(row: dict[str, Any]) -> dict[str, str] | None:
    """Build a review CSV row. Prefer staging; fall back to promoted live email."""
    name = (row.get("name") or "").strip()
    staging_email = (row.get("staging_email") or "").strip()
    staging_url = (row.get("staging_email_source_url") or "").strip()
    if staging_email and staging_url:
        return {"name": name, "email": staging_email, "source_url": staging_url}
    if staging_email:
        return None

    live_email = (row.get("email") or "").strip()
    live_url = (row.get("email_source_url") or "").strip()
    if live_email and live_url:
        return {"name": name, "email": live_email, "source_url": live_url}
    return None


def export_review_csv(
    path: Path = REVIEW_CSV_PATH,
    nyc_only: bool = True,
) -> int:
    """Export name, email, source_url for spot-checking before promotion."""
    rows_out: list[dict[str, str]] = []
    for json_path in sorted(DATA_DIR.glob("*_locations.json")):
        rows: list[dict[str, Any]] = json.loads(json_path.read_text(encoding="utf-8"))
        for row in rows:
            if nyc_only and not is_nyc_row(row):
                continue
            review = review_row_from_location(row)
            if review:
                rows_out.append(review)

    rows_out.sort(key=lambda r: r["name"].lower())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "email", "source_url"])
        writer.writeheader()
        writer.writerows(rows_out)
    return len(rows_out)


def apply_seeds_only() -> dict[str, int]:
    stats = {"seeded": 0, "updated": 0, "headless": 0}
    for path in sorted(DATA_DIR.glob("*_locations.json")):
        rows: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
        file_changed = False
        for row in rows:
            seed = apply_seed(row)
            if not seed:
                continue
            stats["seeded"] += 1
            if seed.email_needs_headless:
                stats["headless"] += 1
            if apply_result_to_row(row, seed, force=True):
                stats["updated"] += 1
                file_changed = True
        if file_changed:
            save_json_csv(path, rows)
            print(f"Updated {path.name}")
    export_count = export_review_csv(nyc_only=True)
    print(f"Review CSV -> {REVIEW_CSV_PATH} ({export_count} rows)")
    return stats


def enrich_all(
    limit: int | None = None,
    force: bool = False,
    nyc_only: bool = True,
) -> dict[str, int]:
    cache = load_cache()
    search = WebSearchClient()
    fetcher = SiteFetcher()
    stats = {
        "targets": 0,
        "seeded": 0,
        "found": 0,
        "headless": 0,
        "updated": 0,
        "skipped": 0,
    }

    for path in sorted(DATA_DIR.glob("*_locations.json")):
        rows: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
        file_changed = False

        for row in rows:
            if nyc_only and not is_nyc_row(row):
                continue
            if not force and not _needs_email(row) and not apply_seed(row):
                stats["skipped"] += 1
                continue
            if limit is not None and stats["targets"] >= limit:
                break

            stats["targets"] += 1
            seed = apply_seed(row)
            if seed:
                stats["seeded"] += 1
                if apply_result_to_row(row, seed, force=force):
                    stats["updated"] += 1
                    file_changed = True
                if seed.email:
                    stats["found"] += 1
                if seed.email_needs_headless:
                    stats["headless"] += 1
                continue

            result = enrich_row(row, search, fetcher, cache, force=force)
            if result and result.email:
                stats["found"] += 1
            if result and result.email_needs_headless:
                stats["headless"] += 1
            if result and apply_result_to_row(row, result, force=force):
                stats["updated"] += 1
                file_changed = True

            if stats["targets"] % 5 == 0:
                save_cache(cache)
                print(
                    f"  processed {stats['targets']} — found {stats['found']}, "
                    f"headless flags {stats['headless']}"
                )

        if file_changed:
            save_json_csv(path, rows)
            print(f"Updated {path.name}")

        save_cache(cache)
        if limit is not None and stats["targets"] >= limit:
            break

    export_count = export_review_csv(nyc_only=nyc_only)
    print(f"Review CSV -> {REVIEW_CSV_PATH} ({export_count} rows)")
    return stats


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Enrich venue emails from website contact pages (search + fetch)"
    )
    parser.add_argument("--limit", type=int, default=None, help="Max rows to process")
    parser.add_argument("--force", action="store_true", help="Overwrite existing emails")
    parser.add_argument("--all-regions", action="store_true", help="Not just NYC rows")
    parser.add_argument(
        "--seeds-only",
        action="store_true",
        help="Apply verified email seeds only (no search/fetch)",
    )
    parser.add_argument(
        "--export-review",
        action="store_true",
        help="Export review CSV only (no enrichment)",
    )
    args = parser.parse_args()

    if args.export_review:
        count = export_review_csv(nyc_only=not args.all_regions)
        print(f"Review CSV -> {REVIEW_CSV_PATH} ({count} rows)")
        return

    if args.seeds_only:
        stats = apply_seeds_only()
        print(
            f"Done — seeded {stats['seeded']}, headless flags {stats['headless']}, "
            f"rows updated {stats['updated']}"
        )
        return

    stats = enrich_all(
        limit=args.limit,
        force=args.force,
        nyc_only=not args.all_regions,
    )
    print(
        f"Done — targets {stats['targets']}, seeded {stats['seeded']}, "
        f"emails found {stats['found']}, headless flags {stats['headless']}, "
        f"rows updated {stats['updated']}, skipped {stats['skipped']}"
    )


if __name__ == "__main__":
    main()
