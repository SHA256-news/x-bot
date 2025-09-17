import json
import os
import tempfile
import unittest
from pathlib import Path

from bot import main
from unittest import mock


class FakeEventRegistry:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def execQuery(self, query):  # pragma: no cover - simple pass-through
        self.requests.append(query)
        return self.response


class FakeTwitterClient:
    def __init__(self):
        self.tweets = []

    def create_tweet(self, text):  # pragma: no cover - simple pass-through
        self.tweets.append(text)


class MainModuleTests(unittest.TestCase):
    def test_create_twitter_client_allows_missing_when_requested(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            client = main.create_twitter_client(allow_missing=True)
        self.assertIsNone(client)

    def test_create_twitter_client_requires_credentials_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(main.BotConfigurationError):
                main.create_twitter_client()

    def test_sync_updates_after_writes_all_known_keys(self):
        state = {
            "updatesAfterNewsUri": None,
            "updatesAfterBlogUri": None,
            "updatesAfterPrUri": None,
        }
        params = {
            "recentActivityArticlesNewsUpdatesAfterUri": "news-uri",
            "recentActivityArticlesBlogsUpdatesAfterUri": "blog-uri",
            "recentActivityArticlesPrUpdatesAfterUri": "pr-uri",
        }

        main.sync_updates_after(params, state)

        self.assertEqual(state["updatesAfterNewsUri"], "news-uri")
        self.assertEqual(state["updatesAfterBlogUri"], "blog-uri")
        self.assertEqual(state["updatesAfterPrUri"], "pr-uri")

    def test_format_tweet_truncates_summary(self):
        article = {
            "title": "Bitcoin Mining Breakthrough",
            "body": " ".join(["A" * 80] * 5),
            "url": "https://example.com/article",
        }

        tweet = main.format_tweet(article)

        self.assertLessEqual(len(tweet), main.MAX_TWEET_LENGTH)
        self.assertIn("https://example.com/article", tweet)

    def test_enrich_articles_merges_detailed_results(self):
        response = {
            "articles": {
                "results": [
                    {
                        "uri": "uri-1",
                        "title": "Detailed",
                        "body": "Fresh context",
                    }
                ]
            }
        }
        er = FakeEventRegistry(response)
        enriched = main.enrich_articles(er, [{"uri": "uri-1", "title": "Original"}])

        self.assertEqual(len(enriched), 1)
        self.assertEqual(enriched[0]["title"], "Detailed")
        self.assertEqual(enriched[0]["body"], "Fresh context")

    def test_enrich_articles_falls_back_on_unexpected_payload(self):
        er = FakeEventRegistry(None)
        activity = [{"uri": "uri-1", "title": "Original"}]

        enriched = main.enrich_articles(er, activity)

        self.assertEqual(enriched, activity)

    def test_post_articles_skips_duplicates_and_tracks_new_entries(self):
        client = FakeTwitterClient()
        state = {"postedArticleUris": ["uri-1"]}
        articles = [
            {"uri": "uri-1", "title": "Duplicate", "body": "Already posted"},
            {
                "uri": "uri-2",
                "title": "Brand new Mining Report",
                "body": "Details about Bitcoin mining advancements",
                "url": "https://example.com/report",
            },
        ]

        main.post_articles(client, articles, state=state, dry_run=False)

        self.assertEqual(len(client.tweets), 1)
        self.assertIn("uri-2", state["postedArticleUris"])
        self.assertNotIn("uri-1", client.tweets[0])  # ensure we only posted new URI

    def test_post_articles_dry_run_does_not_post_or_update_state(self):
        client = FakeTwitterClient()
        posted = ["uri-1"]
        state = {"postedArticleUris": posted}
        articles = [
            {
                "uri": "uri-2",
                "title": "Brand new Mining Report",
                "body": "Details about Bitcoin mining advancements",
                "url": "https://example.com/report",
            }
        ]

        main.post_articles(client, articles, state=state, dry_run=True)

        self.assertEqual(client.tweets, [])
        self.assertIs(state["postedArticleUris"], posted)
        self.assertEqual(state["postedArticleUris"], ["uri-1"])

    def test_post_articles_requires_client_when_not_dry_run(self):
        state = {"postedArticleUris": []}
        with self.assertRaises(main.BotConfigurationError):
            main.post_articles(
                None,
                [{"uri": "uri-3", "title": "Example", "body": "Body"}],
                state=state,
                dry_run=False,
            )

    def test_is_bitcoin_mining_article_detects_keyword(self):
        article = {"title": "Global Bitcoin mining trends", "body": ""}

        self.assertTrue(main.is_bitcoin_mining_article(article, query="bitcoin mining"))
        self.assertFalse(
            main.is_bitcoin_mining_article({"title": "Random", "body": "Irrelevant"}, query="bitcoin mining")
        )

    def test_is_bitcoin_mining_article_relaxed_matcher(self):
        # Test case 1: BTC + hashrate + miners + ASICs
        article1 = {
            "title": "BTC hashrate hits new ATH as miners deploy new ASICs",
            "body": "",
            "concepts": []
        }
        self.assertTrue(main.is_bitcoin_mining_article(article1, query="bitcoin mining"))

        # Test case 2: Bitcoin + difficulty + exahash
        article2 = {
            "title": "Bitcoin difficulty adjusts after exahash surge",
            "body": "",
            "concepts": []
        }
        self.assertTrue(main.is_bitcoin_mining_article(article2, query="bitcoin mining"))

        # Test case 3: Bitcoin signal but no mining signal
        article3 = {
            "title": "Bitcoin price hits new high",
            "body": "Trading volume increased.",
            "concepts": []
        }
        self.assertFalse(main.is_bitcoin_mining_article(article3, query="bitcoin mining"))

        # Test case 4: Mining signal but no Bitcoin signal
        article4 = {
            "title": "Gold mining operations expand",
            "body": "New miners deployed in the field.",
            "concepts": []
        }
        self.assertFalse(main.is_bitcoin_mining_article(article4, query="bitcoin mining"))

        # Test case 5: Both signals in concepts
        article5 = {
            "title": "Industry News",
            "body": "Various updates.",
            "concepts": [
                {"label": {"eng": "Bitcoin technology"}},
                {"label": {"eng": "Mining equipment"}}
            ]
        }
        self.assertTrue(main.is_bitcoin_mining_article(article5, query="bitcoin mining"))

    def test_bootstrap_logic_caps_posts_and_sets_completed(self):
        # Test bootstrap logic with fresh state
        client = FakeTwitterClient()
        state = {"postedArticleUris": [], "bootstrapCompleted": False}
        articles = [
            {"uri": "uri-1", "title": "First Article", "body": "Content", "url": "https://example.com/1"},
            {"uri": "uri-2", "title": "Second Article", "body": "Content", "url": "https://example.com/2"},
            {"uri": "uri-3", "title": "Third Article", "body": "Content", "url": "https://example.com/3"},
        ]

        # Bootstrap with count=1, should only post 1 article
        main.post_articles(client, articles, state=state, dry_run=False, bootstrap_count=1)

        self.assertEqual(len(client.tweets), 1)
        self.assertEqual(len(state["postedArticleUris"]), 1)
        self.assertTrue(state["bootstrapCompleted"])
        self.assertIn("uri-1", state["postedArticleUris"])

    def test_bootstrap_logic_with_zero_matches(self):
        # Test bootstrap logic when no articles match
        client = FakeTwitterClient()
        state = {"postedArticleUris": [], "bootstrapCompleted": False}
        articles = []  # No articles

        # Bootstrap with count=1, should still set bootstrapCompleted=True
        main.post_articles(client, articles, state=state, dry_run=False, bootstrap_count=1)

        self.assertEqual(len(client.tweets), 0)
        self.assertEqual(len(state["postedArticleUris"]), 0)
        self.assertTrue(state["bootstrapCompleted"])

    def test_bootstrap_logic_already_completed(self):
        # Test that bootstrap logic is skipped when already completed
        client = FakeTwitterClient()
        state = {"postedArticleUris": [], "bootstrapCompleted": True}
        articles = [
            {"uri": "uri-1", "title": "First Article", "body": "Content", "url": "https://example.com/1"},
            {"uri": "uri-2", "title": "Second Article", "body": "Content", "url": "https://example.com/2"},
        ]

        # Bootstrap with count=1, but already completed, should post all articles
        main.post_articles(client, articles, state=state, dry_run=False, bootstrap_count=1)

        self.assertEqual(len(client.tweets), 2)
        self.assertEqual(len(state["postedArticleUris"]), 2)
        self.assertTrue(state["bootstrapCompleted"])

    def test_state_round_trip(self):
        with tempfile.TemporaryDirectory() as tempdir:
            path = Path(tempdir) / "state.json"
            state = main.load_state(path)
            self.assertEqual(
                state,
                {
                    "updatesAfterNewsUri": None,
                    "updatesAfterBlogUri": None,
                    "updatesAfterPrUri": None,
                    "postedArticleUris": [],
                    "bootstrapCompleted": False,
                },
            )

            state["updatesAfterNewsUri"] = "news"
            main.save_state(path, state)

            with path.open("r", encoding="utf-8") as handle:
                saved = json.load(handle)

            self.assertEqual(saved["updatesAfterNewsUri"], "news")
            self.assertIn("postedArticleUris", saved)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
