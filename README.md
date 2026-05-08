# YouTube Multimodal Ingestion Pipeline

Multimodal YouTube data ingestion pipeline for building structured datasets from video content.

## Overview

This project processes a YouTube video (via URL or ID) and extracts structured multimodal data for dataset creation. It focuses on transforming raw video content into analyzable representations without storing raw media.

## Extracted Data

### Metadata
- Title
- Description
- Channel information
- Publish date
- Tags
- View, like, and comment counts

### Text / Transcript
- Video transcripts (manual or auto-generated)
- Timestamped speech segments
- Cleaned and segmented text

### Audio Features
- Speech segment timing
- Acoustic features (e.g., pitch, energy, duration)

### Visual Features
- Scene segmentation
- Frame-level embeddings
- OCR-extracted text from frames

### Temporal Alignment
- Synchronization of text, audio, and visual signals
- Time-based event structuring

### Engagement Signals
- Views, likes, and comments (when available)

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