"""
Daily discovery + fetch for Claude resources articles.
Clears saved article data and fetches every discovered article on each run.
Keeps a rolling 7-day publication window and uses structured content only.
"""

import argparse
import asyncio
import json
import re
import shutil
import sys
import urllib.request
from datetime import date, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urlencode, urlparse
import html as html_module
from xml.etree.ElementTree import Element, SubElement, tostring
from xml.dom.minidom import parseString

from app.claude import CATEGORY_API, LISTING_URL, is_article_url, listing_resources


LISTING_PAGES = [LISTING_URL]

DATA_DIR = Path("data")
WINDOW_DAYS = 7


def _read_listing(listing_url: str) -> tuple[list, int]:
    req = urllib.request.Request(listing_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return listing_resources(response.read().decode("utf-8"))


def _read_resource_page(page_number: int) -> tuple[list, int]:
    query = urlencode({"page": page_number, "language": "en", "type": "articles"})
    req = urllib.request.Request(
        CATEGORY_API + "?" + query,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Referer": LISTING_URL},
    )
    with urllib.request.urlopen(req, timeout=30) as response:
        data = json.load(response)
    if not isinstance(data.get("items"), list) or not isinstance(data.get("total"), int):
        raise ValueError("Claude pagination returned an invalid resource page")
    return data["items"], data["total"]


async def discover_listing(listing_url: str) -> list[str]:
    items, total = _read_listing(listing_url)
    cutoff = date.today() - timedelta(days=WINDOW_DAYS)
    urls: list[str] = []
    seen: set[str] = set()
    seen_pages: set[tuple] = set()
    page_number = 1
    while items:
        signature = tuple((item.get("slug"), item.get("externalUrl"), item.get("date")) for item in items)
        if signature in seen_pages:
            raise ValueError("Claude article pagination did not advance")
        seen_pages.add(signature)
        reached_cutoff = False
        for item in items:
            published = item.get("date")
            if not published:
                continue
            published_date = date.fromisoformat(published[:10])
            if published_date < cutoff:
                reached_cutoff = True
                continue
            # External resources are separate publications, not Claude article pages.
            if item.get("externalUrl"):
                continue
            slug = item.get("slug")
            if not isinstance(slug, str):
                continue
            url = LISTING_URL + "/" + slug
            if is_article_url(url) and url not in seen:
                seen.add(url)
                urls.append(url)
        if reached_cutoff or page_number >= total:
            break
        page_number += 1
        items, total = _read_resource_page(page_number)
    return urls


def url_to_slug(url: str) -> str:
    path = urlparse(url).path.strip("/")
    return re.sub(r"[^a-zA-Z0-9_-]", "-", path)


async def main(test_version: int | None = None) -> None:
    from app.extractor import extract

    if DATA_DIR.exists():
        shutil.rmtree(DATA_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    new_urls: list[str] = []
    for listing in LISTING_PAGES:
        print(f"Discovering: {listing}")
        found = await discover_listing(listing)
        print(f"  Found {len(found)} links")
        for url in found:
            if url not in new_urls:
                new_urls.append(url)

    if not new_urls:
        print("No articles found.")
    else:
        print(f"\nFetching {len(new_urls)} article(s):")
        today = date.today().isoformat()
        out_dir = DATA_DIR / today
        out_dir.mkdir(parents=True, exist_ok=True)

        for url in new_urls:
            print(f"  → {url}")
            try:
                article = await extract(url, use_cache=False)
                # Category/filter pages have no date — skip them
                if not article.published_at:
                    print(f"    Skipped (no published_at — likely a category page)")
                    continue
                # Skip articles published outside the window (when date is known)
                if article.published_at:
                    try:
                        pub_date = date.fromisoformat(article.published_at[:10])
                        if pub_date < date.today() - timedelta(days=WINDOW_DAYS):
                            print(f"    Skipped (older than {WINDOW_DAYS} days): {pub_date}")
                            continue
                    except ValueError:
                        pass  # unparseable date — save it anyway
                slug = url_to_slug(url)
                dest = out_dir / f"{slug}.json"
                dest.write_text(article.model_dump_json(indent=2), encoding="utf-8")
                print(f"    Saved: {dest}")
            except Exception as exc:
                print(f"    ERROR: {exc}", file=sys.stderr)

    _cleanup_old_data()
    _generate_feed(test_version)


def _body_to_html(text: str, cover_url: str = "", body_images: list = None) -> str:
    if not text.strip().startswith("<"):
        raise ValueError("Structured article HTML is required")
    inline_bases = {
        u.split("?")[0] for u in re.findall(r'<img[^>]+src=["\']([^"\']+)', text)
    }
    cover_html = ""
    if cover_url and cover_url.split("?")[0] not in inline_bases:
        cover_html = (
            f'<img src="{html_module.escape(cover_url, quote=True)}" '
            'style="max-width:100%;display:block;margin-bottom:1em">'
        )
    return cover_html + text


def _generate_feed(test_version: int | None = None) -> None:
    if test_version is not None and test_version < 1:
        raise ValueError("Test feed version must be a positive integer")
    articles = []
    for f in sorted(DATA_DIR.glob("**/*.json"), reverse=True):
        try:
            article = json.loads(f.read_text(encoding="utf-8"))
            body = article.get("body_text") or ""
            if not isinstance(body, str) or not body.strip().startswith("<"):
                print(f"Skipped {f}: structured article HTML is missing")
                continue
            articles.append(article)
        except Exception:
            continue

    rss = Element("rss", version="2.0")
    rss.set("xmlns:atom", "http://www.w3.org/2005/Atom")
    rss.set("xmlns:content", "http://purl.org/rss/1.0/modules/content/")
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = "Claude Articles"
    SubElement(channel, "link").text = LISTING_URL
    SubElement(channel, "description").text = "Latest Claude articles from Anthropic"
    SubElement(channel, "language").text = "en"
    atom_link = SubElement(channel, "atom:link")
    atom_link.set("href", "https://jayreck996.github.io/py-anthropic/feed.xml")
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    for a in articles:
        item = SubElement(channel, "item")
        SubElement(item, "title").text = a.get("title") or "Untitled"
        SubElement(item, "link").text = a.get("url", "")
        SubElement(item, "guid", isPermaLink="false").text = a.get("url", "")
        body = a["body_text"]
        cover = (a.get("cover_image") or {}).get("url", "")
        body_images = a.get("images") or []
        SubElement(item, "description").text = a.get("summary") or ""
        if body:
            html_body = _body_to_html(body, cover, body_images)
            encoded = SubElement(item, "content:encoded")
            encoded.text = f"<![CDATA[{html_body}]]>"
        if a.get("published_at"):
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(a["published_at"].replace("Z", "+00:00"))
                SubElement(item, "pubDate").text = format_datetime(dt)
            except Exception:
                pass
        if a.get("cover_image") and a["cover_image"].get("url"):
            enc = SubElement(item, "enclosure")
            enc.set("url", a["cover_image"]["url"])
            enc.set("type", "image/jpeg")
            enc.set("length", "0")

    _write_feed(rss, Path("feed.xml"))
    print(f"Feed updated: {len(articles)} item(s) -> feed.xml")
    if test_version is not None:
        test_name = f"feed_v{test_version}.xml"
        atom_link.set("href", atom_link.get("href").rsplit("/", 1)[0] + "/" + test_name)
        for guid in channel.findall("item/guid"):
            guid.text += f"#v{test_version}"
        _write_feed(rss, Path(test_name))
        print(f"Test feed updated: {test_name}")


def _write_feed(rss: Element, destination: Path) -> None:
    xml_str = parseString(tostring(rss, encoding="unicode")).toprettyxml(indent="  ")
    # minidom escapes CDATA delimiters and content — fix both
    xml_str = xml_str.replace("&lt;![CDATA[", "<![CDATA[").replace("]]&gt;", "]]>")
    xml_str = re.sub(
        r"<!\[CDATA\[(.*?)]]>",
        lambda m: f"<![CDATA[{html_module.unescape(m.group(1))}]]>",
        xml_str,
        flags=re.DOTALL,
    )
    destination.write_text(xml_str, encoding="utf-8")


def _cleanup_old_data() -> None:
    cutoff = date.today() - timedelta(days=WINDOW_DAYS)
    for day_dir in sorted(DATA_DIR.iterdir()):
        if not day_dir.is_dir():
            continue
        try:
            if date.fromisoformat(day_dir.name) < cutoff:
                shutil.rmtree(day_dir)
                print(f"Removed expired data: {day_dir}")
        except ValueError:
            pass  # skip non-date directories


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update the permanent feed.xml RSS feed.")
    parser.add_argument("--feed-only", action="store_true", help="Rebuild from saved articles without fetching.")
    parser.add_argument("--test-version", type=int, metavar="N", help="Also write feed_vN.xml for testing.")
    args = parser.parse_args()
    if args.test_version is not None and args.test_version < 1:
        parser.error("--test-version must be a positive integer")
    if args.feed_only:
        _generate_feed(args.test_version)
    else:
        asyncio.run(main(args.test_version))
