import html as html_module
import json
import time
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from .models import Article, Image

_cache: dict[str, tuple[float, Article]] = {}
CACHE_TTL = 3600


async def extract(url: str, *, use_cache: bool = True) -> Article:
    if use_cache and url in _cache:
        ts, article = _cache[url]
        if time.time() - ts < CACHE_TTL:
            return article

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
                )
            )
            page = await ctx.new_page()
            await page.goto(url, wait_until="networkidle", timeout=30_000)
            try:
                await page.wait_for_selector("main article, main section", timeout=8_000)
            except Exception:
                pass
            html = await page.content()

            body_text = await _extract_sanity_body(page)
            if not body_text:
                raise ValueError("Sanity Portable Text content was not found; article skipped")
        finally:
            await browser.close()

    soup = BeautifulSoup(html, "lxml")

    title = (
        _meta(soup, "og:title")
        or _meta(soup, "twitter:title")
        or _jsonld_field(soup, "headline")
        or (soup.h1.get_text(strip=True) if soup.h1 else None)
    )
    summary = (
        _meta(soup, "og:description")
        or _meta(soup, "description")
        or _jsonld_field(soup, "description")
    )
    author = _jsonld_author(soup) or _meta(soup, "author")
    published_at = (
        _meta(soup, "article:published_time")
        or _jsonld_field(soup, "datePublished")
        or _jsonld_field(soup, "dateCreated")
    )
    og_image = _meta(soup, "og:image") or _meta(soup, "twitter:image")
    cover_image = Image(url=og_image) if og_image else None
    body_html = body_text
    images = _body_images(BeautifulSoup(f"<article>{body_text}</article>", "lxml"), url)
    tags = _extract_tags(soup)

    article = Article(
        url=url,
        title=title,
        author=author,
        published_at=published_at,
        summary=summary,
        cover_image=cover_image,
        body_html=body_html,
        body_text=body_text,
        images=images,
        tags=tags,
    )
    _cache[url] = (time.time(), article)
    return article


async def _extract_sanity_body(page) -> Optional[str]:
    """Extract Sanity Portable Text body from Next.js App Router RSC payload."""
    blocks = await page.evaluate("""
    () => {
        const scripts = [...document.querySelectorAll('script:not([src])')];
        const decoded = scripts
            .filter(s => s.textContent.includes('__next_f.push'))
            .map(s => {
                const m = s.textContent.match(/push\\(\\[1,(".*")\\]\\)$/s);
                try { return m ? JSON.parse(m[1]) : ''; } catch(e) { return ''; }
            }).join('');

        const bodyIdx = decoded.indexOf('"body":[');
        if (bodyIdx < 0) return null;
        const bodyStart = bodyIdx + 7;
        let depth = 0, i = bodyStart, bodyEnd = -1;
        for (; i < decoded.length && i < bodyStart + 150000; i++) {
            if (decoded[i] === '[') depth++;
            else if (decoded[i] === ']') { depth--; if (depth === 0) { bodyEnd = i + 1; break; } }
        }
        if (bodyEnd < 0) return null;
        try { return JSON.parse(decoded.slice(bodyStart, bodyEnd)); }
        catch(e) { return null; }
    }
    """)
    if not blocks:
        return None
    return _portable_text_to_html(blocks)


def _portable_text_to_html(blocks: list) -> str:
    """Convert Sanity Portable Text blocks to clean HTML."""
    parts = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        btype = block.get("_type")

        if btype == "image":
            url = block.get("url", "")
            desc = block.get("description", "")
            if not url:
                continue
            alt = html_module.escape(desc)
            cap = (
                f'<figcaption style="font-size:0.85em;color:#666;margin-top:0.4em">{alt}</figcaption>'
                if alt else ""
            )
            parts.append(
                f'<figure style="margin:1.5em 0">'
                f'<img src="{url}" alt="{alt}" style="max-width:100%;display:block">'
                f"{cap}</figure>"
            )

        elif btype == "block":
            style = block.get("style", "normal")
            children = block.get("children") or []
            mark_defs = {
                m["_key"]: m
                for m in (block.get("markDefs") or [])
                if isinstance(m, dict) and "_key" in m
            }

            inner = ""
            for span in children:
                if not isinstance(span, dict):
                    continue
                text = html_module.escape(span.get("text", ""))
                marks = span.get("marks") or []
                # Links (via markDefs)
                link = next(
                    (mark_defs[m] for m in marks if m in mark_defs and mark_defs[m].get("_type") == "link"),
                    None,
                )
                if link:
                    href = html_module.escape(link.get("href", ""))
                    text = f'<a href="{href}">{text}</a>'
                # Inline styles
                if "strong" in marks:
                    text = f"<strong>{text}</strong>"
                if "em" in marks:
                    text = f"<em>{text}</em>"
                if "code" in marks:
                    text = f"<code>{text}</code>"
                inner += text

            if not inner.strip():
                continue

            if style in ("h1", "h2", "h3", "h4", "h5", "h6"):
                parts.append(f"<{style}>{inner}</{style}>")
            elif style == "blockquote":
                parts.append(f"<blockquote>{inner}</blockquote>")
            elif block.get("listItem"):
                parts.append(f"<li>{inner}</li>")
            else:
                parts.append(f"<p>{inner}</p>")

    return "".join(parts)


def _meta(soup: BeautifulSoup, name: str) -> Optional[str]:
    tag = soup.find("meta", attrs={"property": name}) or soup.find(
        "meta", attrs={"name": name}
    )
    return tag.get("content", "").strip() or None if tag else None


def _jsonld_field(soup: BeautifulSoup, field: str) -> Optional[str]:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
            for item in data.get("@graph", [data]):
                if isinstance(item, dict) and field in item:
                    val = item[field]
                    if isinstance(val, str):
                        return val.strip() or None
                    if isinstance(val, dict):
                        return (val.get("url") or val.get("name") or "").strip() or None
        except (json.JSONDecodeError, AttributeError):
            continue
    return None


def _jsonld_author(soup: BeautifulSoup) -> Optional[str]:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
            for item in data.get("@graph", [data]):
                if not isinstance(item, dict):
                    continue
                author = item.get("author")
                if isinstance(author, dict):
                    return author.get("name", "").strip() or None
                if isinstance(author, list):
                    names = [a.get("name", "") for a in author if isinstance(a, dict)]
                    return ", ".join(filter(None, names)) or None
        except (json.JSONDecodeError, AttributeError):
            continue
    return None


def _body_images(soup: BeautifulSoup, base_url: str) -> list[Image]:
    images: list[Image] = []
    seen: set[str] = set()
    container = soup.select_one("main article") or soup.select_one("article") or soup.select_one("main")
    if not container:
        return images
    for img in container.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src or src in seen:
            continue
        seen.add(src)
        full = urljoin(base_url, src) if not src.startswith("http") else src
        try:
            w = int(img.get("width", 0)) or None
            h = int(img.get("height", 0)) or None
        except (ValueError, TypeError):
            w = h = None
        if w and w < 50:
            continue
        images.append(Image(url=full, alt=img.get("alt"), width=w, height=h))
    return images


def _extract_tags(soup: BeautifulSoup) -> list[str]:
    tags: list[str] = []
    for el in soup.select(
        "[class*='tag' i], [class*='category' i], [class*='topic' i], "
        "[rel='tag'], [data-category]"
    ):
        text = el.get_text(strip=True)
        if text and len(text) < 60 and text not in tags:
            tags.append(text)
    return tags
