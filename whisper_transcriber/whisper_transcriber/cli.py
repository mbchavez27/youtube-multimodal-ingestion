from __future__ import annotations

import argparse
import csv
import io
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from faster_whisper import WhisperModel
from tqdm import tqdm

LOGGER = logging.getLogger("whisper_transcriber")


@dataclass(slots=True)
class RunConfig:
    manual_csv: str
    out_dir: str
    tmp_dir: str
    lang: str
    model: str
    device: str
    compute_type: str
    keep_audio: bool
    yt_dlp_path: str
    window: str | None
    progress_window: bool


@dataclass(slots=True)
class WindowProgress:
    bar: tqdm
    file_duration: float | None
    file_progress: float = 0.0

    def update_to(self, seconds: float) -> None:
        if self.bar is None:
            return
        if seconds < 0:
            return
        if self.file_duration is not None:
            seconds = min(seconds, self.file_duration)
        delta = seconds - self.file_progress
        if delta <= 0:
            return
        self.bar.update(delta)
        self.file_progress = seconds

    def finalize(self) -> None:
        if self.file_duration is None:
            return
        self.update_to(self.file_duration)


def parse_args(argv: list[str]) -> RunConfig:
    parser = argparse.ArgumentParser(
        description="Transcribe YouTube audio locally using faster-whisper"
    )
    default_device = "auto"
    parser.add_argument(
        "--manual-csv",
        required=True,
        help="CSV with columns window,video (pre/cancel/post)",
    )
    parser.add_argument(
        "--window",
        default=None,
        choices=("pre", "cancel", "post"),
        help="Only process a single window (pre/cancel/post). Default: all windows.",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Transcript output dir (default data/<person>/transcripts)",
    )
    parser.add_argument(
        "--tmp-dir",
        default=None,
        help="Temp audio dir (default data/<person>/.tmp_audio)",
    )
    parser.add_argument("--lang", default="ko", help="Preferred subtitle language")
    parser.add_argument("--model", default="small", help="Whisper model size")
    parser.add_argument(
        "--device",
        default=default_device,
        choices=("cuda", "cpu", "auto"),
        help="Compute device (auto selects cuda if available, else cpu)",
    )
    parser.add_argument(
        "--compute-type",
        default="int8",
        help="faster-whisper compute type (e.g., int8, float16)",
    )
    parser.add_argument(
        "--keep-audio",
        action="store_true",
        help="Keep temp audio files (debugging)",
    )
    parser.add_argument(
        "--yt-dlp",
        dest="yt_dlp_path",
        default=None,
        help="Path to yt-dlp binary (default: auto-detect)",
    )
    parser.add_argument(
        "--progress-window",
        action="store_true",
        help="Show a single progress bar per window (transcription only)",
    )
    parser.add_argument("--log-level", default="INFO")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    manual_csv = str(args.manual_csv)
    person = Path(manual_csv).stem
    out_dir = _resolve_under_root(
        str(args.out_dir) if args.out_dir else f"data/{person}/transcripts"
    )
    tmp_dir = _resolve_under_root(
        str(args.tmp_dir) if args.tmp_dir else f"data/{person}/.tmp_audio"
    )
    yt_dlp_path = args.yt_dlp_path or _find_yt_dlp()

    return RunConfig(
        manual_csv=manual_csv,
        out_dir=out_dir,
        tmp_dir=tmp_dir,
        lang=str(args.lang),
        model=str(args.model),
        device=str(args.device),
        compute_type=str(args.compute_type),
        keep_audio=bool(args.keep_audio),
        yt_dlp_path=str(yt_dlp_path),
        window=str(args.window) if args.window else None,
        progress_window=bool(args.progress_window),
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(sys.argv[1:] if argv is None else argv)
    return run(config)


def run(config: RunConfig) -> int:
    out_dir = Path(config.out_dir)
    tmp_dir = Path(config.tmp_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    manual_groups = _load_manual_csv(config.manual_csv)
    total_planned = sum(len(items) for items in manual_groups.values())
    LOGGER.info("Loaded %d videos from %s", total_planned, config.manual_csv)
    LOGGER.info(
        "Loaded manual videos pre=%d cancel=%d post=%d total=%d",
        len(manual_groups["pre"]),
        len(manual_groups["cancel"]),
        len(manual_groups["post"]),
        total_planned,
    )
    LOGGER.info("Output dir=%s", out_dir)
    LOGGER.info("Temp dir=%s", tmp_dir)
    LOGGER.info("yt-dlp=%s", config.yt_dlp_path)

    whisper_model: WhisperModel | None = None

    failures = 0
    skipped = 0
    processed = 0

    # Cache by video_id so duplicates across windows can reuse outputs without re-download.
    cache: dict[str, dict[str, Path]] = {}

    windows_to_process = (
        (config.window,) if config.window else ("pre", "cancel", "post")
    )
    for window_label in windows_to_process:
        selected = manual_groups[window_label]
        total_selected = len(selected)
        duration_cache: dict[str, float] = {}
        progress_bar: tqdm | None = None
        if config.progress_window and total_selected:
            total_seconds = 0.0
            for video_url in selected:
                if not _extract_video_id(video_url):
                    continue
                duration = _yt_dlp_duration_seconds(
                    yt_dlp_path=config.yt_dlp_path,
                    video_url=video_url,
                )
                if duration is None:
                    continue
                duration_cache[video_url] = duration
                total_seconds += duration
            progress_bar = tqdm(
                total=total_seconds if total_seconds > 0 else None,
                unit="s",
                unit_scale=True,
                desc=f"window {window_label}",
                leave=True,
            )
        LOGGER.info("Window %s selected=%d", window_label, total_selected)
        for index, video_url in enumerate(selected, start=1):
            output_basename = f"{window_label}-{index}"
            remaining = max(total_selected - index, 0)
            window_progress = (
                WindowProgress(
                    bar=progress_bar,
                    file_duration=duration_cache.get(video_url),
                )
                if progress_bar is not None
                else None
            )

            video_id = _extract_video_id(video_url)
            if not video_id:
                # Allow CSVs to include channel URLs etc; skip without failing the run.
                LOGGER.warning("Skipping non-video URL: %s", video_url)
                skipped += 1
                continue

            processed += 1
            LOGGER.info(
                "[%d/%d] window=%s remaining=%d video_id=%s",
                processed,
                total_planned,
                window_label,
                remaining,
                video_id,
            )

            whisper_vtt = (
                out_dir / f"{output_basename}.{config.lang}.source-whisper.vtt"
            )
            txt_out = out_dir / f"{output_basename}.{config.lang}.txt"

            if txt_out.exists() and whisper_vtt.exists():
                LOGGER.info("Transcript already exists; skipping %s", output_basename)
                if window_progress is not None:
                    window_progress.finalize()
                continue

            if video_id in cache:
                cached = cache[video_id]
                _copy_if_missing(cached.get("txt"), txt_out)
                _copy_if_missing(cached.get("vtt_whisper"), whisper_vtt)
                if txt_out.exists() and whisper_vtt.exists():
                    LOGGER.info("Reused cached transcript for %s", output_basename)
                    if window_progress is not None:
                        window_progress.finalize()
                    continue

            if _migrate_legacy_transcripts(
                out_dir=out_dir,
                video_id=video_id,
                lang=config.lang,
                target_basename=output_basename,
            ):
                _update_cache_from_outputs(
                    cache=cache,
                    out_dir=out_dir,
                    video_id=video_id,
                    basename=output_basename,
                    lang=config.lang,
                )
                if window_progress is not None:
                    window_progress.finalize()
                continue

            if whisper_model is None:
                desired_device = str(config.device).lower()
                resolved_device = desired_device
                if desired_device == "auto":
                    resolved_device = "cuda" if _cuda_available() else "cpu"

                LOGGER.info(
                    "Loading Whisper model=%s device=%s compute_type=%s",
                    config.model,
                    resolved_device,
                    config.compute_type,
                )
                try:
                    whisper_model = WhisperModel(
                        config.model,
                        device=resolved_device,
                        compute_type=config.compute_type,
                    )
                except RuntimeError as exc:
                    # Common on shared machines: CUDA runtime/driver mismatch.
                    if resolved_device == "cuda":
                        LOGGER.warning(
                            "Failed to init Whisper on CUDA (%s); falling back to CPU",
                            exc,
                        )
                        whisper_model = WhisperModel(
                            config.model,
                            device="cpu",
                            compute_type=config.compute_type,
                        )
                    else:
                        raise

            audio_path = tmp_dir / f"{video_id}.m4a"
            try:
                _download_audio(
                    yt_dlp_path=config.yt_dlp_path,
                    video_url=video_url,
                    out_audio_path=audio_path,
                )
                _whisper_to_vtt_and_txt(
                    model=whisper_model,
                    audio_path=audio_path,
                    lang=config.lang,
                    out_vtt_path=whisper_vtt,
                    out_txt_path=txt_out,
                    progress=window_progress,
                )
                _update_cache_from_outputs(
                    cache=cache,
                    out_dir=out_dir,
                    video_id=video_id,
                    basename=output_basename,
                    lang=config.lang,
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "Transcription failed window=%s slot=%s video_id=%s: %s",
                    window_label,
                    output_basename,
                    video_id,
                    exc,
                )
                failures += 1
                if window_progress is not None:
                    window_progress.finalize()
            finally:
                if not config.keep_audio:
                    try:
                        if audio_path.exists():
                            audio_path.unlink()
                    except OSError:
                        pass
        if progress_bar is not None:
            progress_bar.close()

    if failures or skipped:
        LOGGER.info(
            "Completed videos=%d skipped=%d failures=%d",
            total_planned,
            skipped,
            failures,
        )
    else:
        LOGGER.info("Completed successfully")
    return 0 if failures == 0 else 1


def _resolve_under_root(path_value: str) -> str:
    candidate = Path(path_value).expanduser()
    if candidate.is_absolute():
        return str(candidate)
    repo_root = Path(__file__).resolve().parents[3]
    return str(repo_root / candidate)


def _find_yt_dlp() -> str:
    # Prefer module-local yt-dlp if present, else system PATH.
    repo_root = Path(__file__).resolve().parents[3]
    module_local = (
        repo_root / "01-collection" / "youtube_scraper" / ".venv" / "bin" / "yt-dlp"
    )
    if module_local.exists():
        return str(module_local)
    found = shutil.which("yt-dlp")
    if found:
        return found
    raise FileNotFoundError(
        "yt-dlp not found. Install yt-dlp or provide --yt-dlp /path/to/yt-dlp"
    )


def _load_manual_csv(path_value: str) -> dict[str, list[str]]:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        # Match youtube_scraper behavior: treat relative paths as repo-root relative
        path = Path(_resolve_under_root(str(path)))
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {path}")

    rows: dict[str, list[str]] = {"pre": [], "cancel": [], "post": []}

    with path.open("r", encoding="utf-8", newline="") as handle:
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


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def _copy_if_missing(src: Path | None, dst: Path) -> None:
    if not src or not src.exists() or dst.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _migrate_legacy_transcripts(
    *, out_dir: Path, video_id: str, lang: str, target_basename: str
) -> bool:
    legacy_txt = out_dir / f"{video_id}.{lang}.txt"
    legacy_whisper_vtt = out_dir / f"{video_id}.{lang}.source-whisper.vtt"

    new_txt = out_dir / f"{target_basename}.{lang}.txt"
    new_whisper_vtt = out_dir / f"{target_basename}.{lang}.source-whisper.vtt"

    used = False
    if legacy_txt.exists():
        _copy_if_missing(legacy_txt, new_txt)
        used = True
    if legacy_whisper_vtt.exists():
        _copy_if_missing(legacy_whisper_vtt, new_whisper_vtt)
        used = True

    if used and new_txt.exists() and new_whisper_vtt.exists():
        LOGGER.info("Migrated legacy transcript -> %s", target_basename)
        return True
    return False


def _update_cache_from_outputs(
    *,
    cache: dict[str, dict[str, Path]],
    out_dir: Path,
    video_id: str,
    basename: str,
    lang: str,
) -> None:
    txt_out = out_dir / f"{basename}.{lang}.txt"
    whisper_vtt = out_dir / f"{basename}.{lang}.source-whisper.vtt"

    if not txt_out.exists():
        return
    if not whisper_vtt.exists():
        return

    entry: dict[str, Path] = {"txt": txt_out}
    if whisper_vtt.exists():
        entry["vtt_whisper"] = whisper_vtt
    cache[video_id] = entry


def _normalize_video_url(value: str) -> str:
    if value.startswith("http://") or value.startswith("https://"):
        return value
    # Raw ID
    return f"https://www.youtube.com/watch?v={value}"


def _extract_video_id(video_url: str) -> str | None:
    # Minimal parsing (good enough for watch URLs and youtu.be links)
    if "watch?v=" in video_url:
        return video_url.split("watch?v=", 1)[1].split("&", 1)[0]
    if "youtu.be/" in video_url:
        return video_url.split("youtu.be/", 1)[1].split("?", 1)[0]
    return None


def _run_yt_dlp(args: list[str]) -> subprocess.CompletedProcess[str]:
    LOGGER.debug("yt-dlp %s", " ".join(args[1:]))
    return subprocess.run(
        args,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _download_audio(*, yt_dlp_path: str, video_url: str, out_audio_path: Path) -> None:
    out_audio_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_prefix = out_audio_path.with_suffix("")
    cmd = [
        yt_dlp_path,
        "-f",
        "bestaudio/best",
        "--no-playlist",
        "-o",
        str(tmp_prefix) + ".%(ext)s",
        video_url,
    ]
    proc = _run_yt_dlp(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp audio download failed: {proc.stdout.strip()}")

    # yt-dlp picks an extension; move the downloaded file to the expected path
    candidates = [
        p for p in out_audio_path.parent.glob(tmp_prefix.name + ".*") if p.is_file()
    ]
    if not candidates:
        raise FileNotFoundError("Audio download produced no file")
    # Prefer m4a if present
    chosen = None
    for p in candidates:
        if p.suffix.lower() == ".m4a":
            chosen = p
            break
    if chosen is None:
        chosen = candidates[0]
    chosen.replace(out_audio_path)
    for extra in candidates:
        if extra.exists() and extra != out_audio_path:
            try:
                extra.unlink()
            except OSError:
                pass


def _whisper_to_vtt_and_txt(
    *,
    model: WhisperModel,
    audio_path: Path,
    lang: str,
    out_vtt_path: Path,
    out_txt_path: Path,
    progress: WindowProgress | None = None,
) -> None:
    segments, _info = model.transcribe(
        str(audio_path),
        language=lang,
        vad_filter=True,
    )
    out_vtt_path.parent.mkdir(parents=True, exist_ok=True)

    vtt_lines = ["WEBVTT", ""]
    txt_lines: list[str] = []
    for seg in segments:
        if progress is not None:
            progress.update_to(float(seg.end))
        start = _format_vtt_time(seg.start)
        end = _format_vtt_time(seg.end)
        text = (seg.text or "").strip()
        if not text:
            continue
        vtt_lines.append(f"{start} --> {end}")
        vtt_lines.append(text)
        vtt_lines.append("")
        txt_lines.append(text)

    if progress is not None:
        progress.finalize()

    out_vtt_path.write_text("\n".join(vtt_lines) + "\n", encoding="utf-8")
    out_txt_path.write_text(
        "\n".join(txt_lines) + ("\n" if txt_lines else ""), encoding="utf-8"
    )


def _format_vtt_time(seconds: float) -> str:
    # VTT requires HH:MM:SS.mmm
    if seconds < 0:
        seconds = 0
    ms_total = int(round(seconds * 1000.0))
    ms = ms_total % 1000
    s_total = ms_total // 1000
    s = s_total % 60
    m_total = s_total // 60
    m = m_total % 60
    h = m_total // 60
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _yt_dlp_duration_seconds(*, yt_dlp_path: str, video_url: str) -> float | None:
    cmd = [
        yt_dlp_path,
        "--no-playlist",
        "--print",
        "%(duration)s",
        video_url,
    ]
    proc = _run_yt_dlp(cmd)
    if proc.returncode != 0:
        LOGGER.warning("yt-dlp duration lookup failed: %s", proc.stdout.strip())
        return None
    for line in proc.stdout.splitlines():
        value = line.strip()
        if not value:
            continue
        try:
            duration = float(value)
        except ValueError:
            continue
        if duration <= 0:
            return None
        return duration
    return None


def _cuda_available() -> bool:
    try:
        import ctranslate2
    except Exception:
        return False
    try:
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
