"""Lean website crawl + deterministic markdown sectioning.

Stack (replaces crawl4ai):

- ``httpx.AsyncClient`` for static HTTP fetching (handles 95% of B2B SaaS
  marketing sites, which are SSR/SSG).
- ``trafilatura`` for HTML -> readable markdown. Industry-standard, no
  LLM dependencies, ~200 KB.
- ``selectolax`` for cheap link discovery (CPython-native, much faster
  than BeautifulSoup for our needs).
- ``playwright`` is loaded lazily as a fallback for JS-heavy pages whose
  static fetch returns too little content. A single shared browser is
  reused across renders. Set ``crawl_js_fallback_min_chars=0`` to disable.

Crawling strategy: best-first BFS bounded by ``crawl_max_pages`` and
``crawl_max_depth``. URLs are scored by token overlap against a fixed
list of B2B-relevant keywords (about / pricing / customers / etc).

Caching: raw HTML is persisted under ``crawl_cache_dir`` keyed by
sha256(url). Cache hits skip the network entirely; the same URL never
re-renders unless the cache file is removed.

Provenance is still owned by us:

- ``section_id = sha1(url, heading, char_start)`` is deterministic.
- LLM extractors only reference existing section_ids.
- Crawl4AI is no longer in the tree.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
import trafilatura
from selectolax.parser import HTMLParser

from ..config import settings

log = logging.getLogger(__name__)


# Tuned for B2B/SaaS commercial discovery. Used as a URL-token relevance
# scorer for the BFS frontier.
_COMMERCIAL_KEYWORDS: list[str] = [
    "about", "company", "product", "platform", "solution", "solutions",
    "feature", "features",
    "pricing", "plans", "enterprise",
    "customer", "customers", "case-stud", "case_stud", "stories",
    "use-case", "use-cases", "use_case",
    "industry", "industries",
    "career", "careers", "jobs", "hiring", "team",
    "news", "press", "blog",
]

_NON_HTML_SUFFIXES = (
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
    ".zip", ".tar", ".gz", ".mp4", ".mp3", ".wav", ".css", ".js",
    ".woff", ".woff2", ".ttf", ".otf",
)


# ---------- Public types (kept stable: callers don't change) ----------


@dataclass
class PageResult:
    """One crawled page in a shape the rest of the pipeline consumes."""

    url: str
    success: bool
    status_code: int | None
    markdown: str
    content_hash: str
    metadata: dict


@dataclass
class CrawlOutput:
    pages: list[PageResult]
    failed_urls: list[str]
    discovered_urls: list[str] = field(default_factory=list)


# ---------- Helpers ----------


def _normalize_url(url: str) -> str:
    if not url:
        return url
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    # Drop fragments; query params are kept since they sometimes change
    # routing on SaaS sites (e.g. ?tab=enterprise).
    url, _ = urldefrag(url)
    return url.rstrip("/")


def _domain_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host.removeprefix("www.")


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _cache_path(url: str) -> Path:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return settings.crawl_cache_dir / f"{key}.html"


def _read_cache(url: str) -> str | None:
    p = _cache_path(url)
    if not p.exists():
        return None
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return None


def _write_cache(url: str, html: str) -> None:
    try:
        _cache_path(url).write_text(html, encoding="utf-8")
    except OSError as e:
        log.debug("cache write failed for %s: %s", url, e)


def _looks_html(content_type: str | None, body: str) -> bool:
    if content_type and "html" not in content_type.lower():
        return False
    head = body[:2000].lower()
    return "<html" in head or "<body" in head or "<!doctype" in head


def _score_url(url: str) -> float:
    """Cheap token-overlap score against the commercial keyword list."""
    path = urlparse(url).path.lower()
    if not path or path == "/":
        return 1.0  # homepage always wins for the seed.
    score = 0.0
    for kw in _COMMERCIAL_KEYWORDS:
        if kw in path:
            score += 1.0
    # Penalize very deep URLs — they are usually long-tail blog posts.
    depth = path.count("/")
    score -= 0.15 * max(0, depth - 2)
    return score


def _extract_links(html: str, base_url: str, allowed_domain: str) -> list[str]:
    try:
        tree = HTMLParser(html)
    except Exception:  # noqa: BLE001
        return []
    out: list[str] = []
    seen: set[str] = set()
    for a in tree.css("a[href]"):
        href = a.attributes.get("href")
        if not href:
            continue
        href = href.strip()
        if (
            not href
            or href.startswith(("#", "mailto:", "tel:", "javascript:"))
            or href.endswith(_NON_HTML_SUFFIXES)
        ):
            continue
        full = urljoin(base_url, href)
        full, _ = urldefrag(full)
        full = full.rstrip("/")
        if not full.startswith(("http://", "https://")):
            continue
        if _domain_of(full) != allowed_domain:
            continue
        if full in seen:
            continue
        seen.add(full)
        out.append(full)
    return out


def _to_markdown(html: str) -> str:
    """HTML -> clean markdown. Returns empty string on failure."""
    try:
        md = trafilatura.extract(
            html,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            favor_recall=True,
            with_metadata=False,
        )
    except Exception:  # noqa: BLE001
        return ""
    return (md or "").strip()


# ---------- Static fetcher (httpx) ----------


async def _fetch_static(client: httpx.AsyncClient, url: str) -> tuple[int | None, str]:
    """Returns (status_code, html). Empty html on failure.

    403/401 often means bot blocking on static httpx; the caller may
    retry with Playwright.
    """
    cached = _read_cache(url)
    if cached is not None:
        return 200, cached
    try:
        r = await client.get(url, follow_redirects=True)
    except (httpx.HTTPError, asyncio.TimeoutError) as e:
        log.debug("fetch failed %s: %s", url, e)
        return None, ""
    if r.status_code in (401, 403):
        return r.status_code, ""
    if r.status_code >= 400:
        return r.status_code, ""
    if not _looks_html(r.headers.get("content-type"), r.text):
        return r.status_code, ""
    _write_cache(url, r.text)
    return r.status_code, r.text


# ---------- Optional Playwright fallback (lazy) ----------


@dataclass
class _Renderer:
    """Lazy single-browser Playwright wrapper. Reused across page renders."""

    pw: object | None = None
    browser: object | None = None
    enabled: bool = True

    async def render(self, url: str) -> str:
        if not self.enabled:
            return ""
        try:
            if self.browser is None:
                from playwright.async_api import async_playwright

                self.pw = await async_playwright().start()
                self.browser = await self.pw.chromium.launch(  # type: ignore[union-attr]
                    headless=settings.crawl_headless,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
            ctx = await self.browser.new_context(  # type: ignore[union-attr]
                user_agent=settings.crawl_user_agent,
            )
            page = await ctx.new_page()
            try:
                await page.goto(
                    url,
                    timeout=int(settings.crawl_page_timeout_s * 1000),
                    wait_until="domcontentloaded",
                )
                # Best-effort idle wait without blocking forever on noisy sites.
                try:
                    await page.wait_for_load_state(
                        "networkidle", timeout=4000
                    )
                except Exception:  # noqa: BLE001
                    pass
                html = await page.content()
                return html or ""
            finally:
                await page.close()
                await ctx.close()
        except Exception as e:  # noqa: BLE001
            log.info("playwright render failed for %s: %s", url, e)
            self.enabled = False  # don't keep retrying once Playwright is broken
            return ""

    async def close(self) -> None:
        try:
            if self.browser is not None:
                await self.browser.close()  # type: ignore[union-attr]
            if self.pw is not None:
                await self.pw.stop()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass


# ---------- BFS frontier ----------


@dataclass
class _FrontierItem:
    url: str
    depth: int
    score: float = field(default=0.0)


async def _crawl_one(
    client: httpx.AsyncClient,
    renderer: _Renderer,
    url: str,
) -> PageResult | None:
    status, html = await _fetch_static(client, url)

    # Bot-blocked or empty static body → try Playwright before giving up.
    needs_browser = (
        settings.crawl_js_fallback_min_chars > 0
        and renderer.enabled
        and (
            not html
            or status in (401, 403)
            or (html and len(_to_markdown(html)) < settings.crawl_js_fallback_min_chars)
        )
    )
    if needs_browser:
        rendered = await renderer.render(url)
        if rendered:
            html = rendered
            status = status or 200

    if not html:
        return None
    md = _to_markdown(html)
    if not md.strip():
        return None
    _write_cache(url, html)
    return PageResult(
        url=url,
        success=True,
        status_code=status,
        markdown=md,
        content_hash=_content_hash(md),
        metadata={"final_html_chars": len(html)},
    )


async def crawl_company(
    homepage_url: str,
    *,
    max_pages: int | None = None,
    explicit_urls: list[str] | None = None,
) -> CrawlOutput:
    """Crawl a company website.

    Behavior:
    - When ``explicit_urls`` is provided (Planner ``fetch_more`` step), we
      crawl only those URLs in parallel; no link discovery is performed.
    - Otherwise we BFS from the homepage, scoring frontier URLs by token
      overlap against ``_COMMERCIAL_KEYWORDS``, capped by ``max_pages``
      and ``crawl_max_depth``.
    """
    homepage_url = _normalize_url(homepage_url)
    domain = _domain_of(homepage_url)
    cap = max_pages or settings.crawl_max_pages

    timeout = httpx.Timeout(settings.crawl_page_timeout_s)
    headers = {"User-Agent": settings.crawl_user_agent}
    sem = asyncio.Semaphore(settings.crawl_concurrency)

    pages: list[PageResult] = []
    failed: list[str] = []
    renderer = _Renderer(enabled=settings.crawl_js_fallback_min_chars > 0)

    async with httpx.AsyncClient(
        headers=headers, timeout=timeout, follow_redirects=True
    ) as client:

        async def fetch_with_sem(url: str) -> PageResult | None:
            async with sem:
                try:
                    return await _crawl_one(client, renderer, url)
                except Exception as e:  # noqa: BLE001
                    log.info("crawl: %s raised: %s", url, e)
                    return None

        try:
            discovered: set[str] = set()

            if explicit_urls is not None:
                # Single-shot: crawl exactly the URLs provided.
                urls = [_normalize_url(u) for u in explicit_urls][:cap]
                discovered.update(urls)
                results = await asyncio.gather(
                    *(fetch_with_sem(u) for u in urls)
                )
                for u, r in zip(urls, results):
                    if r is None:
                        failed.append(u)
                    else:
                        pages.append(r)
                        html = _read_cache(u) or ""
                        for link in _extract_links(html, u, domain):
                            discovered.add(link)
            else:
                pages, failed, discovered = await _bfs(
                    fetch_with_sem=fetch_with_sem,
                    seed=homepage_url,
                    domain=domain,
                    max_pages=cap,
                    max_depth=settings.crawl_max_depth,
                )
        finally:
            await renderer.close()

    log.info(
        "crawl: %s -> %d pages (%d failed, %d discovered links)",
        homepage_url, len(pages), len(failed), len(discovered),
    )
    return CrawlOutput(
        pages=pages,
        failed_urls=failed,
        discovered_urls=sorted(discovered),
    )


async def _bfs(
    *,
    fetch_with_sem,
    seed: str,
    domain: str,
    max_pages: int,
    max_depth: int,
) -> tuple[list[PageResult], list[str], set[str]]:
    """Best-first BFS bounded by ``max_pages`` and ``max_depth``.

    Each layer is fetched in parallel (subject to the connection
    semaphore). After fetching, we extract links from every successful
    page, score them, and keep only the top survivors for the next layer
    so we don't blow past ``max_pages``.

    Returns ``(pages, failed_urls, discovered_urls)`` where
    ``discovered_urls`` is every same-domain link we saw in HTML (whether
    or not we successfully crawled it).
    """
    seen: set[str] = {seed}
    discovered: set[str] = {seed}
    pages: list[PageResult] = []
    failed: list[str] = []

    layer: list[_FrontierItem] = [_FrontierItem(url=seed, depth=0, score=1.0)]
    while layer and len(pages) < max_pages:
        # Cap parallelism per-layer to the remaining budget.
        remaining = max_pages - len(pages)
        batch = layer[:remaining]
        urls = [it.url for it in batch]
        results = await asyncio.gather(*(fetch_with_sem(u) for u in urls))

        next_links: list[_FrontierItem] = []
        for it, r in zip(batch, results):
            if r is None:
                failed.append(it.url)
                # Still try link discovery from cache (e.g. partial 403 page).
                html = _read_cache(it.url) or ""
                if html:
                    for link in _extract_links(html, it.url, domain):
                        if link not in seen:
                            seen.add(link)
                            discovered.add(link)
                continue
            pages.append(r)
            if it.depth >= max_depth:
                continue
            html = _read_cache(it.url) or ""
            if not html:
                continue
            for link in _extract_links(html, base_url=it.url, allowed_domain=domain):
                discovered.add(link)
                if link in seen:
                    continue
                seen.add(link)
                next_links.append(
                    _FrontierItem(
                        url=link, depth=it.depth + 1, score=_score_url(link)
                    )
                )

        next_links.sort(key=lambda x: x.score, reverse=True)
        layer = next_links[: max_pages * 2]

    return pages, failed, discovered


# ---------- Deterministic markdown sectioning (unchanged) ----------


_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")


@dataclass
class RawSection:
    heading: str | None
    text: str
    char_start: int
    char_end: int


def _split_markdown(markdown: str) -> list[RawSection]:
    """Split markdown into heading-rooted sections.

    The first section captures text before any heading (the lede). New
    sections start at every ``#`` / ``##`` / ``###`` / ``####`` line.
    """
    sections: list[RawSection] = []
    current_heading: str | None = None
    buf: list[str] = []
    char_start = 0
    char_cursor = 0

    def flush() -> None:
        nonlocal buf, char_start, char_cursor
        text = "\n\n".join(p.strip() for p in buf if p.strip()).strip()
        if text:
            sections.append(
                RawSection(
                    heading=current_heading,
                    text=text,
                    char_start=char_start,
                    char_end=char_start + len(text),
                )
            )
            char_cursor = char_start + len(text) + 2
        buf = []
        char_start = char_cursor

    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            joined = " ".join(p.strip() for p in paragraph if p.strip()).strip()
            if joined:
                buf.append(joined)
            paragraph.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        m = _HEADING_RE.match(line)
        if m:
            flush_paragraph()
            flush()
            current_heading = m.group(2).strip()
            continue
        if not line.strip():
            flush_paragraph()
            continue
        # Lists / quotes / code: treat each line as its own paragraph block.
        if line.lstrip().startswith(("- ", "* ", "> ", "```")):
            flush_paragraph()
            buf.append(line.strip())
            continue
        paragraph.append(line)
    flush_paragraph()
    flush()
    return sections


def _stable_id(url: str, heading: str | None, char_start: int) -> str:
    base = f"{url}|{heading or ''}|{char_start}"
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]
    return f"sec_{digest}"


def _split_oversized(s: RawSection) -> list[RawSection]:
    if len(s.text) <= settings.max_section_chars:
        return [s]
    paragraphs = [p for p in s.text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for p in paragraphs:
        if size + len(p) > settings.max_section_chars and buf:
            chunks.append("\n\n".join(buf))
            tail = "\n\n".join(buf)[-settings.section_overlap_chars :]
            buf = [tail, p] if tail else [p]
            size = sum(len(x) for x in buf)
        else:
            buf.append(p)
            size += len(p)
    if buf:
        chunks.append("\n\n".join(buf))

    offset = s.char_start
    out: list[RawSection] = []
    for c in chunks:
        out.append(
            RawSection(
                heading=s.heading,
                text=c,
                char_start=offset,
                char_end=offset + len(c),
            )
        )
        offset += len(c)
    return out


def section_page(url: str, markdown: str) -> list[dict]:
    """Return the section dicts for one page's markdown."""
    if not markdown:
        return []
    raw_sections = _split_markdown(markdown)
    final: list[dict] = []
    for s in raw_sections:
        for sub in _split_oversized(s):
            sid = _stable_id(url, sub.heading, sub.char_start)
            final.append(
                {
                    "section_id": sid,
                    "url": url,
                    "heading": sub.heading,
                    "text": sub.text,
                    "char_start": sub.char_start,
                    "char_end": sub.char_end,
                    "source": "website",
                }
            )
    return final


def new_page_id() -> str:
    return f"page_{uuid.uuid4().hex[:12]}"


# ---------- fetch_more URL selection (used by Planner repair loop) ----------


_FIELD_PATH_KEYWORDS: dict[str, list[str]] = {
    "target_industries": ["industry", "industries", "sector", "market", "vertical"],
    "size_bands": ["size", "enterprise", "smb", "mid-market", "employees", "scale"],
    "likely_buyers": ["buyer", "buyers", "customer", "customers", "client"],
    "common_triggers": ["trigger", "news", "hiring", "funding", "launch", "growth"],
    "negative_icp": ["negative", "not", "exclude", "disqualif"],
}


def _path_tokens(path: str) -> set[str]:
    return {t for t in re.split(r"[/\-_]+", path.lower()) if len(t) > 2}


def pick_fetch_more_urls(
    *,
    homepage: str,
    discovered: list[str],
    crawled: list[str],
    planner_suggestions: list[str],
    missing_fields: list[str],
    limit: int = 5,
) -> list[str]:
    """Choose real uncrawled URLs for the fetch_more repair pass.

    The Planner LLM often invents paths like ``/about/industries`` that do
    not exist. We only return URLs that were actually discovered during the
    initial BFS, preferring:

    1. Uncrawled URLs that fuzzy-match a planner suggestion (token overlap).
    2. Otherwise, uncrawled URLs scored by commercial keywords + missing-field
       hints.
    """
    crawled_set = {_normalize_url(u) for u in crawled}
    uncrawled = [_normalize_url(u) for u in discovered if _normalize_url(u) not in crawled_set]
    if not uncrawled:
        return []

    picked: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        u = _normalize_url(url)
        if u in seen or u in crawled_set:
            return
        if u not in uncrawled:
            return
        seen.add(u)
        picked.append(u)

    # Fuzzy-match planner suggestions to real discovered URLs.
    for raw in planner_suggestions:
        if len(picked) >= limit:
            break
        path = raw
        if raw.startswith(("http://", "https://")):
            path = urlparse(raw).path
        sug_tokens = _path_tokens(path)
        if not sug_tokens:
            continue
        ranked: list[tuple[int, str]] = []
        for u in uncrawled:
            if u in seen:
                continue
            overlap = len(sug_tokens & _path_tokens(urlparse(u).path))
            if overlap:
                ranked.append((overlap, u))
        ranked.sort(key=lambda x: (-x[0], -_score_url(x[1])))
        for _, u in ranked[:2]:
            _add(u)

    # Fill remaining slots with commercially relevant uncrawled links.
    if len(picked) < limit:
        hint_tokens: set[str] = set()
        for field in missing_fields:
            hint_tokens.update(_FIELD_PATH_KEYWORDS.get(field, []))

        def _rank(u: str) -> float:
            base = _score_url(u)
            path = urlparse(u).path.lower()
            for tok in hint_tokens:
                if tok in path:
                    base += 0.5
            return base

        for u in sorted(uncrawled, key=_rank, reverse=True):
            if len(picked) >= limit:
                break
            _add(u)

    return picked[:limit]
