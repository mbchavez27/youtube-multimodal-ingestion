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
    extract_audio_features: bool
    extract_visual_features: bool
    align_temporal: bool
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
    extract_audio_features = prompt_yes_no("Extract audio features (energy, ZCR, centroid)?", False)
    extract_visual_features = prompt_yes_no("Extract visual features (scenes, OCR)?", False)
    align_temporal = prompt_yes_no("Align multimodal features by time?", False)

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
        extract_audio_features=extract_audio_features,
        extract_visual_features=extract_visual_features,
        align_temporal=align_temporal,
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


def extract_audio_features(audio_path: Path) -> dict | None:
    LOGGER.info("Extracting audio features (energy, ZCR, centroid)...")

    try:
        import librosa
        import numpy as np

        y, sr = librosa.load(str(audio_path), sr=16000)

        duration = librosa.get_duration(y=y, sr=sr)
        LOGGER.info(f"Audio duration: {duration:.2f}s")

        default_frame_length = 2048
        default_hop_length = 512

        energy = librosa.feature.rms(
            y=y,
            frame_length=default_frame_length,
            hop_length=default_hop_length
        )[0]

        zcr = librosa.feature.zero_crossing_rate(
            y=y,
            frame_length=default_frame_length,
            hop_length=default_hop_length
        )[0]

        spectral_centroid = librosa.feature.spectral_centroid(
            y=y,
            sr=sr
        )[0]

        segments = []
        segment_duration = 1.0
        frames_per_segment = int(segment_duration * sr / default_hop_length)

        for i, start_time in enumerate(np.arange(0, duration, segment_duration)):
            end_time = min(start_time + segment_duration, duration)

            start_sample = int(start_time * sr)
            if start_sample >= len(y):
                break

            if i < len(energy):
                segment_energy = float(np.mean(energy[i * frames_per_segment:(i + 1) * frames_per_segment]))
                segment_zcr = float(np.mean(zcr[i * frames_per_segment:(i + 1) * frames_per_segment]))
                segment_centroid = float(np.mean(spectral_centroid[i * frames_per_segment:(i + 1) * frames_per_segment]))
            else:
                segment_energy = 0.0
                segment_zcr = 0.0
                segment_centroid = 0.0

            segments.append({
                "start": round(start_time, 2),
                "end": round(end_time, 2),
                "energy_rms": round(segment_energy, 6),
                "zcr": round(segment_zcr, 6),
                "spectral_centroid_hz": round(segment_centroid, 2),
            })

        LOGGER.info(f"Extracted audio features for {len(segments)} segments")

        return {
            "sample_rate": sr,
            "duration_seconds": round(duration, 2),
            "segments": segments,
        }
    except Exception as e:
        LOGGER.error("Audio feature extraction failed: %s", e)
        return None


def extract_visual_features(video_id: str, video_url: str) -> dict | None:
    LOGGER.info("Extracting visual features (scenes, OCR)...")

    tmp_video_dir = Path(f".tmp_video_{video_id}")
    tmp_video_dir.mkdir(parents=True, exist_ok=True)

    try:
        video_path = tmp_video_dir / f"{video_id}.mp4"

        LOGGER.info("Downloading video frames...")
        subprocess.run(
            [
                "yt-dlp",
                "-f", "best[height<=480]",
                "-o", str(video_path),
                "--no-playlist",
                "--quiet",
                video_url,
            ],
            check=True,
            capture_output=True,
        )

        if not video_path.exists():
            LOGGER.error("Video download failed")
            return None

        import scenedetect
        from scenedetect import SceneManager
        from scenedetect.detectors import ContentDetector

        video = scenedetect.open_video(str(video_path))
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(threshold=30.0))
        scene_manager.detect_scenes(video)

        scenes = scene_manager.get_scene_list()
        LOGGER.info(f"Detected {len(scenes)} scenes")

        scenes_list = []
        for i, (start, end) in enumerate(scenes):
            scenes_list.append({
                "scene_index": i + 1,
                "start_frame": start.frame_num,
                "end_frame": end.frame_num,
                "start_time": round(start.seconds, 2),
                "end_time": round(end.seconds, 2),
            })

        import pytesseract
        import cv2

        ocr_frames = []
        cap = cv2.VideoCapture(str(video_path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30

        frame_indices = []
        if scenes_list:
            for scene in scenes_list[:5]:
                mid_time = (scene["start_time"] + scene["end_time"]) / 2
                frame_idx = int(mid_time * fps)
                frame_indices.append(frame_idx)
        else:
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = frame_count / fps if fps > 0 else 0
            frame_indices = [int(i * duration / 5 * fps) for i in range(5)]

        for frame_idx in frame_indices[:5]:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            text = pytesseract.image_to_string(gray, lang='eng+kor')

            if text.strip():
                timestamp = round(frame_idx / fps if fps > 0 else 0, 2)
                ocr_frames.append({
                    "timestamp": timestamp,
                    "text": text.strip()[:500],
                })

        cap.release()

        LOGGER.info(f"Extracted {len(ocr_frames)} OCR frames")

        return {
            "scenes": scenes_list,
            "ocr_frames": ocr_frames,
        }

    except Exception as e:
        LOGGER.error("Visual feature extraction failed: %s", e)
        return None
    finally:
        import shutil
        try:
            shutil.rmtree(tmp_video_dir)
        except OSError:
            pass


def align_temporal_features(
    transcript: TranscriptItem | None,
    audio_features: dict | None,
    visual_features: dict | None,
) -> dict | None:
    LOGGER.info("Aligning multimodal features by timestamp...")

    if not transcript and not audio_features and not visual_features:
        LOGGER.warning("No features to align")
        return None

    aligned = []
    max_duration = 0.0

    if transcript and transcript.segments:
        last_end = max((seg["end"] for seg in transcript.segments), default=0)
        max_duration = max(max_duration, last_end)

    if audio_features and audio_features.get("segments"):
        last_end = max((seg["end"] for seg in audio_features["segments"]), default=0)
        max_duration = max(max_duration, last_end)

    if visual_features and visual_features.get("scenes"):
        last_end = max((seg["end_time"] for seg in visual_features["scenes"]), default=0)
        max_duration = max(max_duration, last_end)

    step = 1.0
    timestamp = 0.0

    while timestamp < max_duration:
        entry = {"timestamp": round(timestamp, 2)}

        if transcript and transcript.segments:
            for seg in transcript.segments:
                if seg["start"] <= timestamp < seg["end"]:
                    entry["transcript_text"] = seg["text"]
                    break

        if audio_features and audio_features.get("segments"):
            for seg in audio_features["segments"]:
                if seg["start"] <= timestamp < seg["end"]:
                    entry["audio_energy"] = seg.get("energy_rms")
                    break

        if visual_features and visual_features.get("scenes"):
            for scene in visual_features["scenes"]:
                if scene["start_time"] <= timestamp < scene["end_time"]:
                    entry["scene_index"] = scene["scene_index"]
                    break

        if entry.get("transcript_text") or entry.get("audio_energy") or entry.get("scene_index"):
            aligned.append(entry)

        timestamp += step

    LOGGER.info(f"Aligned {len(aligned)} timestamp entries")

    return {
        "aligned_entries": aligned,
        "total_duration": round(max_duration, 2),
    }


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
    audio_features: dict | None = None,
    visual_features: dict | None = None,
    temporal_alignment: dict | None = None,
) -> None:
    run_id = uuid.uuid4().hex
    scraped_at = datetime.now(timezone.utc).isoformat()

    records = []

    records.append(video.to_record(run_id=run_id, scraped_at=scraped_at))

    for comment in comments:
        records.append(comment.to_record(run_id=run_id, scraped_at=scraped_at))

    if transcript:
        records.append(transcript.to_record(run_id=run_id, scraped_at=scraped_at))

    if audio_features:
        audio_record = {
            "platform": "youtube",
            "record_type": "audio_features",
            "run_id": run_id,
            "scraped_at": scraped_at,
            "source_account": video.channel_id or "",
            "video_id": video.video_id,
            "video_url": video.video_url,
            "sample_rate": audio_features.get("sample_rate"),
            "duration_seconds": audio_features.get("duration_seconds"),
            "segments": audio_features.get("segments", []),
        }
        records.append(audio_record)

    if visual_features:
        visual_record = {
            "platform": "youtube",
            "record_type": "visual_features",
            "run_id": run_id,
            "scraped_at": scraped_at,
            "source_account": video.channel_id or "",
            "video_id": video.video_id,
            "video_url": video.video_url,
            "scenes": visual_features.get("scenes", []),
            "ocr_frames": visual_features.get("ocr_frames", []),
        }
        records.append(visual_record)

    if temporal_alignment:
        alignment_record = {
            "platform": "youtube",
            "record_type": "temporal_alignment",
            "run_id": run_id,
            "scraped_at": scraped_at,
            "source_account": video.channel_id or "",
            "video_id": video.video_id,
            "video_url": video.video_url,
            "total_duration": temporal_alignment.get("total_duration"),
            "aligned_entries": temporal_alignment.get("aligned_entries", []),
        }
        records.append(alignment_record)

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
    audio_features = None
    visual_features = None
    temporal_alignment = None
    tmp_audio = None

    needs_audio = not config.skip_transcript or config.extract_audio_features

    if needs_audio:
        tmp_audio = Path(f".tmp_audio_{video.video_id}.m4a")
        if not tmp_audio.exists():
            if not download_audio(config.url, tmp_audio):
                print("Warning: Could not download audio")
                tmp_audio = None
        else:
            LOGGER.info("Reusing existing temp audio file")

    if not config.skip_transcript and tmp_audio and WHISPER_AVAILABLE:
        transcript = transcribe_audio(
            tmp_audio,
            config.lang,
            config.model,
            config.device,
            config.compute_type,
        )
        if transcript and config.export_transcript_files:
            export_transcript_files(transcript, video.video_id, transcript.language or config.lang)
    elif not WHISPER_AVAILABLE and not config.skip_transcript:
        print("Warning: faster-whisper not installed. Skipping transcript.")

    if config.extract_audio_features and tmp_audio and tmp_audio.exists():
        audio_features = extract_audio_features(tmp_audio)
    elif config.extract_audio_features and not tmp_audio:
        print("Warning: No audio file available for feature extraction")

    if config.extract_visual_features:
        visual_features = extract_visual_features(video.video_id, config.url)
    elif config.extract_visual_features and not visual_features:
        print("Warning: Visual feature extraction failed")

    if config.align_temporal:
        temporal_alignment = align_temporal_features(transcript, audio_features, visual_features)
    elif config.align_temporal and not temporal_alignment:
        print("Warning: Temporal alignment failed")

    if tmp_audio and tmp_audio.exists() and not config.keep_audio:
        try:
            tmp_audio.unlink()
            LOGGER.info("Cleaned up temp audio file")
        except OSError:
            pass

    write_output(config.output_path, video, comments, transcript, audio_features, visual_features, temporal_alignment)

    features_extracted = []
    if audio_features:
        features_extracted.append("audio features")
    if visual_features:
        features_extracted.append("visual features")
    if temporal_alignment:
        features_extracted.append("temporal alignment")
    transcript_done = "transcript" if transcript else ""
    parts = [p for p in [f"Processed {len(comments)} comments", transcript_done] + features_extracted if p]
    print(f"\nDone! {' | '.join(parts)}")
    return 0


def main() -> int:
    config = parse_args()
    return run(config)


if __name__ == "__main__":
    raise SystemExit(main())