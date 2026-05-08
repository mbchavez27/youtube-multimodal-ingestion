from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger("youtube_scraper")


class YtDlpClient:
    def __init__(self, *, cache_dir: str | None = None) -> None:
        self.cache_dir = cache_dir

    def dump_json(self, url: str) -> dict[str, Any]:
        LOGGER.info("yt-dlp metadata start url=%s", url)
        args = ["yt-dlp", "--dump-json", "--no-playlist", url]
        if self.cache_dir:
            args.extend(["--cache-dir", self.cache_dir])
        payload = self._run(args)
        LOGGER.info("yt-dlp metadata done url=%s", url)
        return self._parse_json(payload)

    def dump_json_entries(
        self,
        url: str,
        *,
        flat: bool = True,
        playlist_end: int = 200,
    ) -> list[dict[str, Any]]:
        LOGGER.info("yt-dlp list start url=%s", url)
        args = [
            "yt-dlp",
            "--dump-json",
            "--playlist-end",
            str(max(1, int(playlist_end))),
        ]
        if flat:
            args.append("--flat-playlist")
        args.append(url)
        if self.cache_dir:
            args.extend(["--cache-dir", self.cache_dir])
        total = max(1, int(playlist_end))
        items = list(self._iter_json_entries(args, total_hint=total))
        LOGGER.info("yt-dlp list done url=%s items=%d", url, len(items))
        return items

    def dump_comments(
        self,
        url: str,
        *,
        output_dir: str,
        output_basename: str | None = None,
        video_id: str | None = None,
    ) -> Path | None:
        LOGGER.info("yt-dlp comments start url=%s", url)
        out_dir = Path(output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        output_template = out_dir / "%(id)s.%(ext)s"
        args = [
            "yt-dlp",
            "--write-comments",
            "--skip-download",
            "--no-playlist",
            "-o",
            str(output_template),
            url,
        ]
        if self.cache_dir:
            args.extend(["--cache-dir", self.cache_dir])
        self._run(args)
        comments_file = self._find_comments_file(out_dir)
        if output_basename:
            comments_file = self._rename_comment_outputs(
                out_dir,
                output_basename=output_basename,
                video_id=video_id,
                fallback=comments_file,
            )
        LOGGER.info(
            "yt-dlp comments done url=%s file=%s",
            url,
            comments_file,
        )
        return comments_file

    @staticmethod
    def _parse_json(payload: str) -> dict[str, Any]:
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _iter_json_entries(
        args: list[str], *, total_hint: int | None = None
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        processed = 0
        for line in process.stdout:
            if not line.strip():
                continue
            items.append(YtDlpClient._parse_json(line))
            processed += 1
            if total_hint is not None:
                remaining = max(total_hint - processed, 0)
                LOGGER.info(
                    "Listing uploads %d/%d remaining=%d",
                    processed,
                    total_hint,
                    remaining,
                )
            else:
                LOGGER.info("Listing uploads processed=%d", processed)
        stderr = process.stderr.read()
        returncode = process.wait()
        if returncode != 0:
            raise subprocess.CalledProcessError(
                returncode,
                args,
                output="",
                stderr=stderr,
            )
        return items

    @staticmethod
    def _find_comments_file(out_dir: Path) -> Path | None:
        candidates = list(out_dir.glob("*.comments.json"))
        if not candidates:
            candidates = list(out_dir.glob("*.comments.jsonl"))
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.stat().st_mtime)

    @staticmethod
    def _rename_comment_outputs(
        out_dir: Path,
        *,
        output_basename: str,
        video_id: str | None,
        fallback: Path | None,
    ) -> Path | None:
        comments_file = None
        if video_id:
            preferred = out_dir / f"{video_id}.comments.json"
            if preferred.exists():
                comments_file = preferred
            else:
                preferred = out_dir / f"{video_id}.comments.jsonl"
                if preferred.exists():
                    comments_file = preferred
        if comments_file is None:
            comments_file = fallback
        if comments_file is not None:
            suffix = None
            name = comments_file.name
            if name.endswith(".comments.jsonl"):
                suffix = ".comments.jsonl"
            elif name.endswith(".comments.json"):
                suffix = ".comments.json"
            if suffix:
                target = out_dir / f"{output_basename}{suffix}"
                if target.exists():
                    LOGGER.warning("Skipping rename; target exists %s", target)
                else:
                    comments_file = comments_file.rename(target)
        if video_id:
            info_file = out_dir / f"{video_id}.info.json"
            if info_file.exists():
                info_target = out_dir / f"{output_basename}.info.json"
                if info_target.exists():
                    LOGGER.warning("Skipping rename; target exists %s", info_target)
                else:
                    info_file.rename(info_target)
        return comments_file

    @staticmethod
    def _run(args: list[str]) -> str:
        result = subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
