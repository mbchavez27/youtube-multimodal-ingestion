#!/usr/bin/env python3
"""Scrape all YouTube video comments and export as CSV."""

import csv
import logging
import re
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

LOGGER = logging.getLogger("comments_ingest")


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


def main() -> int:
    print("YouTube Comment Ingest Tool")
    print("━" * 50)

    url_input = input("Enter YouTube URL or video ID: ").strip()
    url = normalize_url(url_input)
    video_id = extract_video_id(url)

    if not video_id:
        print("Error: Invalid YouTube URL or video ID")
        return 1

    print(f"\nProcessing: {url}")
    print("━" * 50)

    verbose = prompt_yes_no("Verbose logging (debug)?", False)
    setup_logging(verbose)

    client = YtDlpClient()
    scraper = VideoScraper(client)

    print("\nFetching video metadata...")
    video = scraper.scrape_video(
        channel_id="",
        video_url=url,
        window_label=None,
        anchor_id=None,
    )
    if not video:
        print("Error: Could not fetch video information")
        return 1

    print(f"Title: {video.title}")
    print(f"Channel: {video.channel_id}")
    print(f"Duration: {video.duration_seconds}s")
    print(f"Views: {video.view_count or 'N/A'}")
    print(f"Comments (reported): {video.comment_count or 'N/A'}\n")

    print("Fetching all comments (this may take a while)...")
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
        print(f"Fetched {len(comments)} comments")
    except Exception as e:
        LOGGER.warning("Failed to scrape comments: %s", e)
    finally:
        import shutil
        try:
            shutil.rmtree(tmp_dir)
        except OSError:
            pass

    if not comments:
        print("No comments found")
        return 1

    output_path = repo_root / "outputs" / "comments" / f"{video_id}_comments.csv"
    write_csv(comments, video, output_path)
    print(f"\nDone! Saved {len(comments)} comments to {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
