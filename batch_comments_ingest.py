#!/usr/bin/env python3
"""Scrape all YouTube video comments for every URL in a CSV file."""

import csv
import io
import logging
import re
import shutil
import sys
from pathlib import Path

repo_root = Path(__file__).parent
venv_bin = repo_root / ".venv" / "bin"
if venv_bin.exists():
    import os
    current_path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{venv_bin}{os.pathsep}{current_path}"

sys.path.insert(0, str(repo_root / "youtube_scraper"))

from youtube_scraper.scraper.comment_scraper import CommentScraper
from youtube_scraper.scraper.video_scraper import VideoScraper
from youtube_scraper.scraper.yt_dlp_client import YtDlpClient

LOGGER = logging.getLogger("batch_comments_ingest")


def normalize_url(url: str) -> str:
    url = url.strip()
    if re.match(r"^[A-Za-z0-9_-]{6,}$", url):
        return f"https://www.youtube.com/watch?v={url}"
    if url.startswith(("http://", "https://")):
        return url
    return f"https://www.youtube.com/watch?v={url}"


def extract_video_id(url: str) -> str | None:
    patterns = [
        r"(?:v=|/v/|youtu\.be/)([A-Za-z0-9_-]{6,})",
        r"embed/([A-Za-z0-9_-]{6,})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def prompt_yes_no(prompt: str, default: bool = False) -> bool:
    default_str = "Y" if default else "N"
    user_input = input(f"{prompt} [{default_str}]: ").strip().lower()
    if not user_input:
        return default
    return user_input in ("y", "yes")


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(message)s",
    )


CSV_FIELDS = [
    "video_id",
    "video_title",
    "channel_id",
    "comment_id",
    "text",
    "user_handle",
    "user_display_name",
    "like_count",
    "reply_count",
    "create_time",
    "is_reply",
    "parent_comment_id",
]


def write_csv(comments, video, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for c in comments:
            writer.writerow({
                "video_id": video.video_id,
                "video_title": video.title or "",
                "channel_id": video.channel_id or "",
                "comment_id": c.comment_id,
                "text": c.text,
                "user_handle": c.user_handle or "",
                "user_display_name": c.user_display_name or "",
                "like_count": c.like_count if c.like_count is not None else "",
                "reply_count": c.reply_count if c.reply_count is not None else "",
                "create_time": c.create_time.isoformat() if c.create_time else "",
                "is_reply": "yes" if c.is_reply else "no",
                "parent_comment_id": c.parent_comment_id or "",
            })


def load_urls_from_csv(path: Path, column_hint: str | None = None) -> list[str]:
    if not path.exists():
        print(f"Error: File not found: {path}")
        sys.exit(1)

    with path.open("r", encoding="utf-8", newline="") as handle:
        header_line: str | None = None
        while True:
            line = handle.readline()
            if line == "":
                break
            if line.strip():
                header_line = line
                break

        if not header_line:
            print("Error: CSV file is empty")
            sys.exit(1)

        remainder = handle.read()
        combined = header_line + remainder

        try:
            dialect = csv.Sniffer().sniff(combined[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel

        reader = csv.reader(io.StringIO(combined), dialect)
        raw_headers = next(reader, [])
        headers = [str(h).strip().lstrip("\ufeff").lower() for h in raw_headers]

    rows = list(reader)

    # Determine which column to use
    col_index: int | None = None
    if column_hint:
        lower_hint = column_hint.strip().lower()
        try:
            col_index = headers.index(lower_hint)
        except ValueError:
            print(f"Column '{column_hint}' not found. Available columns: {', '.join(headers)}")
            sys.exit(1)
    else:
        # Auto-detect: look for known column names first
        known_names = ["video", "url", "link", "youtube_url", "video_url"]
        for name in known_names:
            try:
                col_index = headers.index(name)
                print(f"Auto-detected column: '{headers[col_index]}'")
                break
            except ValueError:
                continue

    urls: list[str] = []
    # If we found a named column, use it
    if col_index is not None:
        for row in rows:
            if col_index < len(row):
                val = str(row[col_index]).strip()
                if val:
                    urls.append(val)
    else:
        # Fall back: scan ALL cells for YouTube URL/ID patterns
        print("No known column found — scanning all cells for YouTube links...")
        seen: set[str] = set()
        url_pattern = re.compile(
            r"(?:https?://)?(?:www\.)?(?:youtube\.com|youtu\.be)/\S+"
        )
        id_pattern = re.compile(r"^[A-Za-z0-9_-]{6,}$")
        for row in rows:
            for cell in row:
                val = str(cell).strip()
                if not val or val in seen:
                    continue
                match = url_pattern.search(val)
                if match:
                    seen.add(val)
                    urls.append(match.group(0))
                elif id_pattern.match(val):
                    seen.add(val)
                    urls.append(val)

    if not urls:
        print("Error: No YouTube URLs or video IDs found in CSV")
        sys.exit(1)

    # Deduplicate and normalize
    normalized: list[str] = []
    seen_normalized: set[str] = set()
    for u in urls:
        nu = normalize_url(u)
        if nu not in seen_normalized:
            seen_normalized.add(nu)
            normalized.append(nu)

    return normalized


def process_video(
    url: str,
    output_dir: Path,
) -> tuple[str, int]:
    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError(f"Could not extract video ID from: {url}")

    print(f"\n  URL: {url}")
    print(f"  ID:  {video_id}")

    client = YtDlpClient()
    scraper = VideoScraper(client)

    print("  Fetching video metadata...")
    video = scraper.scrape_video(
        channel_id="",
        video_url=url,
        window_label=None,
        anchor_id=None,
    )
    if not video:
        raise RuntimeError("Could not fetch video information")

    print(f"  Title:   {video.title}")
    print(f"  Channel: {video.channel_id}")
    print(f"  Comments (reported): {video.comment_count or 'N/A'}")

    print("  Fetching all comments (this may take a while)...")
    comment_client = YtDlpClient()
    comment_scraper = CommentScraper(comment_client, max_comments_per_video=0)

    tmp_dir = Path(f".tmp_comments_{video_id}")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    comments = []
    try:
        comments = comment_scraper.scrape_comments(
            channel_id=video.channel_id or "",
            video_id=video.video_id,
            video_url=url,
            window_label=None,
            anchor_id=None,
            output_basename="comments",
            output_dir=str(tmp_dir),
            extractor_args=["--extractor-args", "youtube:max_comments=all"],
        )
    except Exception as e:
        LOGGER.warning("Failed to scrape comments: %s", e)
    finally:
        try:
            shutil.rmtree(tmp_dir)
        except OSError:
            pass

    if comments:
        output_path = output_dir / f"{video_id}_comments.csv"
        write_csv(comments, video, output_path)
        print(f"  Saved {len(comments)} comments → {output_path}")
    else:
        print("  No comments found — skipping output")
        return video_id, 0

    return video_id, len(comments)


def main() -> int:
    print("YouTube Batch Comment Ingest Tool")
    print("━" * 60)

    csv_input = input("Enter path to CSV file: ").strip()
    csv_path = Path(csv_input).expanduser()

    column_hint = input(
        "Enter column name (or press Enter to auto-detect): "
    ).strip() or None

    verbose = prompt_yes_no("Verbose logging (debug)?", False)
    setup_logging(verbose)

    folder_input = input(
        "Enter output folder name (or press Enter for 'batch_output'): "
    ).strip() or "batch_output"
    if "/" in folder_input or folder_input.startswith(("~", ".")):
        output_dir = Path(folder_input).expanduser().resolve()
    else:
        output_dir = repo_root / "outputs" / folder_input

    print("\nLoading URLs from CSV...")
    urls = load_urls_from_csv(csv_path, column_hint)
    print(f"Found {len(urls)} unique video URL(s)")

    output_dir.mkdir(parents=True, exist_ok=True)

    print("━" * 60)
    total_videos = 0
    total_comments = 0
    failures = 0

    for i, url in enumerate(urls, start=1):
        remaining = len(urls) - i
        print(f"\n[{i}/{len(urls)}] — {remaining} remaining")
        print("─" * 40)
        try:
            vid_id, count = process_video(url, output_dir)
            total_videos += 1
            total_comments += count
        except Exception as e:
            LOGGER.warning("Failed processing %s: %s", url, e)
            failures += 1

    print("\n" + "━" * 60)
    print(
        f"Batch complete: {total_videos} videos processed, "
        f"{total_comments} total comments, {failures} failure(s)"
    )
    print("━" * 60)

    return 0 if total_videos > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
