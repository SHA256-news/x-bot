import json
import tempfile
import unittest
from pathlib import Path

from bot import main


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

    def test_is_bitcoin_mining_article_detects_keyword(self):
        article = {"title": "Global Bitcoin mining trends", "body": ""}

        self.assertTrue(main.is_bitcoin_mining_article(article, query="bitcoin mining"))
        self.assertFalse(
            main.is_bitcoin_mining_article({"title": "Random", "body": "Irrelevant"}, query="bitcoin mining")
        )

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
