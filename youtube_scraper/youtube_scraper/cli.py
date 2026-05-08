from __future__ import annotations

import argparse
import csv
import io
import logging
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .scraper.comment_scraper import CommentScraper
from .scraper.video_scraper import VideoScraper
from .scraper.yt_dlp_client import YtDlpClient
from .storage.jsonl_writer import JsonlWriter

LOGGER = logging.getLogger("youtube_scraper")


@dataclass(slots=True)
class RunConfig:
    manual_csv: str
    output_path: str
    max_comments_per_video: int
    cache_dir: str | None
    comments_dir: str
    person: str


def parse_args(argv: list[str]) -> RunConfig:
    parser = argparse.ArgumentParser(
        description="Scrape YouTube videos from a manual CSV list"
    )
    parser.add_argument(
        "--manual-csv",
        required=True,
        help="CSV with columns window,video (pre/cancel/post)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSONL path",
    )
    parser.add_argument("--max-comments-per-video", type=int, default=1000)
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Optional yt-dlp cache directory",
    )
    parser.add_argument(
        "--comments-dir",
        default=None,
        help="Directory to store yt-dlp comment dumps",
    )
    parser.add_argument("--log-level", default="INFO")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    manual_csv = str(args.manual_csv)
    person = Path(manual_csv).stem
    default_output = f"outputs/{person}/{person}_youtube.jsonl"
    default_comments = f"outputs/{person}/comments"
    output_path = _resolve_under_root(str(args.out) if args.out else default_output)
    comments_dir = _resolve_under_root(
        str(args.comments_dir) if args.comments_dir else default_comments
    )
    cache_dir = str(args.cache_dir) if args.cache_dir else None

    return RunConfig(
        manual_csv=manual_csv,
        output_path=output_path,
        max_comments_per_video=int(args.max_comments_per_video),
        cache_dir=cache_dir,
        comments_dir=comments_dir,
        person=person,
    )


def run(config: RunConfig) -> int:
    run_id = uuid.uuid4().hex
    scraped_at = datetime.now(timezone.utc).isoformat()
    Path(config.output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(config.comments_dir).mkdir(parents=True, exist_ok=True)

    client = YtDlpClient(cache_dir=config.cache_dir)
    video_scraper = VideoScraper(client)
    comment_scraper = CommentScraper(
        client, max_comments_per_video=config.max_comments_per_video
    )
    writer = JsonlWriter(config.output_path)

    LOGGER.info("Starting run_id=%s person=%s", run_id, config.person)
    LOGGER.info("Output path=%s", config.output_path)
    LOGGER.info("Comments dir=%s", config.comments_dir)

    manual_groups = _load_manual_csv(config.manual_csv)
    total_videos = 0
    total_comments = 0
    failures = 0

    total_planned = sum(len(items) for items in manual_groups.values())
    LOGGER.info(
        "Loaded manual videos pre=%d cancel=%d post=%d total=%d",
        len(manual_groups["pre"]),
        len(manual_groups["cancel"]),
        len(manual_groups["post"]),
        total_planned,
    )

    for window_label in ("pre", "cancel", "post"):
        selected = manual_groups[window_label]
        total_selected = len(selected)
        LOGGER.info("Window %s selected=%d", window_label, total_selected)
        for index, video_url in enumerate(selected, start=1):
            remaining = max(total_selected - index, 0)
            output_basename = f"{window_label}-{index}"
            LOGGER.info(
                "Scraping video %d/%d window=%s remaining=%d",
                index,
                total_selected,
                window_label,
                remaining,
            )
            try:
                video = video_scraper.scrape_video(
                    config.person,
                    video_url,
                    window_label=window_label,
                    anchor_id=None,
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Video scrape failed %s: %s", video_url, exc)
                failures += 1
                continue

            if writer.write(video.to_record(run_id=run_id, scraped_at=scraped_at)):
                total_videos += 1

            try:
                comments = comment_scraper.scrape_comments(
                    config.person,
                    video.video_id,
                    video.video_url,
                    window_label=window_label,
                    anchor_id=None,
                    output_basename=output_basename,
                    output_dir=config.comments_dir,
                )
                for comment in comments:
                    if writer.write(
                        comment.to_record(run_id=run_id, scraped_at=scraped_at)
                    ):
                        total_comments += 1
                LOGGER.info(
                    "Comments scraped video=%s count=%d",
                    video.video_id,
                    len(comments),
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Comments scrape failed %s: %s", video.video_id, exc)
                failures += 1

    writer.close()
    LOGGER.info(
        "Done run_id=%s videos=%d comments=%d failures=%d output=%s",
        run_id,
        total_videos,
        total_comments,
        failures,
        config.output_path,
    )
    return 0 if total_videos > 0 else 1


def _resolve_under_root(path_value: str) -> str:
    candidate = Path(path_value).expanduser()
    if candidate.is_absolute():
        return str(candidate)
    repo_root = Path(__file__).resolve().parents[3]
    return str(repo_root / candidate)


def _load_manual_csv(path_value: str) -> dict[str, list[str]]:
    path = Path(path_value).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")
    rows: dict[str, list[str]] = {"pre": [], "cancel": [], "post": []}
    with path.open("r", encoding="utf-8", newline="") as handle:
        # Some exports include leading blank lines, a UTF-8 BOM, or use a
        # delimiter other than comma. Normalize those cases here.
        header_line: str | None = None
        header_line_no = 0
        while True:
            line = handle.readline()
            if line == "":
                break
            header_line_no += 1
            if line.strip():
                header_line = line
                break

        if not header_line:
            raise ValueError("CSV must include headers: window,video")

        remainder = handle.read()
        combined = header_line + remainder
        try:
            dialect = csv.Sniffer().sniff(combined[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(io.StringIO(combined), dialect)

        raw_headers = next(reader, [])
        headers = [str(h).strip().lstrip("\ufeff").lower() for h in raw_headers]
        try:
            window_index = headers.index("window")
            video_index = headers.index("video")
        except ValueError as exc:
            raise ValueError("CSV must include headers: window,video") from exc

        for row_offset, row in enumerate(reader, start=1):
            row_index = header_line_no + row_offset
            if not row:
                continue
            window = (
                str(row[window_index]).strip().lower()
                if window_index < len(row)
                else ""
            )
            video_value = (
                str(row[video_index]).strip() if video_index < len(row) else ""
            )
            if not window or not video_value:
                LOGGER.warning("Skipping row %d missing window/video", row_index)
                continue
            if window not in rows:
                raise ValueError(
                    f"Invalid window '{window}' at row {row_index}; expected pre/cancel/post"
                )
            rows[window].append(_normalize_video_url(video_value))
    for key in rows:
        rows[key] = _dedupe_preserve_order(rows[key])
    return rows


def _normalize_video_url(value: str) -> str:
    text = value.strip()
    if not text:
        return text
    if text.startswith("http"):
        return text
    if re.match(r"^[A-Za-z0-9_-]{6,}$", text):
        return f"https://www.youtube.com/watch?v={text}"
    return text


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def main() -> int:
    config = parse_args(sys.argv[1:])
    return run(config)


if __name__ == "__main__":
    raise SystemExit(main())
