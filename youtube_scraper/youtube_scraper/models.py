from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class AccountSnapshot:
    channel_id: str
    channel_handle: str | None = None
    display_name: str | None = None
    description: str | None = None
    followers: int | None = None
    total_views: int | None = None
    video_count: int | None = None
    scrape_url: str | None = None

    def to_record(self, *, run_id: str, scraped_at: str) -> dict[str, Any]:
        return {
            "platform": "youtube",
            "record_type": "account",
            "run_id": run_id,
            "scraped_at": scraped_at,
            "source_account": self.channel_handle or self.channel_id,
            "channel_id": self.channel_id,
            "username": self.channel_handle,
            "display_name": self.display_name,
            "biography": self.description,
            "followers": self.followers,
            "total_views": self.total_views,
            "post_count": self.video_count,
            "scrape_url": self.scrape_url,
        }


@dataclass(slots=True)
class VideoItem:
    channel_id: str
    video_id: str
    video_url: str
    title: str | None = None
    description: str | None = None
    create_time: datetime | None = None
    view_count: int | None = None
    like_count: int | None = None
    comment_count: int | None = None
    duration_seconds: int | None = None
    tags: list[str] = field(default_factory=list)
    is_live: bool | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    window_label: str | None = None
    anchor_id: str | None = None

    def to_record(self, *, run_id: str, scraped_at: str) -> dict[str, Any]:
        return {
            "platform": "youtube",
            "record_type": "video",
            "run_id": run_id,
            "scraped_at": scraped_at,
            "source_account": self.channel_id,
            "video_id": self.video_id,
            "video_url": self.video_url,
            "title": self.title,
            "description": self.description,
            "create_time": self.create_time.isoformat() if self.create_time else None,
            "view_count": self.view_count,
            "like_count": self.like_count,
            "comment_count": self.comment_count,
            "duration_seconds": self.duration_seconds,
            "hashtags": _hashtags_from_tags(self.tags),
            "mentions": _mentions_from_text(self.description),
            "is_live": self.is_live,
            "window_label": self.window_label,
            "anchor_id": self.anchor_id,
            "raw": self.raw,
        }


@dataclass(slots=True)
class CommentItem:
    channel_id: str
    video_id: str
    comment_id: str
    text: str
    user_handle: str | None = None
    user_display_name: str | None = None
    like_count: int | None = None
    reply_count: int | None = None
    create_time: datetime | None = None
    is_reply: bool = False
    parent_comment_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    window_label: str | None = None
    anchor_id: str | None = None

    def to_record(self, *, run_id: str, scraped_at: str) -> dict[str, Any]:
        return {
            "platform": "youtube",
            "record_type": "comment",
            "run_id": run_id,
            "scraped_at": scraped_at,
            "source_account": self.channel_id,
            "video_id": self.video_id,
            "comment_id": self.comment_id,
            "text": self.text,
            "user_handle": self.user_handle,
            "user_display_name": self.user_display_name,
            "like_count": self.like_count,
            "reply_count": self.reply_count,
            "create_time": self.create_time.isoformat() if self.create_time else None,
            "is_reply": self.is_reply,
            "parent_comment_id": self.parent_comment_id,
            "window_label": self.window_label,
            "anchor_id": self.anchor_id,
            "raw": self.raw,
        }


@dataclass(slots=True)
class TranscriptItem:
    channel_id: str
    video_id: str
    video_url: str | None
    language: str | None
    text: str
    segments: list[dict[str, Any]] = field(default_factory=list)
    window_label: str | None = None
    anchor_id: str | None = None

    def to_record(self, *, run_id: str, scraped_at: str) -> dict[str, Any]:
        return {
            "platform": "youtube",
            "record_type": "transcript",
            "run_id": run_id,
            "scraped_at": scraped_at,
            "source_account": self.channel_id,
            "video_id": self.video_id,
            "video_url": self.video_url,
            "language": self.language,
            "text": self.text,
            "segments": self.segments,
            "window_label": self.window_label,
            "anchor_id": self.anchor_id,
        }


def _hashtags_from_tags(tags: list[str]) -> list[str]:
    return [t.lstrip("#") for t in tags if isinstance(t, str) and t.strip()]


def _mentions_from_text(text: str | None) -> list[str]:
    if not text:
        return []
    out = []
    for token in text.split():
        if token.startswith("@") and len(token) > 1:
            out.append(token[1:])
    return out
