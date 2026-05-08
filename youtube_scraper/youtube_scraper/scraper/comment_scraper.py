from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..models import CommentItem
from .yt_dlp_client import YtDlpClient

LOGGER = logging.getLogger("youtube_scraper.comments")


class CommentScraper:
    def __init__(self, client: YtDlpClient, *, max_comments_per_video: int) -> None:
        self.client = client
        self.max_comments_per_video = (
            max_comments_per_video if max_comments_per_video > 0 else 10**9
        )

    def scrape_comments(
        self,
        channel_id: str,
        video_id: str,
        video_url: str,
        *,
        window_label: str | None = None,
        anchor_id: str | None = None,
        output_basename: str | None = None,
        output_dir: str,
    ) -> list[CommentItem]:
        LOGGER.info("Scraping comments for video_id=%s url=%s", video_id, video_url)
        comments_file = self.client.dump_comments(
            video_url,
            output_dir=output_dir,
            output_basename=output_basename,
            video_id=video_id,
        )
        if not comments_file:
            LOGGER.warning("No comments file created for video_id=%s", video_id)
            return []
        LOGGER.debug("Comments file found: %s", comments_file)
        return _parse_comments_file(
            comments_file,
            channel_id=channel_id,
            video_id=video_id,
            max_comments=self.max_comments_per_video,
            window_label=window_label,
            anchor_id=anchor_id,
        )


def _parse_comments_file(
    path: Path,
    *,
    channel_id: str,
    video_id: str,
    max_comments: int,
    window_label: str | None,
    anchor_id: str | None,
) -> list[CommentItem]:
    raw = _read_json_payload(path)
    LOGGER.debug("Parsing comments file: %s, type=%s", path.name, type(raw).__name__)

    if isinstance(raw, list):
        comments = raw
        LOGGER.debug("Detected list format (extracted from info.json)")
    elif isinstance(raw, dict):
        comments = raw.get("comments")
        if isinstance(comments, list):
            LOGGER.debug("Detected dict format with 'comments' key")
        else:
            top_keys = list(raw.keys())
            LOGGER.warning("Comments not a list. Top-level keys: %s", top_keys)
            if "comments" not in raw:
                LOGGER.warning("'comments' key not found in JSON. Available keys: %s", top_keys)
            elif not isinstance(comments, list):
                LOGGER.warning("'comments' exists but is type %s, not list", type(comments))
            return []
    else:
        LOGGER.warning("Raw JSON is not a dict or list, type: %s", type(raw))
        return []

    if not isinstance(comments, list):
        LOGGER.warning("Comments is not a list, type: %s", type(comments))
        return []

    LOGGER.info("Found %d comments in file (max: %d)", len(comments), max_comments)
    out: list[CommentItem] = []
    for entry in comments:
        if not isinstance(entry, dict):
            continue
        text = _as_str(entry.get("text"))
        if not text:
            continue
        comment_id = str(entry.get("id") or "")
        if not comment_id:
            continue
        out.append(
            CommentItem(
                channel_id=channel_id,
                video_id=video_id,
                comment_id=comment_id,
                text=text,
                user_handle=_as_str(entry.get("author")),
                user_display_name=_as_str(entry.get("author")),
                like_count=_as_int(entry.get("like_count")),
                reply_count=_as_int(entry.get("reply_count")),
                create_time=_parse_timestamp(entry.get("timestamp")),
                is_reply=bool(entry.get("is_reply"))
                if entry.get("is_reply") is not None
                else False,
                parent_comment_id=_as_str(entry.get("parent")),
                raw=entry,
                window_label=window_label,
                anchor_id=anchor_id,
            )
        )
        if len(out) >= max_comments:
            break
    return out


def _read_json_payload(path: Path) -> dict[str, Any]:
    content = path.read_text(encoding="utf-8").strip()
    if not content:
        return {}
    if content[0] == "{":
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            return {}
    first_line = content.splitlines()[0].strip()
    try:
        return json.loads(first_line)
    except json.JSONDecodeError:
        return {}


def _parse_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        timestamp = float(value)
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def _as_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
