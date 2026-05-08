# YouTube Scraper (Manual List)

This module is a standalone YouTube collection pipeline under `01-collection/youtube_scraper`.
It now runs from a manual CSV list of videos (pre/cancel/post) that you provide.

## What it collects

- Video metadata for each provided URL/ID
- Comment collection per video (via `yt-dlp --write-comments`)
- JSONL output for analytics pipelines

## Project layout

```text
01-collection/youtube_scraper/
  README.md
  pyproject.toml
  youtube_scraper/
    cli.py
    models.py
    filters/
      timeframe.py
    scraper/
      yt_dlp_client.py
      channel_scraper.py
      video_scraper.py
      comment_scraper.py
    storage/
      jsonl_writer.py
```

## Install (module-local uv)

From repository root:

```bash
uv venv 01-collection/youtube_scraper/.venv
uv sync --directory 01-collection/youtube_scraper
uv run --directory 01-collection/youtube_scraper python -m youtube_scraper.cli --help
```

## Quick start

From repository root:

```bash
uv run --directory 01-collection/youtube_scraper python -m youtube_scraper.cli \
  --manual-csv data/input/gdragon.csv
```

The output will be created under `data/<person>/` where `<person>` is the CSV filename stem.
For example `data/gdragon.csv` writes to:

- `data/gdragon/gdragon_youtube.jsonl`
- `data/gdragon/comments/`

## CSV format

Required headers: `window,video`

Example:

```text
window,video
pre,https://www.youtube.com/watch?v=AAA
cancel,BBB123
post,https://www.youtube.com/watch?v=CCC
```

Sample file name: `data/input/gdragon.csv`

```text
window,video
pre,https://www.youtube.com/watch?v=J6Kp5l2xF5E
pre,5LmBWb8zWfI
cancel,https://www.youtube.com/watch?v=6UeLUY3h3s0
post,8T7J6f0WzH8
post,https://www.youtube.com/watch?v=4tBnF46ybZk
```

Valid `window` values: `pre`, `cancel`, `post`.

The `video` column accepts full YouTube URLs or raw video IDs. IDs are normalized to
`https://www.youtube.com/watch?v=<ID>`.

## CLI options

- `--manual-csv` CSV path with `window,video` columns
- `--out` output JSONL path (default `data/<person>/<person>_youtube.jsonl`)
- `--max-comments-per-video` default `1000`, use `0` for unbounded mode
- `--comments-dir` directory to store comment dump files (default `data/<person>/comments`)
- `--cache-dir` optional `yt-dlp` cache directory

## Output format (JSONL)

Each line is one JSON record with `record_type`:

- `video`
- `comment`
- `transcript` (reserved for future use)

Common fields:

- `platform` (`youtube`)
- `run_id`
- `scraped_at`
- `source_account`

Additional window fields:

- `window_label` (`pre`, `cancel`, `post`)
-- `anchor_id` (reserved; not used in manual mode)

Comment dump files are named `pre-1`, `cancel-1`, `post-1`, etc. per window group
in the CSV order, preserving yt-dlp extensions (`.comments.json` or `.comments.jsonl`).

## Notes and reliability

- Comment availability depends on `yt-dlp` support for the target video; if comments are
  restricted or unavailable, the scraper will still emit video metadata and continue.
- For large crawls, expect longer runtimes and occasional partial comment dumps.

## Legal and ethical usage

Use this tool responsibly and in compliance with YouTube terms, local law, and your
institution's research ethics requirements. Only collect data you are authorized to
collect and store.
