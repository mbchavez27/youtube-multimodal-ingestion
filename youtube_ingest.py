#!/usr/bin/env python3
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

LOGGER = logging.getLogger("youtube_ingest")

repo_root = Path(__file__).parent
venv_bin = repo_root / ".venv" / "bin"
if venv_bin.exists():
    import os
    current_path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{venv_bin}{os.pathsep}{current_path}"

sys.path.insert(0, str(repo_root / "youtube_scraper"))
sys.path.insert(0, str(repo_root / "whisper_transcriber"))

from youtube_scraper.scraper.yt_dlp_client import YtDlpClient
from youtube_scraper.scraper.video_scraper import VideoScraper
from youtube_scraper.scraper.comment_scraper import CommentScraper
from youtube_scraper.models import VideoItem, CommentItem, TranscriptItem

try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False


@dataclass(slots=True)
class Config:
    url: str
    output_path: str | None
    max_comments: int
    skip_comments: bool
    skip_transcript: bool
    lang: str
    model: str
    device: str
    compute_type: str
    keep_audio: bool
    export_transcript_files: bool


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


def prompt_default(prompt: str, default: str) -> str:
    user_input = input(f"{prompt} [{default}]: ").strip()
    return default if not user_input else user_input


def prompt_yes_no(prompt: str, default: bool = False) -> bool:
    default_str = "Y" if default else "N"
    user_input = input(f"{prompt} [{default_str}]: ").strip().lower()
    if not user_input:
        return default
    return user_input in ("y", "yes")


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def get_video_info(url: str) -> dict | None:
    client = YtDlpClient()
    try:
        return client.dump_json(url)
    except Exception as e:
        LOGGER.warning("Failed to fetch video info: %s", e)
        return None


def parse_args() -> Config:
    print("YouTube Multimodal Ingestion Tool")
    print("━" * 50)

    url_input = input("Enter YouTube URL or video ID: ").strip()
    url = normalize_url(url_input)
    video_id = extract_video_id(url)

    if not video_id:
        print("Error: Invalid YouTube URL or video ID")
        sys.exit(1)

    print(f"\nProcessing: {url}")
    print("━" * 50)

    output_path = prompt_default("Output file", f"{video_id}.jsonl")
    if output_path == "-":
        output_path = None
    elif not output_path.endswith(".jsonl"):
        output_path = f"{output_path}.jsonl"

    try:
        max_comments = int(prompt_default("Max comments to fetch (0 = unlimited)", "1000"))
    except ValueError:
        max_comments = 1000

    skip_comments = prompt_yes_no("Skip comments?", False)
    skip_transcript = prompt_yes_no("Skip transcript?", False)

    lang = prompt_default("Language (auto/ko/en/etc)", "auto")
    model = prompt_default("Model (small/medium/large-v3)", "small")
    device = prompt_default("Device (auto/cpu/cuda)", "auto")

    keep_audio = prompt_yes_no("Keep temp audio (debug)?", False)
    export_transcript_files = prompt_yes_no("Export .vtt/.txt files?", False)

    print("━" * 50 + "\n")

    return Config(
        url=url,
        output_path=output_path,
        max_comments=max_comments,
        skip_comments=skip_comments,
        skip_transcript=skip_transcript,
        lang=lang,
        model=model,
        device=device,
        compute_type="int8",
        keep_audio=keep_audio,
        export_transcript_files=export_transcript_files,
    )


def scrape_video(config: Config) -> VideoItem | None:
    LOGGER.info("Fetching video metadata...")
    client = YtDlpClient()
    scraper = VideoScraper(client)
    try:
        video = scraper.scrape_video(
            channel_id="",
            video_url=config.url,
            window_label=None,
            anchor_id=None,
        )
        LOGGER.info("Video: %s", video.title)
        return video
    except Exception as e:
        LOGGER.error("Failed to scrape video: %s", e)
        return None


def scrape_comments(config: Config, video: VideoItem) -> list[CommentItem]:
    if config.skip_comments:
        return []

    LOGGER.info("Fetching comments (max=%d)...", config.max_comments)
    client = YtDlpClient()
    scraper = CommentScraper(client, max_comments_per_video=config.max_comments)

    tmp_dir = Path(f".tmp_comments_{video.video_id}")
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        comments = scraper.scrape_comments(
            channel_id=video.channel_id or "",
            video_id=video.video_id,
            video_url=config.url,
            window_label=None,
            anchor_id=None,
            output_basename="comments",
            output_dir=str(tmp_dir),
        )
        LOGGER.info("Fetched %d comments", len(comments))
        return comments
    except Exception as e:
        LOGGER.warning("Failed to scrape comments: %s", e)
        return []
    finally:
        import shutil
        try:
            shutil.rmtree(tmp_dir)
        except OSError:
            pass


def download_audio(url: str, output_path: Path) -> bool:
    LOGGER.info("Downloading audio...")
    try:
        args = [
            "yt-dlp",
            "-f", "bestaudio/best",
            "-o", str(output_path),
            "--no-playlist",
            "--quiet",
            url,
        ]
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=True,
        )
        return output_path.exists()
    except subprocess.CalledProcessError as e:
        LOGGER.error("Audio download failed: %s", e.stderr)
        return False


def transcribe_audio(
    audio_path: Path,
    lang: str,
    model: str,
    device: str,
    compute_type: str,
) -> TranscriptItem | None:
    if not WHISPER_AVAILABLE:
        LOGGER.error("faster-whisper not installed. Run: pip install faster-whisper")
        return None

    resolved_device = device
    if device == "auto":
        resolved_device = "cuda" if _cuda_available() else "cpu"

    LOGGER.info("Loading Whisper model: %s on %s", model, resolved_device)

    try:
        whisper_model = WhisperModel(model, device=resolved_device, compute_type=compute_type)
    except RuntimeError as e:
        if resolved_device == "cuda":
            LOGGER.warning("CUDA failed, falling back to CPU: %s", e)
            whisper_model = WhisperModel(model, device="cpu", compute_type=compute_type)
        else:
            LOGGER.error("Failed to load Whisper model: %s", e)
            return None

    LOGGER.info("Transcribing...")
    try:
        segments, info = whisper_model.transcribe(
            str(audio_path),
            language=None if lang == "auto" else lang,
            beam_size=5,
            vad_filter=True,
        )

        segment_list = []
        full_text = []

        for seg in segments:
            segment_list.append({
                "start": seg.start,
                "end": seg.end,
                "text": seg.text,
            })
            full_text.append(seg.text)

        text = " ".join(full_text)
        detected_lang = info.language if info.language else lang

        LOGGER.info("Transcription complete: %.1fs, language=%s", info.duration or 0, detected_lang)

        return TranscriptItem(
            channel_id="",
            video_id=extract_video_id(str(audio_path)) or "",
            video_url="",
            language=detected_lang,
            text=text,
            segments=segment_list,
            window_label=None,
            anchor_id=None,
        )
    except Exception as e:
        LOGGER.error("Transcription failed: %s", e)
        return None


def _cuda_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


def export_transcript_files(transcript: TranscriptItem, video_id: str, lang: str) -> None:
    base_path = Path(f"data/{video_id}")
    base_path.mkdir(parents=True, exist_ok=True)

    vtt_path = base_path / f"{video_id}.{lang}.vtt"
    txt_path = base_path / f"{video_id}.{lang}.txt"

    with vtt_path.open("w", encoding="utf-8") as f:
        f.write("WEBVTT\n\n")
        for seg in transcript.segments:
            start = _format_vtt_time(seg["start"])
            end = _format_vtt_time(seg["end"])
            f.write(f"{start} --> {end}\n{seg['text']}\n\n")

    with txt_path.open("w", encoding="utf-8") as f:
        f.write(transcript.text)

    LOGGER.info("Exported: %s, %s", vtt_path, txt_path)


def _format_vtt_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def write_output(
    output_path: str | None,
    video: VideoItem,
    comments: list[CommentItem],
    transcript: TranscriptItem | None,
) -> None:
    run_id = uuid.uuid4().hex
    scraped_at = datetime.now(timezone.utc).isoformat()

    records = []

    records.append(video.to_record(run_id=run_id, scraped_at=scraped_at))

    for comment in comments:
        records.append(comment.to_record(run_id=run_id, scraped_at=scraped_at))

    if transcript:
        records.append(transcript.to_record(run_id=run_id, scraped_at=scraped_at))

    if output_path is None:
        for record in records:
            print(json.dumps(record, ensure_ascii=False))
    else:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        LOGGER.info("Output written to: %s", path)


def run(config: Config) -> int:
    setup_logging()

    video = scrape_video(config)
    if not video:
        print("Error: Could not fetch video information")
        return 1

    print(f"Title: {video.title}")
    print(f"Channel: {video.channel_id}")
    print(f"Duration: {video.duration_seconds}s")
    print(f"Views: {video.view_count or 'N/A'}\n")

    comments = scrape_comments(config, video)

    transcript = None
    if not config.skip_transcript:
        if not WHISPER_AVAILABLE:
            print("Warning: faster-whisper not installed. Skipping transcript.")
        else:
            tmp_audio = Path(f".tmp_audio_{video.video_id}.m4a")
            if download_audio(config.url, tmp_audio):
                transcript = transcribe_audio(
                    tmp_audio,
                    config.lang,
                    config.model,
                    config.device,
                    config.compute_type,
                )
                if transcript and config.export_transcript_files:
                    export_transcript_files(transcript, video.video_id, transcript.language or config.lang)
                if not config.keep_audio:
                    try:
                        tmp_audio.unlink()
                    except OSError:
                        pass
            else:
                print("Warning: Could not download audio for transcription")

    write_output(config.output_path, video, comments, transcript)

    print(f"\nDone! Processed {len(comments)} comments" + (f" and transcript" if transcript else ""))
    return 0


def main() -> int:
    config = parse_args()
    return run(config)


if __name__ == "__main__":
    raise SystemExit(main())