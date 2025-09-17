# x-bot

Automation tools for sharing the latest Bitcoin mining news on Twitter.

## Features

- Polls the [Event Registry](https://eventregistry.org/) minute stream for
  recent Bitcoin mining activity using the ``GetRecentArticles`` API.
- Persists the latest ``updatesAfterNewsUri``, ``updatesAfterBlogUri`` and
  ``updatesAfterPrUri`` checkpoints so that repeated runs only fetch new
  content.
- Posts concise summaries for unseen articles to Twitter via the Tweepy
  client.

## Prerequisites

1. Python 3.10+.
2. Valid Event Registry API key with access to the minute stream.
3. A Twitter/X developer application with read and write permissions.

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

The bot is configured entirely through environment variables. In the Codex
environment, define the required secrets via the **Secrets** pane and allow the
runner to inject them into your commands. The provided `scripts/setup.sh`
helper script validates that the secrets are available and can be used to run
commands with the injected credentials.

```bash
# Check credentials and run the bot in dry-run mode
./scripts/setup.sh python -m bot.main --dry-run
```

Set the following values before running the script:

| Variable | Description |
| --- | --- |
| `EVENT_REGISTRY_API_KEY` | API key used to authenticate with Event Registry. (`NEWSAPI_API_KEY` is accepted as a fallback.) |
| `TWITTER_BEARER_TOKEN` | Optional bearer token for Tweepy (used for rate limit handling). |
| `TWITTER_API_KEY` | Twitter consumer key. |
| `TWITTER_API_SECRET` | Twitter consumer secret. |
| `TWITTER_ACCESS_TOKEN` | Twitter access token with write permissions. |
| `TWITTER_ACCESS_TOKEN_SECRET` | Twitter access token secret. (`TWITTER_ACCESS_SECRET` is also recognised for compatibility.) |

Optional environment variables provide additional control:

| Variable | Default | Purpose |
| --- | --- | --- |
| `BOT_QUERY` | `bitcoin mining` | Keyword used to detect relevant articles. |
| `BOT_STATE_PATH` | `bot/state.json` | File path used to persist API checkpoints and posted article URIs. |
| `BOT_ARTICLE_LANG` | *(unset)* | Restrict Event Registry results to a specific ISO language code. |
| `BOT_POLL_INTERVAL` | `300` | Delay (in seconds) between polls when running with `--loop`. |
| `BOT_LOG_LEVEL` | `INFO` | Logging verbosity. |
| `BOT_BOOTSTRAP_COUNT` | `0` | Number of articles to post on first run (bootstrap mode). |

The bot uses a relaxed matcher that detects Bitcoin mining articles in two ways:
1. **Exact match**: The configured query phrase appears in title/body/concepts (original behavior)
2. **Signal-based match**: Both a Bitcoin signal (`bitcoin`, `btc`) AND a mining signal (`mining`, `miner`, `miners`, `hashrate`, `hash rate`, `hashpower`, `hash power`, `difficulty`, `asic`, `asics`, `rig`, `rigs`, `exahash`, `terahash`, `proof-of-work`, `proof of work`) appear across title/body/concepts

The state file stores the last known `updatesAfterNewsUri`,
`updatesAfterBlogUri`, `updatesAfterPrUri`, a short history of article
URIs that have already been posted to Twitter, and a `bootstrapCompleted` flag. Deleting this file resets the
checkpoints.

## Usage

After configuring the environment variables, run the bot directly or via the
setup helper:

```bash
# Direct invocation (environment variables already exported)
python -m bot.main

# Or via the Codex-aware helper script
./scripts/setup.sh python -m bot.main
```

Key command-line options:

- `--loop`: continuously poll Event Registry at the configured interval.
- `--dry-run`: fetch and log new articles without posting to Twitter.
- `--state-file`: override the location of the JSON state file.
- `--bootstrap-count`: number of articles to post on first run (bootstrap mode).

Example (continuous polling every 10 minutes):

```bash
BOT_POLL_INTERVAL=600 python -m bot.main --loop
```

### Bootstrap Mode

When `BOT_BOOTSTRAP_COUNT` is set to a positive integer, the bot will cap the number of posts to that value on the first run with fresh state. After the first run, it will mark bootstrap as completed and post articles normally. This is useful for initial deployments to avoid flooding Twitter with backlog articles.

```bash
# Bootstrap with maximum 1 post on first run
BOT_BOOTSTRAP_COUNT=1 python -m bot.main
```

## Development Notes

- The package entry point lives in `bot/main.py`.
- Dependencies are listed in `requirements.txt` for convenience, but the
  project does not enforce a specific virtual environment manager.
- Tweepy exceptions are logged and skipped so that a failure to post a single
  tweet does not interrupt the polling loop.
