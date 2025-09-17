# Simple Bot - Clean Validation Implementation

This is a minimal, separate implementation of the Bitcoin mining news bot designed for validation and testing. It runs alongside the existing main bot without interfering with its operation.

## Features

- **Relaxed relevance filtering**: Matches articles containing either:
  - The exact query substring anywhere in title/body/concepts, OR
  - At least one Bitcoin term AND one Mining term (case-insensitive)
- **Bootstrap support**: One-time posting of up to N articles on first run
- **Separate state management**: Uses its own state file to avoid conflicts
- **Debug-friendly**: Designed for validation with detailed logging

## Bitcoin and Mining Terms

**Bitcoin terms**: bitcoin, btc  
**Mining terms**: mining, miner, miners, hashrate, hash rate, hashpower, hash power, difficulty, asic, asics, rig, rigs, exahash, terahash, proof-of-work, proof of work

## Usage

### Manual Testing

```bash
# Test run with debug logging and bootstrap
python -m simplebot.main \
  --query "bitcoin" \
  --article-lang "eng" \
  --bootstrap-count 1 \
  --log-level DEBUG \
  --state-file ".github/bot-state/state.json"

# Dry run for testing
python -m simplebot.main \
  --query "bitcoin mining" \
  --dry-run \
  --log-level DEBUG
```

### GitHub Actions Workflow

The simple bot has its own workflow: `.github/workflows/simple-post.yml`

- Manually triggered via `workflow_dispatch`
- Uses DEBUG logging for validation
- Broadened query ("bitcoin") for initial testing
- Bootstrap count of 1 for controlled validation
- Commits state file changes back to repository

## Required Secrets

The workflow requires the same secrets as the main bot:

- `EVENT_REGISTRY_API_KEY`
- `TWITTER_API_KEY`
- `TWITTER_API_SECRET`
- `TWITTER_ACCESS_TOKEN`
- `TWITTER_ACCESS_TOKEN_SECRET`

## Configuration

Environment variables are supported:

- `BOT_QUERY`: Query string (default: "bitcoin mining")
- `BOT_ARTICLE_LANG`: Language filter (default: none)
- `BOT_LOG_LEVEL`: Logging level (default: "INFO")
- `BOT_STATE_PATH`: State file path (default: "simplebot-state.json")

## State File

The simple bot maintains its own state file with:

- Event Registry pagination checkpoints
- Posted article URI history (up to 250 entries)
- `bootstrapCompleted` flag for one-time bootstrap logic

## Notes

- This is a **temporary validation implementation** with intentionally broad settings
- The initial workflow uses DEBUG logs and a broadened query for validation
- After successful testing, the query and log levels should be tightened
- The implementation avoids modifying the existing bot or its workflow
- State management is separate to prevent conflicts with the main bot

## Next Steps

1. Run the workflow manually to verify end-to-end operation
2. Check that articles are fetched and filtered correctly
3. Verify state file is committed when updated
4. Tighten configuration (query specificity, log level) in follow-up PRs