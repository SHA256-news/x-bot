"""Relaxed relevance matching for simplebot."""

from typing import Any, Dict, Set


# Bitcoin terms for relaxed matching
BITCOIN_TERMS: Set[str] = {"bitcoin", "btc"}

# Mining terms for relaxed matching
MINING_TERMS: Set[str] = {
    "mining", "miner", "miners", "hashrate", "hash rate", 
    "hashpower", "hash power", "difficulty", "asic", "asics", 
    "rig", "rigs", "exahash", "terahash", "proof-of-work", "proof of work"
}


def is_relevant_article(article: Dict[str, Any], *, query: str) -> bool:
    """Return True if the article is relevant based on relaxed criteria.
    
    Match if:
    1. The exact query substring appears anywhere (title/body/concepts), OR
    2. At least one Bitcoin term AND one Mining term appear anywhere (case-insensitive)
       across title/body/concepts.
    """
    query_lower = query.lower()
    
    # Get text fields to search
    fields = [
        str(article.get("title", "")),
        str(article.get("body", "")),
    ]
    
    # Add concept labels
    for concept in article.get("concepts", []) or []:
        if isinstance(concept, dict):
            label = concept.get("label", {}).get("eng")
            if label:
                fields.append(str(label))
    
    # Join all text for searching
    all_text = " ".join(fields).lower()
    
    # Check for exact query match
    if query_lower in all_text:
        return True
    
    # Check for Bitcoin term AND Mining term
    has_bitcoin_term = any(term in all_text for term in BITCOIN_TERMS)
    has_mining_term = any(term in all_text for term in MINING_TERMS)
    
    return has_bitcoin_term and has_mining_term