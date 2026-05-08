from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ALLOWED_WINDOW_LABELS = {"pre", "post", "cancel"}


class JsonlWriter:
    def __init__(self, output_path: str) -> None:
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: dict[str, set[str]] = {
            "video": set(),
            "comment": set(),
            "transcript": set(),
        }
        self._load_seen()
        self._fh = self.output_path.open("a", encoding="utf-8")

    def _load_seen(self) -> None:
        if not self.output_path.exists():
            return
        try:
            with self.output_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    record_type = record.get("record_type")
                    key = self._key_for(record)
                    if record_type in self._seen and key is not None:
                        self._seen[record_type].add(key)
        except OSError:
            return

    def close(self) -> None:
        self._fh.close()

    def write(self, record: dict[str, Any]) -> bool:
        record_type = record.get("record_type")
        if record_type == "comment":
            window_label = record.get("window_label")
            if window_label not in ALLOWED_WINDOW_LABELS:
                return False
        key = self._key_for(record)
        if record_type in self._seen and key is not None:
            if key in self._seen[record_type]:
                return False
            self._seen[record_type].add(key)
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()
        return True

    @staticmethod
    def _key_for(record: dict[str, Any]) -> str | None:
        record_type = record.get("record_type")
        if record_type == "video":
            video_id = record.get("video_id")
            return str(video_id) if video_id is not None else None
        if record_type == "comment":
            video_id = record.get("video_id")
            comment_id = record.get("comment_id")
            if video_id is None or comment_id is None:
                return None
            return f"{video_id}::{comment_id}"
        if record_type == "transcript":
            video_id = record.get("video_id")
            return str(video_id) if video_id is not None else None
        return None
