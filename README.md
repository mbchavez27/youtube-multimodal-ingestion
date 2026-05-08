# YouTube Multimodal Ingestion Pipeline

Unified YouTube data ingestion tool that extracts metadata, comments, and transcriptions from videos.

## What It Extracts

| Data Type | Description |
|-----------|-------------|
| **Metadata** | Title, description, channel, publish date, tags, views, likes, duration |
| **Comments** | Text, author, like count, timestamps, replies |
| **Transcript** | Full text + timestamped segments via Whisper |

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
| Language | auto | Transcript language (auto-detects) |
| Model | small | Whisper model: small, medium, large-v3 |
| Device | auto | Compute: auto, cpu, cuda |
| Keep audio | No | Keep temp audio file |
| Export files | No | Export .vtt and .txt files |

## Requirements

- **yt-dlp** — for video metadata and comments (installed via uv)
- **faster-whisper** — for local transcription (installed via uv)
- **ffmpeg** — system dependency for audio extraction

## Key Principle

- No raw video files are stored
- Only structured, derived data (JSONL, .txt, .vtt) is output
- Temp audio is deleted after transcription (unless `--keep-audio` is set)

## Legal Note

Use responsibly and in compliance with YouTube terms, local law, and your institution's research ethics requirements.