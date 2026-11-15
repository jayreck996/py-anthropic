from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from .extractor import extract
from .models import Article
from .claude import is_article_url

app = FastAPI(
    title="py-anthropic",
    description="Fetch full article content (title, body, images, metadata) from claude.com",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

@app.get("/fetch", response_model=Article, summary="Fetch a full Claude article")
async def fetch_article(
    url: str = Query(..., description="Full URL of an claude.com article")
):
    """
    Returns title, author, published_at, summary, cover_image,
    body_html, body_text, images[], and tags[] for any claude.com article.
    Results are cached in-memory for 1 hour.
    """
    parsed = urlparse(url)
    if not is_article_url(url):
        raise HTTPException(
            status_code=400,
            detail=f"Only HTTPS claude.com/resources/articles/<slug> URLs are supported. Got: '{parsed.netloc}'",
        )
    try:
        return await extract(url)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}
