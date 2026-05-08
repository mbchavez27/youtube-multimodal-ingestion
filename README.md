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

## Key Principle

- No raw video or audio files are stored
- Only structured, derived multimodal representations are generated

## Output

Each processed video is converted into a structured dataset entry containing aligned multimodal features and metadata.
