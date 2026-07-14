# Weekly Releases to Telegram

Fetch this week's movie, TV, and animation releases from **TMDB**, covering both
cinema releases and streaming-platform availability, then publish a formatted digest to a
**Telegram channel**.

The project is cross-platform and runs on Windows, macOS, and Linux.

## Features

- Weekly release discovery for movies, series, animated movies, and animated series.
- Cinema and streaming sections powered by TMDB.
- Dynamic streaming-provider lookup by region, so provider IDs are not hard-coded.
- Telegram output as link-preview cards or grouped text.
- Optional trailer buttons using TMDB YouTube videos.
- Optional Rotten Tomatoes scores through OMDb.
- Optional Radarr and Sonarr links for manual add flows.
- Local history file to avoid reposting the same titles week after week.

## Before You Start

You need:

1. **TMDB API key**: create an account on [themoviedb.org](https://www.themoviedb.org), then go to
   *Settings* -> *API* and create a **v3** key. See the
   [TMDB getting started guide](https://developer.themoviedb.org/reference/intro/getting-started).
2. **Telegram bot**: talk to [`@BotFather`](https://t.me/BotFather), run `/newbot`, and copy the bot
   token (`123456:AA...`).
3. **Telegram channel**: create a channel and add the bot as an administrator.
4. **Telegram channel chat ID**:
   - public channel: use `@channel_name`
   - private channel: use the numeric `-100...` ID, which you can get from `getUpdates`
     (`https://api.telegram.org/bot<token>/getUpdates` after posting in the channel) or from a bot
     such as `@userinfobot`.

## Installation

```bash
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate

pip install -r requirements.txt
cp config.example.json config.json
```

Then edit `config.json` with your own API keys and preferences.

Secrets can also be provided through environment variables. Environment variables take precedence
over `config.json`:

- `TMDB_API_KEY`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `OMDB_API_KEY`

Never commit `config.json`.

## Secret Handling

This repository does not contain real credentials. The application reads secrets from `config.json`
(ignored by git) or from environment variables. Only `config.example.json` is committed, with
placeholder values.

- Copy `config.example.json` to `config.json`, then add your own keys.
- Prefer environment variables in CI, containers, or hosted deployments.
- Before pushing, check that `git status` does not list `config.json`.

Secrets are not written to logs.

## Configuration

| Key | Description |
|-----|-------------|
| `tmdb_api_key` | TMDB v3 API key. |
| `telegram_bot_token` | Telegram bot token. |
| `telegram_chat_id` | Telegram channel, either `@channel` or a numeric `-100...` ID. |
| `omdb_api_key` | [OMDb](https://www.omdbapi.com/apikey.aspx) API key used to show Rotten Tomatoes scores. Use `""` to disable. |
| `language` | TMDB language, for example `fr-FR` or `en-US`. |
| `regions` | ISO 3166-1 country codes, for example `["FR", "US"]`. Empty or `["ALL"]` means worldwide. |
| `platforms` | Streaming-platform names. They are resolved dynamically to TMDB provider IDs for each region. |
| `include_cinema` | Include cinema releases. |
| `include_returning_seasons` | `true` includes new seasons such as S2/S3 in addition to brand-new series. Season premieres are detected through `/tv/{id}` and titles are annotated with `- Season N`. `false` keeps only new series starting at S1. |
| `categories` | Subset of `films`, `series`, `animation`, `animation_series`. |
| `min_vote_count` | Minimum TMDB vote count. This is often `0` for very recent releases. |
| `min_popularity` | Minimum TMDB popularity. `0` keeps everything; around `10` filters out much of the noise. Increase it if the digest is too crowded. |
| `max_items_per_section` | Maximum number of titles per section. |
| `max_pages` | Number of TMDB result pages fetched per request. Default: `2`. |
| `week_start_day` | First day of the 7-day window, using Python `weekday()` values: Monday is `0`, Wednesday is `2`, Sunday is `6`. |
| `style` | Message style: `card` sends one message per release with a Telegram link preview; `text` sends grouped text summaries. |
| `trailers` | `true` adds a trailer button using TMDB YouTube videos. This costs one extra TMDB call per title. |
| `cinema_label` | Label for the cinema-showtimes button, for example `Showtimes`, `UGC`, or `My cinema`. |
| `cinema_search_url` | Search URL template for cinema showtimes. `{query}` is replaced with the URL-encoded title. Use `""` to disable. |
| `radarr_url` | Base URL of your Radarr instance, for example `http://localhost:7878`. Adds a manual `Radarr` link to movies. Use `""` to disable. |
| `sonarr_url` | Base URL of your Sonarr instance, for example `http://localhost:8989`. Adds a manual `Sonarr` link to series. Use `""` to disable. |
| `use_history` | `true` stores already-posted titles and avoids reposting them. |
| `history_file` | JSON state file path. Default: `sent_history.json`, ignored by git. |
| `timezone` | Time zone used to compute the weekly window, for example `Europe/Paris`. |

## Usage

```bash
python releases_to_telegram.py --check
python releases_to_telegram.py --dry-run
python releases_to_telegram.py
```

Commands:

| Command | Description |
|---------|-------------|
| `--check` | Validate TMDB and Telegram credentials, then exit. Returns a non-zero exit code if invalid. |
| `--dry-run` | Print the generated digest without sending it to Telegram. |
| no option | Send the digest to the configured Telegram channel. |

Options:

| Option | Description |
|--------|-------------|
| `--config PATH` | Config file path. Default: `./config.json`. |
| `--regions FR,US` | Override configured regions. |
| `--platforms "Netflix,Max"` | Override configured platforms. |
| `--week current\|next\|last` | Target window. Default: `current`. |
| `--text` | Force grouped text mode instead of card mode. |
| `--ignore-history` | Do not filter titles that were already posted. Useful for tests or backfills. |
| `--dry-run` | Print without sending. |
| `--check` | Validate credentials and exit. |
| `--verbose` | Enable detailed logs. |

Exit codes:

- `0`: success
- `1`: configuration or credential error
- `2`: network or API error

## Weekly Window

The release window is a 7-day period anchored on `week_start_day`, in the configured `timezone`.

By default, `week_start_day` is `2`, which means Wednesday through Tuesday. This matches the usual
cinema release day in France. Set `"week_start_day": 0` for a classic Monday-through-Sunday week.

`--week current` selects the window containing today. `--week next` and `--week last` select the
following or previous window.

## Rotten Tomatoes Scores

TMDB does not expose Rotten Tomatoes scores directly. This project uses OMDb as a bridge:

TMDB `external_ids` -> IMDb ID -> OMDb -> Rotten Tomatoes score.

To enable it, get a key from [omdbapi.com](https://www.omdbapi.com/apikey.aspx), then set
`omdb_api_key` or the `OMDB_API_KEY` environment variable.

Limitations:

- Scores only appear when OMDb has them.
- Coverage is better for movies than series.
- Very recent releases often do not have Rotten Tomatoes scores yet.
- OMDb's free tier is limited to 1,000 requests per day.

Without an OMDb key, Rotten Tomatoes scores are simply omitted.

## Radarr and Sonarr Links

Set `radarr_url` and/or `sonarr_url` to add manual add links to Telegram messages.

- Movies: `<radarr_url>/add/new?term=tmdb:<id>`
- Series: `<sonarr_url>/add/new?term=tvdb:<id>`

Sonarr indexes series by TVDB ID, so the TVDB ID is resolved through TMDB `/tv/<id>/external_ids`.
If no TVDB ID is found, the link falls back to a title search.

These links open the prefilled add page in your own instance. They do not add anything
automatically; you still confirm the item yourself.

The URLs must be reachable from the device where you open Telegram. For example, `localhost` only
works if Telegram is opened on the same machine that hosts your Radarr or Sonarr instance.

## Known Limitation: Streaming Dates

TMDB does not expose the exact date when a title was added to a streaming platform.

This project approximates "new on streaming this week" by combining:

- the title's release date or first-air date inside the weekly window
- the title's availability on the configured streaming provider through `with_watch_providers`
  and `flatrate`

This is the best free proxy available through TMDB, but it is still an approximation. Provider IDs
also vary by region, so they are resolved dynamically instead of being hard-coded.

## Scheduling

The script does not schedule itself. Use your operating system's scheduler.

### Cron

```cron
# Every Monday at 08:00
0 8 * * 1 cd /path/to/imdb-release-discovery && /usr/bin/python3 releases_to_telegram.py >> run.log 2>&1
```

## Tests

```bash
python -m pytest -q
```

The test suite does not require network access. It covers date windows, deduplication, formatting,
history handling, enrichment, and CLI behavior.

## Project Structure

```text
releases_to_telegram.py   CLI entry point, date window, collection flow
tmdb.py                   TMDB client, requests, retries, parsing
omdb.py                   OMDb client for Rotten Tomatoes scores
telegram_client.py        Telegram sending client with retries and 429 handling
formatter.py              HTML/text formatting and Telegram message splitting
history.py                Local sent-history persistence
tests/                    Offline unit tests
```

## License

See [LICENSE](LICENSE).
