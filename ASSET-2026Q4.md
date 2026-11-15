ASSET LOG
INSTRUCTION FOR AI MODEL:

ALWAYS ADD NEW ASSET ENTRIES AT THE TOP, DIRECTLY BELOW THIS HEADER.

NEVER DELETE OR EDIT PREVIOUS ASSET ENTRIES.

REQUIRED FORMAT FOR EACH ASSET ENTRY:

## ASSET:{NAME OF ENVIRONMENT} {YYYY-MM-DD HH:MM} → {CONTENT}

## ASSET:py-anthropic 2026-11-15 21:32 → Permanent production feed, structured-only extraction, fresh daily fetches

- Production output is always `feed.xml`, with stable article-URL GUIDs. Daily workflows commit `data/` and `feed.xml` only.
- Versioned feeds are testing snapshots: `python run_daily.py --feed-only --test-version 15` also creates `feed_v15.xml` without changing production GUIDs or existing snapshots.
- Extraction uses Sanity Portable Text only. DOM and Trafilatura content fallbacks are removed; articles without structured content are skipped.
- Every daily run clears saved article data, bypasses the in-memory extraction cache, and fetches all discovered URLs. The rolling publication window remains 7 days.
- Validation: 9 tests passed on GitHub; a live structured-only fetch of the roadshow-launch article succeeded.
- Implementation commits: `06d6f3c` (permanent feed) and `cb5bddd` (strict extraction and fresh fetching).

## ASSET:py-anthropic 2026-11-15 18:46 → Sanity Portable Text inline images — v10 operational

Anthropic blog/research RSS scraper fully operational with inline images from Sanity CMS Portable Text.

| Field | Value |
|---|---|
| Feed URL | `https://raw.githubusercontent.com/jayreck996/py-anthropic/master/feed.xml` |
| Versioned feed | `feed_v10.xml` |
| GUID suffix | `#v10` |
| Schedule | `0 17 * * *` UTC — 6:00 AM NZDT daily |
| Article window | 7 days rolling |
| Discovery | Playwright scrape of `https://www.anthropic.com/news` + `https://www.anthropic.com/research` |

**Extraction pipeline:**

| Stage | Method |
|---|---|
| Render | Playwright headless Chromium |
| Text + images | Sanity Portable Text blocks decoded from Next.js RSC payload — `_type:"block"` for paragraphs, `_type:"image"` for inline images, positional ordering preserved |
| Image URL | Sanity CDN `https://cdn.sanity.io/images/...` from `asset._ref` decoded via `_id` lookup |
| Fallback | DOM walk (`main article` / `main section`), then trafilatura |

**CMS:** Sanity — Portable Text block array interleaves text blocks and image blocks natively, giving correct inline image position without additional ordering logic.

**Key difference vs py-openai:** Sanity stores images inline in the same content array as text. Contentful (OpenAI) stores images as separate `$L` lazy refs resolved from RSC chunk dict.
