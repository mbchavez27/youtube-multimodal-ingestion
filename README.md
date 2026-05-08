# YouTube Multimodal Ingestion Pipeline

Multimodal YouTube data ingestion pipeline for building structured datasets from video content.

## Overview

This project processes a YouTube video (via URL or ID) and extracts structured multimodal data for dataset creation. It focuses on transforming raw video content into analyzable representations without storing raw media.

## Extracted Data

This pipeline extracts five types of structured data from YouTube videos.

---

### Video Metadata (`record_type: "video"`)

| Field | Type | Description |
|-------|------|-------------|
| `video_id` | string | YouTube's unique video identifier |
| `title` | string | Video title |
| `description` | string | Video description text |
| `create_time` | ISO 8601 | Publish timestamp |
| `duration_seconds` | int | Video length in seconds |
| `view_count` | int | Total views (may be unavailable) |
| `like_count` | int | Like count (may be unavailable) |
| `comment_count` | int | Comment count |
| `hashtags` | list[string] | Extracted hashtags from description |
| `mentions` | list[string] | @mentions from description |
| `is_live` | bool | Whether video was a livestream |

---

### Comments (`record_type: "comment"`)

| Field | Type | Description |
|-------|------|-------------|
| `comment_id` | string | Unique comment identifier |
| `text` | string | Comment text content |
| `user_handle` | string | YouTube handle (e.g., `@username`) |
| `user_display_name` | string | Display name |
| `like_count` | int | Number of likes on comment |
| `reply_count` | int | Number of replies |
| `create_time` | ISO 8601 | Comment timestamp |
| `is_reply` | bool | Whether this is a reply |
| `parent_comment_id` | string | Parent comment ID (null if top-level) |

---

### Transcript (`record_type: "transcript"`)

| Field | Type | Description |
|-------|------|-------------|
| `language` | string | Detected language code (e.g., `en`, `ko`) |
| `text` | string | Full transcript as continuous text |
| `segments` | list[Segment] | Timestamped speech segments |

**Segment schema:**
| Field | Type | Description |
|-------|------|-------------|
| `start` | float | Start time in seconds |
| `end` | float | End time in seconds |
| `text` | string | Spoken text for this segment |

---

### Audio Features (`record_type: "audio_features"`)

Extracted using [librosa](https://librosa.org/). Each video is divided into 1-second segments.

| Field | Type | Description |
|-------|------|-------------|
| `sample_rate` | int | Audio sample rate (16000 Hz) |
| `duration_seconds` | float | Total audio duration |
| `segments` | list[AudioSegment] | Per-second acoustic features |

**AudioSegment schema:**
| Field | Type | Description |
|-------|------|-------------|
| `start` | float | Start time (seconds) |
| `end` | float | End time (seconds) |
| `energy_rms` | float | Root Mean Square energy — loudness/intensity of the audio frame. Range: 0 to ~0.5. Higher values indicate louder audio. |
| `zcr` | float | Zero Crossing Rate — how often the audio signal crosses zero. Range: 0 to 1. Higher values may indicate noisier or percussive audio. |
| `spectral_centroid_hz` | float | Spectral Centroid — "center of mass" of the spectrum in Hz. Correlates with perceived brightness/timbre. Typical speech: 1000-4000 Hz. |

---

### Visual Features (`record_type: "visual_features"`)

| Field | Type | Description |
|-------|------|-------------|
| `scenes` | list[Scene] | Detected scene boundaries |
| `ocr_frames` | list[OCRFrame] | OCR text from key frames |

**Scene schema:**
| Field | Type | Description |
|-------|------|-------------|
| `scene_index` | int | Scene number (1-indexed) |
| `start_frame` | int | First frame of scene |
| `end_frame` | int | Last frame of scene |
| `start_time` | float | Scene start (seconds) |
| `end_time` | float | Scene end (seconds) |

**OCRFrame schema:**
| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | float | Frame timestamp (seconds) |
| `text` | string | Extracted text (max 500 chars) |

> **Note:** OCR requires [Tesseract](https://github.com/tesseract-ocr/tesseract) to be installed. Without it, `text` fields return empty.

---

### Temporal Alignment (`record_type: "temporal_alignment"`)

Synchronizes transcript, audio, and visual features at 1-second intervals.

| Field | Type | Description |
|-------|------|-------------|
| `total_duration` | float | Total video duration (seconds) |
| `aligned_entries` | list[AlignEntry] | Time-synchronized records |

**AlignEntry schema:**
| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | float | Time point (seconds) |
| `transcript_text` | string | Spoken text at this timestamp (if any) |
| `audio_energy` | float | RMS energy at this timestamp |
| `scene_index` | int | Active scene number (if any) |

---

### Implementation Status

| Feature | Status | Notes |
|---------|--------|-------|
| Metadata | ✅ Works | Out of box |
| Comments | ✅ Works | Out of box |
| Transcript | ✅ Works | Out of box |
| Audio Features (energy, ZCR, centroid) | ✅ Works | Out of box |
| Visual Features (scene detection) | ✅ Works | Out of box |
| Visual Features (OCR) | ⚠️ Partial | Works without Tesseract (returns empty) |
| Temporal Alignment | ✅ Works | Syncs transcript, audio, visual by time |

## Installation

```bash
uv venv .venv && uv sync
```

Requires:
- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (for dependency management)
- ffmpeg (for audio extraction)

Install ffmpeg:
```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg

# Fedora
sudo dnf install ffmpeg
```

## Usage

```bash
uv run python youtube_ingest.py
```

The tool runs interactively. Follow the prompts:

```
YouTube Multimodal Ingestion Tool
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Enter YouTube URL or video ID: https://www.youtube.com/watch?v=...
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Output file [video_id.jsonl]:              # Press Enter for default
Max comments to fetch (0 = unlimited) [1000]:
Skip comments? [N]:                         # Y to skip
Skip transcript? [N]:                       # Y to skip
Language (auto/ko/en/etc) [auto]:
Model (small/medium/large-v3) [small]:
Device (auto/cpu/cuda) [auto]:
Keep temp audio (debug)? [N]:               # Y to keep audio file
Export .vtt/.txt files? [N]:                # Y to export transcript files
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

## Output

Each video produces a JSONL file (`video_id.jsonl`) with records:

- `record_type: "video"` — video metadata
- `record_type: "comment"` — comment records
- `record_type: "transcript"` — transcript with segments

Example output file:
```json
{"record_type": "video", "video_id": "dQw4w9WgXcQ", "title": "...", ...}
{"record_type": "transcript", "text": "...", "segments": [...], ...}
```

## Options Reference

| Prompt | Default | Description |
|--------|---------|-------------|
| URL/ID | (required) | YouTube URL or video ID |
| Output file | `video_id.jsonl` | Output JSONL path |
| Max comments | 1000 | Comments to fetch (0 = unlimited) |
| Skip comments | No | Skip comment scraping |
| Skip transcript | No | Skip transcription |
| Extract audio features | No | Extract energy, ZCR, spectral centroid |
| Extract visual features | No | Extract scenes + OCR (needs Tesseract) |
| Align multimodal features | No | Sync transcript, audio, visual by time |
| Language | auto | Transcript language (auto-detects) |
| Model | small | Whisper model: small, medium, large-v3 |
| Device | auto | Compute: auto, cpu, cuda |
| Keep audio | No | Keep temp audio file |
| Export files | No | Export .vtt and .txt files |

## Requirements

### Python packages (auto-installed via `uv sync`)
- yt-dlp — video metadata and comments
- faster-whisper — transcription
- librosa — audio feature extraction
- scenedetect — scene detection
- pytesseract — OCR (works without Tesseract, returns empty text)

### System dependencies (must install separately)
- **ffmpeg** — required for audio/video extraction
- **Tesseract** — optional, for OCR text extraction

## Key Principle

- No raw video or audio files are stored
- Only structured, derived multimodal representations are generated
- Temp audio is deleted after transcription (unless Keep audio is selected)

## Legal Note

Use responsibly and in compliance with YouTube terms, local law, and your institution's research ethics requirements.