import asyncio
import sys

from app.extractor import extract
from app.claude import is_article_url

async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python fetch.py <url>", file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1]
    if not is_article_url(url):
        print(f"Error: only HTTPS claude.com/resources/articles/<slug> URLs are supported.", file=sys.stderr)
        sys.exit(1)

    article = await extract(url)
    print(article.model_dump_json(indent=2))


if __name__ == "__main__":
    asyncio.run(main())
