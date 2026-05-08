from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..models import VideoItem
from .yt_dlp_client import YtDlpClient


class VideoScraper:
    def __init__(self, client: YtDlpClient) -> None:
        self.client = client

    def scrape_video(
        self,
        channel_id: str,
        video_url: str,
        *,
        window_label: str | None = None,
        anchor_id: str | None = None,
    ) -> VideoItem:
        payload = self.client.dump_json(video_url)
        return _to_video_item(
            payload,
            channel_id=channel_id,
            video_url=video_url,
            window_label=window_label,
            anchor_id=anchor_id,
        )


def _to_video_item(
    payload: dict[str, Any],
    *,
    channel_id: str,
    video_url: str,
    window_label: str | None,
    anchor_id: str | None,
) -> VideoItem:
    upload_date = _parse_upload_date(payload.get("upload_date"))
    return VideoItem(
        channel_id=channel_id,
        video_id=str(payload.get("id") or ""),
        video_url=video_url,
        title=_as_str(payload.get("title")),
        description=_as_str(payload.get("description")),
        create_time=upload_date,
        view_count=_as_int(payload.get("view_count")),
        like_count=_as_int(payload.get("like_count")),
        comment_count=_as_int(payload.get("comment_count")),
        duration_seconds=_as_int(payload.get("duration")),
        tags=_as_list(payload.get("tags")),
        is_live=_as_bool(payload.get("is_live")),
        raw=payload,
        window_label=window_label,
        anchor_id=anchor_id,
    )


def _parse_upload_date(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value)
    try:
        parsed = datetime.strptime(text, "%Y%m%d")
        return parsed.replace(tzinfo=timezone.utc)
    except ValueError:
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


def _as_list(value: object) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    return [str(value)]


def _as_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "false"}:
            return lowered == "true"
    return None
