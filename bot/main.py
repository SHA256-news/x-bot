"""Command-line entry point for the Bitcoin mining news bot.

The module polls the Event Registry API for recent Bitcoin mining
articles, stores the latest ``updatesAfterNewsUri``,
``updatesAfterBlogUri`` and ``updatesAfterPrUri`` checkpoints, and posts
summaries for newly discovered articles to Twitter.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, MutableMapping, Optional

import tweepy
from eventregistry import (
    ArticleInfoFlags,
    EventRegistry,
    GetRecentArticles,
    ReturnInfo,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_QUERY = "bitcoin mining"
MAX_TWEET_LENGTH = 280
POSTED_HISTORY_LIMIT = 250


class BotConfigurationError(RuntimeError):
    """Raised when a required configuration value is missing."""


def load_state(path: Path) -> Dict[str, Any]:
    """Load persisted state from ``path``.

    The state dictionary always provides ``updatesAfterNewsUri``,
    ``updatesAfterBlogUri``, ``updatesAfterPrUri`` and
    ``postedArticleUris`` keys.
    """

    if not path.exists():
        return {
            "updatesAfterNewsUri": None,
            "updatesAfterBlogUri": None,
            "updatesAfterPrUri": None,
            "postedArticleUris": [],
        }

    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - defensive
        raise RuntimeError(f"Failed to load bot state from {path}: {exc}") from exc

    data.setdefault("updatesAfterNewsUri", None)
    data.setdefault("updatesAfterBlogUri", None)
    data.setdefault("updatesAfterPrUri", None)
    data.setdefault("postedArticleUris", [])
    return data


def save_state(path: Path, state: MutableMapping[str, Any]) -> None:
    """Persist the state dictionary to ``path``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)


def create_event_registry(api_key: Optional[str]) -> EventRegistry:
    """Create an :class:`~eventregistry.EventRegistry` client."""

    if not api_key:
        raise BotConfigurationError(
            "EVENT_REGISTRY_API_KEY is required to connect to Event Registry."
        )
    LOGGER.debug("Initialising EventRegistry client")
    return EventRegistry(apiKey=api_key)


def create_twitter_client() -> tweepy.Client:
    """Create and configure the Tweepy client."""

    bearer_token = os.getenv("TWITTER_BEARER_TOKEN")
    api_key = os.getenv("TWITTER_API_KEY")
    api_secret = os.getenv("TWITTER_API_SECRET")
    access_token = os.getenv("TWITTER_ACCESS_TOKEN")
    access_secret = os.getenv("TWITTER_ACCESS_SECRET")

    missing = [
        name
        for name, value in [
            ("TWITTER_API_KEY", api_key),
            ("TWITTER_API_SECRET", api_secret),
            ("TWITTER_ACCESS_TOKEN", access_token),
            ("TWITTER_ACCESS_SECRET", access_secret),
        ]
        if not value
    ]
    if missing:
        joined = ", ".join(missing)
        raise BotConfigurationError(
            f"Missing Twitter credentials: {joined}. Set the variables before running."
        )

    LOGGER.debug("Initialising Tweepy client")
    return tweepy.Client(
        bearer_token=bearer_token,
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_secret,
        wait_on_rate_limit=True,
    )


def build_recent_articles_request(
    er: EventRegistry,
    *,
    query: str,
    article_lang: Optional[str],
    state: MutableMapping[str, Any],
) -> GetRecentArticles:
    """Prepare a :class:`~eventregistry.GetRecentArticles` request."""

    return_info = ReturnInfo(
        articleInfo=ArticleInfoFlags(
            bodyLen=400,
            title=True,
            body=True,
            url=True,
            authors=False,
            concepts=True,
            categories=False,
        )
    )

    kwargs: Dict[str, Any] = {}
    if state.get("updatesAfterNewsUri"):
        kwargs["recentActivityArticlesNewsUpdatesAfterUri"] = state["updatesAfterNewsUri"]
    if state.get("updatesAfterBlogUri"):
        kwargs["recentActivityArticlesBlogsUpdatesAfterUri"] = state["updatesAfterBlogUri"]
    if state.get("updatesAfterPrUri"):
        kwargs["recentActivityArticlesPrUpdatesAfterUri"] = state["updatesAfterPrUri"]

    # Restrict results to the configured language when provided.
    if article_lang:
        kwargs["articleLang"] = article_lang

    # Event Registry expects filters prefixed with "recentActivityArticles".
    # This narrows the feed to articles that mention the Bitcoin mining keyword.
    kwargs["recentActivityArticlesKeyword"] = query

    return GetRecentArticles(er, returnInfo=return_info, **kwargs)


def fetch_recent_activity(
    er: EventRegistry,
    *,
    query: str,
    article_lang: Optional[str],
    state: MutableMapping[str, Any],
) -> List[Dict[str, Any]]:
    """Fetch the recent activity list and update ``state`` checkpoints."""

    request = build_recent_articles_request(
        er, query=query, article_lang=article_lang, state=state
    )

    LOGGER.debug("Requesting recent activity from Event Registry")
    response = er.execQuery(request)
    recent = response.get("recentActivityArticles", {}) if isinstance(response, dict) else {}
    newest_uri = recent.get("newestUri", {})

    if newest_uri:
        news_uri = newest_uri.get("news")
        blog_uri = newest_uri.get("blogs") or newest_uri.get("blog")
        pr_uri = newest_uri.get("pr")

        if news_uri:
            state["updatesAfterNewsUri"] = news_uri
        if blog_uri:
            state["updatesAfterBlogUri"] = blog_uri
        if pr_uri:
            state["updatesAfterPrUri"] = pr_uri

    activity = recent.get("activity", [])
    if not isinstance(activity, list):
        LOGGER.warning("Unexpected activity payload returned by Event Registry")
        return []

    LOGGER.info("Retrieved %d articles from recent activity", len(activity))
    return [
        article
        for article in activity
        if is_bitcoin_mining_article(article, query=query)
    ]


def is_bitcoin_mining_article(article: Dict[str, Any], *, query: str) -> bool:
    """Return ``True`` if the article appears to reference Bitcoin mining."""

    query_lower = query.lower()
    fields = [
        str(article.get("title", "")),
        str(article.get("body", "")),
    ]
    for field in fields:
        if query_lower in field.lower():
            return True
    for concept in article.get("concepts", []) or []:
        label = concept.get("label", {}).get("eng") if isinstance(concept, dict) else None
        if label and query_lower in str(label).lower():
            return True
    return False


def format_tweet(article: Dict[str, Any]) -> str:
    """Build a tweet summarising the provided article."""

    title = (article.get("title") or "Untitled article").strip()
    summary = article.get("body") or ""
    url = article.get("url") or article.get("permalink") or ""

    summary = " ".join(summary.split())
    if summary:
        summary = summary[:160].rstrip()
    text = title
    if summary:
        text = f"{title} — {summary}"

    if url:
        candidate = f"{text} {url}".strip()
    else:
        candidate = text

    if len(candidate) <= MAX_TWEET_LENGTH:
        return candidate

    ellipsis = "…"
    available = MAX_TWEET_LENGTH - len(url) - 1 if url else MAX_TWEET_LENGTH
    truncated = text[: max(0, available - 1)].rstrip()
    truncated = truncated[:-1] + ellipsis if truncated.endswith(".") else truncated + ellipsis

    if url:
        return f"{truncated} {url}".strip()
    return truncated


def post_articles(
    twitter_client: tweepy.Client,
    articles: Iterable[Dict[str, Any]],
    *,
    state: MutableMapping[str, Any],
    dry_run: bool,
) -> None:
    """Post unseen articles to Twitter and update state."""

    posted_uris: List[str] = list(state.get("postedArticleUris", []))
    for article in articles:
        uri = article.get("uri")
        if not uri:
            LOGGER.debug("Skipping article without URI: %s", article)
            continue
        if uri in posted_uris:
            LOGGER.debug("Skipping already-posted article %s", uri)
            continue

        tweet = format_tweet(article)
        if dry_run:
            LOGGER.info("[DRY RUN] Would post tweet: %s", tweet)
        else:
            LOGGER.info("Posting tweet for article %s", uri)
            try:
                twitter_client.create_tweet(text=tweet)
            except tweepy.TweepyException as exc:  # pragma: no cover - external API
                LOGGER.error("Failed to post tweet for %s: %s", uri, exc)
                continue

        posted_uris.append(uri)
        if len(posted_uris) > POSTED_HISTORY_LIMIT:
            posted_uris = posted_uris[-POSTED_HISTORY_LIMIT:]

    state["postedArticleUris"] = posted_uris


def configure_logging(level: str) -> None:
    """Configure the root logger."""

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--query",
        default=os.getenv("BOT_QUERY", DEFAULT_QUERY),
        help="Keyword used to filter Bitcoin mining news (default: %(default)s)",
    )
    parser.add_argument(
        "--state-file",
        default=os.getenv(
            "BOT_STATE_PATH", Path(__file__).resolve().parent / "state.json"
        ),
        help="Location of the JSON file used to persist API checkpoints",
    )
    parser.add_argument(
        "--article-lang",
        default=os.getenv("BOT_ARTICLE_LANG"),
        help="Restrict Event Registry results to the provided language code",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=int(os.getenv("BOT_POLL_INTERVAL", "300")),
        help="Delay in seconds when running in --loop mode (default: %(default)s)",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Continuously poll Event Registry at the configured interval",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch data but do not post updates to Twitter",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("BOT_LOG_LEVEL", "INFO"),
        help="Logging verbosity (default: %(default)s)",
    )
    return parser.parse_args(argv)


def run_once(
    *,
    er: EventRegistry,
    twitter_client: tweepy.Client,
    query: str,
    article_lang: Optional[str],
    state: MutableMapping[str, Any],
    dry_run: bool,
) -> None:
    """Execute a single poll/post cycle."""

    try:
        articles = fetch_recent_activity(
            er, query=query, article_lang=article_lang, state=state
        )
    except Exception as exc:  # pragma: no cover - external API
        LOGGER.error("Failed to fetch recent activity: %s", exc)
        return

    if not articles:
        LOGGER.info("No Bitcoin mining updates found in this cycle")
        return

    post_articles(twitter_client, articles, state=state, dry_run=dry_run)


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point used by ``python -m bot.main``."""

    args = parse_args(argv)
    configure_logging(args.log_level)

    state_path = Path(args.state_file).expanduser().resolve()
    state = load_state(state_path)

    try:
        er = create_event_registry(os.getenv("EVENT_REGISTRY_API_KEY"))
        twitter_client = create_twitter_client()
    except BotConfigurationError as exc:
        LOGGER.error(str(exc))
        return 2

    LOGGER.info("Starting Bitcoin mining news poller")
    LOGGER.debug("Using state file at %s", state_path)

    try:
        while True:
            run_once(
                er=er,
                twitter_client=twitter_client,
                query=args.query,
                article_lang=args.article_lang,
                state=state,
                dry_run=args.dry_run,
            )
            save_state(state_path, state)
            if not args.loop:
                break
            LOGGER.debug("Sleeping for %s seconds", args.poll_interval)
            time.sleep(max(1, args.poll_interval))
    except KeyboardInterrupt:
        LOGGER.info("Interrupted by user; exiting.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
