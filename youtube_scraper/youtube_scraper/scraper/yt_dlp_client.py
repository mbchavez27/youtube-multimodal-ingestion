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

        comments_file = self._dump_comments_primary(
            url, out_dir, output_template, video_id
        )

        if not comments_file:
            LOGGER.info("Primary method failed, trying fallback methods...")
            comments_file = self._dump_comments_fallback(
                url, out_dir, output_template, video_id
            )

        if output_basename and comments_file:
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

    def _dump_comments_primary(
        self,
        url: str,
        out_dir: Path,
        output_template: Path,
        video_id: str | None,
    ) -> Path | None:
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
        return self._run_and_find_comments(args, out_dir, video_id)

    def _dump_comments_fallback(
        self,
        url: str,
        out_dir: Path,
        output_template: Path,
        video_id: str | None,
    ) -> Path | None:
        methods = [
            ["yt-dlp", "--write-comments", "--skip-download", "--no-playlist", "-o", str(output_template), url],
            ["yt-dlp", "--extractor-args", "youtube:commenter=default", "--write-comments", "--skip-download", "--no-playlist", "-o", str(output_template), url],
            ["yt-dlp", "--extractor-args", "youtube:commenter=legs", "--write-comments", "--skip-download", "--no-playlist", "-o", str(output_template), url],
            ["yt-dlp", "--extractor-args", "youtube:comments:thread=replies", "--write-comments", "--skip-download", "--no-playlist", "-o", str(output_template), url],
        ]
        for i, args in enumerate(methods[1:], start=2):
            if self.cache_dir:
                args.extend(["--cache-dir", self.cache_dir])
            LOGGER.info("Trying comment method %d: %s", i, args[1:4])
            result = self._run_and_find_comments(args, out_dir, video_id)
            if result:
                LOGGER.info("Method %d succeeded", i)
                return result
            LOGGER.info("Method %d failed, trying next...", i)
        return None

    def _run_and_find_comments(
        self,
        args: list[str],
        out_dir: Path,
        video_id: str | None,
    ) -> Path | None:
        stderr = ""
        try:
            result = subprocess.run(
                args,
                check=True,
                capture_output=True,
                text=True,
            )
            stderr = result.stderr
        except subprocess.CalledProcessError as e:
            stderr = e.stderr
            LOGGER.warning("yt-dlp failed: returncode=%s stderr=%s", e.returncode, stderr[-500:] if len(stderr) > 500 else stderr)
            return None

        if stderr:
            LOGGER.debug("yt-dlp stderr: %s", stderr[-1000:] if len(stderr) > 1000 else stderr)

        comments_file = self._find_comments_file(out_dir)
        if not comments_file:
            LOGGER.debug("No comments file found in %s", out_dir)
            existing = list(out_dir.glob("*"))
            LOGGER.debug("Files in output dir: %s", [p.name for p in existing])
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
        LOGGER.debug("Looking for *.comments.json in %s: found=%d", out_dir, len(candidates))
        if not candidates:
            candidates = list(out_dir.glob("*.comments.jsonl"))
            LOGGER.debug("Looking for *.comments.jsonl in %s: found=%d", out_dir, len(candidates))
        if not candidates:
            info_candidates = list(out_dir.glob("*.info.json"))
            if info_candidates:
                LOGGER.debug("Found info.json files, checking for embedded comments")
                for info_file in info_candidates:
                    comments = YtDlpClient._extract_comments_from_info_json(info_file)
                    if comments:
                        comments_file = info_file.with_suffix(".comments.json")
                        comments_file.write_text(json.dumps(comments, ensure_ascii=False), encoding="utf-8")
                        LOGGER.info("Extracted %d comments from info.json to %s", len(comments), comments_file.name)
                        return comments_file
            LOGGER.debug("No comments files found in %s", out_dir)
            all_files = list(out_dir.glob("*"))
            LOGGER.debug("All files in directory: %s", [p.name for p in all_files if p.is_file()])
            return None
        result = max(candidates, key=lambda p: p.stat().st_mtime)
        LOGGER.debug("Found comments file: %s (size=%d)", result.name, result.stat().st_size)
        return result

    @staticmethod
    def _extract_comments_from_info_json(info_path: Path) -> list[dict[str, Any]] | None:
        try:
            data = json.loads(info_path.read_text(encoding="utf-8"))
            comments = data.get("comments")
            if isinstance(comments, list) and len(comments) > 0:
                LOGGER.info("Found %d comments in info.json", len(comments))
                return comments
            LOGGER.debug("No comments in info.json (found: %s)", type(comments) if "comments" in data else "key missing")
            return None
        except (json.JSONDecodeError, OSError) as e:
            LOGGER.warning("Failed to read info.json: %s", e)
            return None

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
