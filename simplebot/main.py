"""Command-line entry point for the simple Bitcoin mining news bot.

This is a minimal, separate implementation that uses relaxed relevance filtering
and includes bootstrap logic for initial validation.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, MutableMapping, Optional

import tweepy
from eventregistry import (
    ArticleInfoFlags,
    EventRegistry,
    GetRecentArticles,
    QueryArticles,
    ReturnInfo,
)

from . import filters, state

LOGGER = logging.getLogger(__name__)
DEFAULT_QUERY = "bitcoin mining"
MAX_TWEET_LENGTH = 280
POSTED_HISTORY_LIMIT = 250

UPDATES_AFTER_PARAMS = {
    "recentActivityArticlesNewsUpdatesAfterUri": "updatesAfterNewsUri",
    "recentActivityArticlesBlogsUpdatesAfterUri": "updatesAfterBlogUri",
    "recentActivityArticlesPrUpdatesAfterUri": "updatesAfterPrUri",
}


class BotConfigurationError(RuntimeError):
    """Raised when a required configuration value is missing."""


def resolve_event_registry_api_key() -> str:
    """Return the Event Registry API key from known environment variables."""
    for env_name in ("EVENT_REGISTRY_API_KEY", "NEWSAPI_API_KEY"):
        api_key = os.getenv(env_name)
        if api_key:
            if env_name != "EVENT_REGISTRY_API_KEY":
                LOGGER.debug(
                    "Using %s as the Event Registry credential source", env_name
                )
            return api_key
    raise BotConfigurationError(
        "EVENT_REGISTRY_API_KEY is required to connect to Event Registry. "
        "Provide the key via EVENT_REGISTRY_API_KEY or NEWSAPI_API_KEY."
    )


def create_event_registry(api_key: str) -> EventRegistry:
    """Create an EventRegistry client."""
    if not api_key:
        raise BotConfigurationError(
            "EVENT_REGISTRY_API_KEY is required to connect to Event Registry."
        )
    LOGGER.debug("Initialising EventRegistry client")
    return EventRegistry(apiKey=api_key)


def create_twitter_client(*, allow_missing: bool = False) -> Optional[tweepy.Client]:
    """Create and configure the Tweepy client."""
    bearer_token = os.getenv("TWITTER_BEARER_TOKEN")
    api_key = os.getenv("TWITTER_API_KEY")
    api_secret = os.getenv("TWITTER_API_SECRET")
    access_token = os.getenv("TWITTER_ACCESS_TOKEN")
    access_secret = os.getenv("TWITTER_ACCESS_TOKEN_SECRET")
    secret_env_name = "TWITTER_ACCESS_TOKEN_SECRET"
    if not access_secret:
        legacy_secret = os.getenv("TWITTER_ACCESS_SECRET")
        if legacy_secret:
            LOGGER.debug(
                "Using TWITTER_ACCESS_SECRET as fallback for TWITTER_ACCESS_TOKEN_SECRET"
            )
            access_secret = legacy_secret
            secret_env_name = "TWITTER_ACCESS_SECRET"

    missing = [
        name
        for name, value in [
            ("TWITTER_API_KEY", api_key),
            ("TWITTER_API_SECRET", api_secret),
            ("TWITTER_ACCESS_TOKEN", access_token),
            (secret_env_name, access_secret),
        ]
        if not value
    ]
    if missing:
        joined = ", ".join(missing)
        if allow_missing:
            LOGGER.info(
                "Skipping Twitter client initialisation; missing credentials: %s",
                joined,
            )
            return None
        raise BotConfigurationError(
            f"Twitter API credentials are required to post updates: {joined}"
        )

    LOGGER.debug("Initialising Tweepy client")
    return tweepy.Client(
        bearer_token=bearer_token,
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_secret,
    )


def build_article_return_info() -> ReturnInfo:
    """Return a ReturnInfo describing article fields."""
    return ReturnInfo(
        articleInfo=ArticleInfoFlags(
            title=True,
            body=True,
            url=True,
            concepts=True,
        )
    )


def build_recent_articles_request(
    er: EventRegistry,
    *,
    query: str,
    article_lang: Optional[str],
    bot_state: MutableMapping[str, Any],
) -> GetRecentArticles:
    """Prepare a GetRecentArticles request."""
    kwargs = {}

    # Set pagination checkpoints from state
    for param_key, state_key in UPDATES_AFTER_PARAMS.items():
        value = bot_state.get(state_key)
        if value:
            kwargs[param_key] = value

    # Restrict results to the configured language when provided
    if article_lang:
        kwargs["articleLang"] = article_lang

    # Event Registry expects filters prefixed with "recentActivityArticles"
    kwargs["recentActivityArticlesKeyword"] = query

    return GetRecentArticles(
        er,
        returnInfo=build_article_return_info(),
        **kwargs,
    )


def sync_updates_after(
    query_params: Dict[str, Any],
    bot_state: MutableMapping[str, Any],
) -> None:
    """Persist GetRecentArticles pagination checkpoints into state."""
    for param_key, state_key in UPDATES_AFTER_PARAMS.items():
        value = query_params.get(param_key)
        if value:
            bot_state[state_key] = value


def enrich_articles(
    er: EventRegistry,
    activity: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Fetch enriched article details for the provided activity feed."""
    uris = [item.get("uri") for item in activity if item.get("uri")]
    if not uris:
        return activity

    query = QueryArticles.initWithArticleUriList(
        uris,
        returnInfo=build_article_return_info(),
    )
    try:
        response = er.execQuery(query)
    except Exception as exc:  # pragma: no cover - external API
        LOGGER.error("Failed to enrich articles: %s", exc)
        return activity
    if not isinstance(response, dict):
        LOGGER.warning("Unexpected article enrichment payload from Event Registry")
        return activity

    articles = response.get("articles", {}).get("results", [])
    if not isinstance(articles, list):
        LOGGER.warning("Unexpected article results returned during enrichment")
        return activity

    detailed_by_uri = {
        str(article.get("uri")): article
        for article in articles
        if isinstance(article, dict) and article.get("uri")
    }

    enriched: List[Dict[str, Any]] = []
    for item in activity:
        uri = item.get("uri")
        detailed = detailed_by_uri.get(str(uri)) if uri is not None else None
        if detailed:
            merged = {**item, **detailed}
        else:
            merged = dict(item)
        enriched.append(merged)
    return enriched


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


def fetch_recent_activity(
    er: EventRegistry,
    *,
    query: str,
    article_lang: Optional[str],
    bot_state: MutableMapping[str, Any],
) -> List[Dict[str, Any]]:
    """Fetch the recent activity list and update state checkpoints."""
    request = build_recent_articles_request(
        er, query=query, article_lang=article_lang, bot_state=bot_state
    )

    LOGGER.debug("Requesting recent activity from Event Registry")
    try:
        activity = request.getUpdates()
    except Exception as exc:  # pragma: no cover - external API
        LOGGER.error("Failed to fetch recent activity: %s", exc)
        return []

    if not isinstance(activity, list):
        LOGGER.warning("Unexpected activity payload returned by Event Registry")
        activity = []

    sync_updates_after(request.queryParams, bot_state)

    if not activity:
        LOGGER.info("No recent activity returned by Event Registry")
        return []

    enriched = enrich_articles(er, activity)
    LOGGER.info("Fetched %d items; filtering for relevance", len(enriched))
    
    relevant = [
        article
        for article in enriched
        if filters.is_relevant_article(article, query=query)
    ]
    
    LOGGER.info("Fetched %d items; %d relevant after filter", len(enriched), len(relevant))
    return relevant


def post_articles(
    twitter_client: Optional[tweepy.Client],
    articles: List[Dict[str, Any]],
    *,
    bot_state: MutableMapping[str, Any],
    dry_run: bool,
    bootstrap_count: int = 0,
) -> None:
    """Post unseen articles to Twitter and update state with bootstrap support."""
    if not dry_run and twitter_client is None:
        raise BotConfigurationError(
            "A Twitter client is required when not running in dry-run mode."
        )

    posted_uris: List[str] = list(bot_state.get("postedArticleUris", []))
    updated_history = False
    posted_count = 0

    # Check if this is a bootstrap run
    is_bootstrap = bootstrap_count > 0 and not bot_state.get("bootstrapCompleted", False)
    
    if is_bootstrap:
        LOGGER.info("Bootstrap mode: will post up to %d articles", bootstrap_count)

    for article in articles:
        uri = article.get("uri")
        if not uri:
            LOGGER.debug("Skipping article without URI: %s", article)
            continue
        if uri in posted_uris:
            LOGGER.debug("Skipping already-posted article %s", uri)
            continue

        # Check bootstrap limit
        if is_bootstrap and posted_count >= bootstrap_count:
            LOGGER.info("Bootstrap limit reached (%d posts)", bootstrap_count)
            break

        tweet = format_tweet(article)
        if dry_run:
            LOGGER.info("[DRY RUN] Would post tweet: %s", tweet)
            posted_count += 1
            continue

        LOGGER.info("Posting tweet for article %s", uri)
        try:
            twitter_client.create_tweet(text=tweet)
        except tweepy.TweepyException as exc:  # pragma: no cover - external API
            LOGGER.error("Failed to post tweet for %s: %s", uri, exc)
            continue

        posted_uris.append(uri)
        if len(posted_uris) > POSTED_HISTORY_LIMIT:
            posted_uris = posted_uris[-POSTED_HISTORY_LIMIT:]
        updated_history = True
        posted_count += 1

    # Mark bootstrap as completed if this was a bootstrap run
    if is_bootstrap:
        bot_state["bootstrapCompleted"] = True
        updated_history = True
        LOGGER.info("Bootstrap completed; posted %d articles", posted_count)

    if not dry_run and (updated_history or "postedArticleUris" not in bot_state):
        bot_state["postedArticleUris"] = posted_uris


def configure_logging(level: str) -> None:
    """Configure the root logger."""
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Simple Bitcoin mining news bot with relaxed filtering and bootstrap support.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--query",
        default=os.getenv("BOT_QUERY", DEFAULT_QUERY),
        help="Keyword used to filter Bitcoin mining news (default: %(default)s)",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=os.getenv("BOT_STATE_PATH", "simplebot-state.json"),
        help="Location of the JSON file used to persist API checkpoints",
    )
    parser.add_argument(
        "--article-lang",
        default=os.getenv("BOT_ARTICLE_LANG"),
        help="Restrict Event Registry results to the provided language code",
    )
    parser.add_argument(
        "--bootstrap-count",
        type=int,
        default=0,
        help="Bootstrap: post up to N articles on first run (default: %(default)s)",
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
    twitter_client: Optional[tweepy.Client],
    query: str,
    article_lang: Optional[str],
    bot_state: MutableMapping[str, Any],
    dry_run: bool,
    bootstrap_count: int = 0,
) -> None:
    """Execute a single poll/post cycle."""
    # Log effective configuration at startup
    LOGGER.info("Starting simplebot with query='%s', lang='%s', bootstrap=%d", 
                query, article_lang or "any", bootstrap_count)

    try:
        articles = fetch_recent_activity(
            er, query=query, article_lang=article_lang, bot_state=bot_state
        )
    except Exception as exc:  # pragma: no cover - external API
        LOGGER.error("Failed to fetch recent activity: %s", exc)
        return

    if not articles:
        LOGGER.info("No relevant articles found in this cycle")
        # Still complete bootstrap even if no articles found
        if bootstrap_count > 0 and not bot_state.get("bootstrapCompleted", False):
            bot_state["bootstrapCompleted"] = True
            LOGGER.info("Bootstrap completed with 0 articles")
        return

    post_articles(
        twitter_client, 
        articles, 
        bot_state=bot_state, 
        dry_run=dry_run,
        bootstrap_count=bootstrap_count
    )


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point used by python -m simplebot.main."""
    args = parse_args(argv)
    configure_logging(args.log_level)

    try:
        api_key = resolve_event_registry_api_key()
        er = create_event_registry(api_key)
        twitter_client = create_twitter_client(allow_missing=args.dry_run)
        
        bot_state = state.load_state(args.state_file)
        
        run_once(
            er=er,
            twitter_client=twitter_client,
            query=args.query,
            article_lang=args.article_lang,
            bot_state=bot_state,
            dry_run=args.dry_run,
            bootstrap_count=args.bootstrap_count,
        )
        
        state.save_state(args.state_file, bot_state)
        
    except BotConfigurationError as exc:
        LOGGER.error("Configuration error: %s", exc)
        return 1
    except KeyboardInterrupt:
        LOGGER.info("Interrupted by user; exiting.")

    return 0


if __name__ == "__main__":
    sys.exit(main())