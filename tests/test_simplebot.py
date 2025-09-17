"""Unit tests for simplebot components."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from simplebot import filters, state
from simplebot.main import BotConfigurationError, post_articles, format_tweet


class SimplebotFiltersTests(unittest.TestCase):
    """Test the relaxed filtering logic."""
    
    def test_exact_query_match_in_title(self):
        article = {"title": "Bitcoin mining trends", "body": "", "concepts": []}
        self.assertTrue(filters.is_relevant_article(article, query="bitcoin mining"))
    
    def test_exact_query_match_in_body(self):
        article = {"title": "News", "body": "Latest bitcoin mining report", "concepts": []}
        self.assertTrue(filters.is_relevant_article(article, query="bitcoin mining"))
    
    def test_exact_query_match_in_concepts(self):
        article = {
            "title": "News", 
            "body": "", 
            "concepts": [{"label": {"eng": "Bitcoin mining equipment"}}]
        }
        self.assertTrue(filters.is_relevant_article(article, query="bitcoin mining"))
    
    def test_bitcoin_and_mining_terms_match(self):
        article = {"title": "BTC difficulty", "body": "New ASIC miners", "concepts": []}
        self.assertTrue(filters.is_relevant_article(article, query="crypto"))
    
    def test_bitcoin_term_without_mining_term_no_match(self):
        article = {"title": "Bitcoin price update", "body": "New highs", "concepts": []}
        self.assertFalse(filters.is_relevant_article(article, query="crypto"))
    
    def test_mining_term_without_bitcoin_term_no_match(self):
        article = {"title": "Gold mining news", "body": "New equipment", "concepts": []}
        self.assertFalse(filters.is_relevant_article(article, query="crypto"))
    
    def test_case_insensitive_matching(self):
        article = {"title": "BITCOIN MINING NEWS", "body": "", "concepts": []}
        self.assertTrue(filters.is_relevant_article(article, query="bitcoin mining"))
    
    def test_no_match(self):
        article = {"title": "Stock market", "body": "Wall street update", "concepts": []}
        self.assertFalse(filters.is_relevant_article(article, query="crypto"))


class SimplebotStateTests(unittest.TestCase):
    """Test state management functionality."""
    
    def test_load_nonexistent_state_returns_defaults(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "nonexistent.json"
            loaded_state = state.load_state(state_path)
            
            expected = {
                "updatesAfterNewsUri": None,
                "updatesAfterBlogUri": None,
                "updatesAfterPrUri": None,
                "postedArticleUris": [],
                "bootstrapCompleted": False,
            }
            self.assertEqual(loaded_state, expected)
    
    def test_save_and_load_state_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "test-state.json"
            
            test_state = {
                "updatesAfterNewsUri": "12345",
                "postedArticleUris": ["uri1", "uri2"],
                "bootstrapCompleted": True,
            }
            
            state.save_state(state_path, test_state)
            loaded_state = state.load_state(state_path)
            
            # Should include defaults for missing keys
            self.assertEqual(loaded_state["updatesAfterNewsUri"], "12345")
            self.assertEqual(loaded_state["postedArticleUris"], ["uri1", "uri2"])
            self.assertEqual(loaded_state["bootstrapCompleted"], True)
            self.assertIsNone(loaded_state["updatesAfterBlogUri"])
    
    def test_load_invalid_json_raises_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "invalid.json"
            with state_path.open("w") as f:
                f.write("invalid json{")
            
            with self.assertRaises(RuntimeError):
                state.load_state(state_path)


class SimplebotMainTests(unittest.TestCase):
    """Test main simplebot functions."""
    
    def test_format_tweet_same_as_main_bot(self):
        """Ensure tweet formatting is consistent with main bot."""
        article = {
            "title": "Bitcoin Mining Report",
            "body": "This is a detailed body text that might be quite long",
            "url": "https://example.com/article",
        }
        
        tweet = format_tweet(article)
        self.assertIn("Bitcoin Mining Report", tweet)
        self.assertIn("https://example.com/article", tweet)
        self.assertLessEqual(len(tweet), 280)
    
    def test_post_articles_bootstrap_logic(self):
        """Test bootstrap posting logic."""
        mock_client = Mock()
        
        # Fresh state (bootstrap not completed)
        test_state = {
            "postedArticleUris": [],
            "bootstrapCompleted": False,
        }
        
        articles = [
            {"uri": "uri1", "title": "Article 1", "body": "Body 1", "url": "http://ex1.com"},
            {"uri": "uri2", "title": "Article 2", "body": "Body 2", "url": "http://ex2.com"},
        ]
        
        # Bootstrap with limit of 1
        post_articles(
            mock_client,
            articles,
            bot_state=test_state,
            dry_run=False,
            bootstrap_count=1,
        )
        
        # Should post only 1 article and mark bootstrap completed
        self.assertEqual(mock_client.create_tweet.call_count, 1)
        self.assertTrue(test_state["bootstrapCompleted"])
        self.assertEqual(len(test_state["postedArticleUris"]), 1)
    
    def test_post_articles_no_bootstrap_after_completed(self):
        """Test that bootstrap doesn't activate if already completed."""
        mock_client = Mock()
        
        # State with bootstrap already completed
        test_state = {
            "postedArticleUris": [],
            "bootstrapCompleted": True,
        }
        
        articles = [
            {"uri": "uri1", "title": "Article 1", "body": "Body 1", "url": "http://ex1.com"},
            {"uri": "uri2", "title": "Article 2", "body": "Body 2", "url": "http://ex2.com"},
        ]
        
        # Should post all articles normally (no bootstrap limit)
        post_articles(
            mock_client,
            articles,
            bot_state=test_state,
            dry_run=False,
            bootstrap_count=1,  # This should be ignored
        )
        
        # Should post both articles since bootstrap is already completed
        self.assertEqual(mock_client.create_tweet.call_count, 2)
    
    def test_post_articles_dry_run_bootstrap(self):
        """Test dry run with bootstrap logic."""
        test_state = {
            "postedArticleUris": [],
            "bootstrapCompleted": False,
        }
        
        articles = [
            {"uri": "uri1", "title": "Article 1", "body": "Body 1"},
        ]
        
        post_articles(
            None,  # No client needed for dry run
            articles,
            bot_state=test_state,
            dry_run=True,
            bootstrap_count=1,
        )
        
        # Should complete bootstrap even in dry run
        self.assertTrue(test_state["bootstrapCompleted"])
        self.assertEqual(test_state["postedArticleUris"], [])  # No URIs added in dry run


if __name__ == "__main__":
    unittest.main()