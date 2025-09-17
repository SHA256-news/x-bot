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
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional

import tweepy
from eventregistry import (
    ArticleInfoFlags,
    EventRegistry,
    GetRecentArticles,
    QueryArticles,
    ReturnInfo,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_QUERY = "bitcoin mining"
MAX_TWEET_LENGTH = 280
POSTED_HISTORY_LIMIT = 250

UPDATES_AFTER_PARAMS: Mapping[str, str] = {
    "recentActivityArticlesNewsUpdatesAfterUri": "updatesAfterNewsUri",
    "recentActivityArticlesBlogsUpdatesAfterUri": "updatesAfterBlogUri",
    "recentActivityArticlesPrUpdatesAfterUri": "updatesAfterPrUri",
}


class BotConfigurationError(RuntimeError):
    """Raised when a required configuration value is missing."""


def load_state(path: Path) -> Dict[str, Any]:
    """Load persisted state from ``path``.

    The state dictionary always provides ``updatesAfterNewsUri``,
    ``updatesAfterBlogUri``, ``updatesAfterPrUri``, ``postedArticleUris``
    and ``bootstrapCompleted`` keys.
    """

    if not path.exists():
        return {
            "updatesAfterNewsUri": None,
            "updatesAfterBlogUri": None,
            "updatesAfterPrUri": None,
            "postedArticleUris": [],
            "bootstrapCompleted": False,
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
    data.setdefault("bootstrapCompleted", False)
    return data


def save_state(path: Path, state: MutableMapping[str, Any]) -> None:
    """Persist the state dictionary to ``path``."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)


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
    """Create an :class:`~eventregistry.EventRegistry` client."""

    if not api_key:
        raise BotConfigurationError(
            "EVENT_REGISTRY_API_KEY is required to connect to Event Registry."
        )
    LOGGER.debug("Initialising EventRegistry client")
    return EventRegistry(apiKey=api_key)


def create_twitter_client(*, allow_missing: bool = False) -> Optional[tweepy.Client]:
    """Create and configure the Tweepy client.

    When ``allow_missing`` is ``True`` the function returns ``None`` instead of
    raising :class:`BotConfigurationError` if required credentials are
    unavailable. This is primarily used for ``--dry-run`` executions where the
    Twitter client is not required to post updates.
    """

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


def build_article_return_info() -> ReturnInfo:
    """Return a :class:`~eventregistry.ReturnInfo` describing article fields."""

    return ReturnInfo(
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


def build_recent_articles_request(
    er: EventRegistry,
    *,
    query: str,
    article_lang: Optional[str],
    state: MutableMapping[str, Any],
) -> GetRecentArticles:
    """Prepare a :class:`~eventregistry.GetRecentArticles` request."""

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

    return GetRecentArticles(
        er,
        returnInfo=build_article_return_info(),
        **kwargs,
    )


def sync_updates_after(
    query_params: Mapping[str, Any],
    state: MutableMapping[str, Any],
) -> None:
    """Persist ``GetRecentArticles`` pagination checkpoints into ``state``."""

    for param_key, state_key in UPDATES_AFTER_PARAMS.items():
        value = query_params.get(param_key)
        if value:
            state[state_key] = value


def enrich_articles(
    er: EventRegistry,
    activity: Iterable[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Fetch enriched article details for the provided activity feed."""

    activity_list = list(activity)
    uris = [item.get("uri") for item in activity_list if item.get("uri")]
    if not uris:
        return activity_list

    query = QueryArticles.initWithArticleUriList(
        uris,
        returnInfo=build_article_return_info(),
    )
    try:
        response = er.execQuery(query)
    except Exception as exc:  # pragma: no cover - external API
        LOGGER.error("Failed to enrich articles: %s", exc)
        return activity_list
    if not isinstance(response, dict):
        LOGGER.warning("Unexpected article enrichment payload from Event Registry")
        return activity_list

    articles = response.get("articles", {}).get("results", [])
    if not isinstance(articles, list):
        LOGGER.warning("Unexpected article results returned during enrichment")
        return activity_list

    detailed_by_uri = {
        str(article.get("uri")): article
        for article in articles
        if isinstance(article, dict) and article.get("uri")
    }

    enriched: List[Dict[str, Any]] = []
    for item in activity_list:
        uri = item.get("uri")
        detailed = detailed_by_uri.get(str(uri)) if uri is not None else None
        if detailed:
            merged = {**item, **detailed}
        else:
            merged = dict(item)
        enriched.append(merged)
    return enriched


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
    try:
        activity = request.getUpdates()
    except Exception as exc:  # pragma: no cover - external API
        LOGGER.error("Failed to fetch recent activity: %s", exc)
        return []

    if not isinstance(activity, list):
        LOGGER.warning("Unexpected activity payload returned by Event Registry")
        activity = []

    sync_updates_after(request.queryParams, state)

    if not activity:
        LOGGER.info("No recent activity returned by Event Registry")
        return []

    enriched = enrich_articles(er, activity)
    LOGGER.info("Retrieved %d enriched articles from recent activity", len(enriched))
    return [
        article
        for article in enriched
        if is_bitcoin_mining_article(article, query=query)
    ]


def is_bitcoin_mining_article(article: Dict[str, Any], *, query: str) -> bool:
    """Return ``True`` if the article appears to reference Bitcoin mining."""

    query_lower = query.lower()
    fields = [
        str(article.get("title", "")),
        str(article.get("body", "")),
    ]
    
    # Check for exact query phrase (original behavior)
    for field in fields:
        if query_lower in field.lower():
            return True
    for concept in article.get("concepts", []) or []:
        label = concept.get("label", {}).get("eng") if isinstance(concept, dict) else None
        if label and query_lower in str(label).lower():
            return True
    
    # Relaxed matcher: Bitcoin signal + mining signal
    bitcoin_signals = ["bitcoin", "btc"]
    mining_signals = [
        "mining", "miner", "miners", "hashrate", "hash rate", "hashpower", "hash power",
        "difficulty", "asic", "asics", "rig", "rigs", "exahash", "terahash",
        "proof-of-work", "proof of work"
    ]
    
    # Collect all text content for signal detection
    all_content = []
    all_content.extend(fields)
    for concept in article.get("concepts", []) or []:
        label = concept.get("label", {}).get("eng") if isinstance(concept, dict) else None
        if label:
            all_content.append(str(label))
    
    combined_text = " ".join(all_content).lower()
    
    # Check if both Bitcoin signal and mining signal are present
    has_bitcoin_signal = any(signal in combined_text for signal in bitcoin_signals)
    has_mining_signal = any(signal in combined_text for signal in mining_signals)
    
    return has_bitcoin_signal and has_mining_signal


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
    twitter_client: Optional[tweepy.Client],
    articles: Iterable[Dict[str, Any]],
    *,
    state: MutableMapping[str, Any],
    dry_run: bool,
    bootstrap_count: int = 0,
) -> None:
    """Post unseen articles to Twitter and update state."""

    if not dry_run and twitter_client is None:
        raise BotConfigurationError(
            "A Twitter client is required when not running in dry-run mode."
        )

    posted_uris: List[str] = list(state.get("postedArticleUris", []))
    updated_history = False
    
    # Bootstrap logic: cap posts on first run if bootstrap active
    is_bootstrap_run = bootstrap_count > 0 and not state.get("bootstrapCompleted", False)
    post_count = 0
    
    for article in articles:
        uri = article.get("uri")
        if not uri:
            LOGGER.debug("Skipping article without URI: %s", article)
            continue
        if uri in posted_uris:
            LOGGER.debug("Skipping already-posted article %s", uri)
            continue

        # Check bootstrap limit
        if is_bootstrap_run and post_count >= bootstrap_count:
            LOGGER.info("Bootstrap mode: reached post limit of %d", bootstrap_count)
            break

        tweet = format_tweet(article)
        if dry_run:
            LOGGER.info("[DRY RUN] Would post tweet: %s", tweet)
            post_count += 1
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
        post_count += 1

    # Mark bootstrap as completed after first run
    if is_bootstrap_run:
        state["bootstrapCompleted"] = True
        updated_history = True
        LOGGER.info("Bootstrap mode completed after posting %d articles", post_count)

    if not dry_run and (updated_history or "postedArticleUris" not in state):
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
    parser.add_argument(
        "--bootstrap-count",
        type=int,
        default=int(os.getenv("BOT_BOOTSTRAP_COUNT", "0")),
        help="Number of articles to post on first run (default: %(default)s)",
    )
    return parser.parse_args(argv)


def run_once(
    *,
    er: EventRegistry,
    twitter_client: Optional[tweepy.Client],
    query: str,
    article_lang: Optional[str],
    state: MutableMapping[str, Any],
    dry_run: bool,
    bootstrap_count: int = 0,
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

    post_articles(twitter_client, articles, state=state, dry_run=dry_run, bootstrap_count=bootstrap_count)


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point used by ``python -m bot.main``."""

    args = parse_args(argv)
    configure_logging(args.log_level)

    state_path = Path(args.state_file).expanduser().resolve()
    state = load_state(state_path)

    try:
        er = create_event_registry(resolve_event_registry_api_key())
        twitter_client = create_twitter_client(allow_missing=args.dry_run)
    except BotConfigurationError as exc:
        LOGGER.error(str(exc))
        return 2

    LOGGER.info("Starting Bitcoin mining news poller")
    LOGGER.info("Effective query: %s", args.query)
    if args.bootstrap_count > 0:
        bootstrap_status = "active" if not state.get("bootstrapCompleted", False) else "completed"
        LOGGER.info("Bootstrap mode: %s (count: %d)", bootstrap_status, args.bootstrap_count)
    else:
        LOGGER.info("Bootstrap mode: disabled")
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
                bootstrap_count=args.bootstrap_count,
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
