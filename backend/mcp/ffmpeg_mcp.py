"""FFmpeg wrapper (spec C.7).

Discrete functions, not shell strings scattered through agent code. Three consumers:

* **Transcriptor** - extract audio from a video, split long audio into uploadable chunks.
* **Audio Curator** - trim, loop, normalise to -14 LUFS and convert to Ogg Vorbis.
* **Tester** - pull frames out of a recorded gameplay clip when a run was captured as video.

FFmpeg is an external binary. When it is missing, every function raises
:class:`FFmpegUnavailable` with the exact install instruction rather than an opaque
``FileNotFoundError``, and callers degrade gracefully (the Transcriptor can still use
yt-dlp subtitles; the Curator can still copy a file unchanged).
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

log = logging.getLogger(__name__)

FFMPEG_CANDIDATES = ("ffmpeg", "ffmpeg.exe")
FFPROBE_CANDIDATES = ("ffprobe", "ffprobe.exe")
DEFAULT_LUFS = -14.0
DEFAULT_SEGMENT_S = 600  # 10 minutes: Deepgram's sweet spot for a single upload


class FFmpegUnavailable(RuntimeError):
    pass


class FFmpegError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    path: str
    duration_s: float = 0.0
    format_name: str = ""
    has_audio: bool = False
    has_video: bool = False
    sample_rate: int = 0
    channels: int = 0
    bit_rate: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


def ffmpeg_path() -> str | None:
    for candidate in FFMPEG_CANDIDATES:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def ffprobe_path() -> str | None:
    for candidate in FFPROBE_CANDIDATES:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def available() -> bool:
    return ffmpeg_path() is not None


def describe() -> dict[str, Any]:
    tool = ffmpeg_path()
    return {
        "available": bool(tool),
        "ffmpeg": tool or "",
        "ffprobe": ffprobe_path() or "",
        "install_hint": (
            "Install FFmpeg and make sure it is on PATH: winget install Gyan.FFmpeg "
            "(Windows), brew install ffmpeg (macOS), or your package manager on Linux."
        ),
    }


def _run(args: Sequence[str], *, timeout: int = 600) -> subprocess.CompletedProcess:
    tool = ffmpeg_path()
    if not tool:
        raise FFmpegUnavailable(describe()["install_hint"])
    result = subprocess.run(  # noqa: S603 - fixed binary, argument list, no shell
        [tool, "-hide_banner", "-nostdin", "-y", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        raise FFmpegError(
            f"ffmpeg exited {result.returncode}: {(result.stderr or result.stdout).strip()[-600:]}"
        )
    return result


def probe(path: str | Path) -> MediaInfo:
    """Read duration/format/streams. Uses ffprobe when present, else ffmpeg's own parse."""
    target = Path(path)
    if not target.exists():
        raise FFmpegError(f"{target} does not exist.")
    prober = ffprobe_path()
    if prober:
        result = subprocess.run(  # noqa: S603
            [prober, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(target)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            payload = json.loads(result.stdout)
            streams = payload.get("streams") or []
            audio = next((item for item in streams if item.get("codec_type") == "audio"), {})
            video = next((item for item in streams if item.get("codec_type") == "video"), None)
            return MediaInfo(
                path=str(target),
                duration_s=float((payload.get("format") or {}).get("duration") or 0.0),
                format_name=(payload.get("format") or {}).get("format_name", ""),
                has_audio=bool(audio),
                has_video=video is not None,
                sample_rate=int(audio.get("sample_rate") or 0),
                channels=int(audio.get("channels") or 0),
                bit_rate=int((payload.get("format") or {}).get("bit_rate") or 0),
                raw=payload,
            )
    # Fall back to ffmpeg's stderr summary.
    tool = ffmpeg_path()
    if not tool:
        raise FFmpegUnavailable(describe()["install_hint"])
    result = subprocess.run(  # noqa: S603
        [tool, "-hide_banner", "-i", str(target)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    text = result.stderr or ""
    duration_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", text)
    duration = 0.0
    if duration_match:
        hours, minutes, seconds = duration_match.groups()
        duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return MediaInfo(
        path=str(target),
        duration_s=duration,
        format_name="",
        has_audio="Audio:" in text,
        has_video="Video:" in text,
    )


# --- transcriptor helpers -------------------------------------------------------------


def extract_audio(
    video_path: str | Path,
    out_path: str | Path,
    *,
    sample_rate: int = 16000,
    mono: bool = True,
    start: float | None = None,
    duration: float | None = None,
) -> Path:
    """Pull a mono 16 kHz WAV out of a video - what speech-to-text services want."""
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    args: list[str] = []
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", str(video_path)]
    if duration is not None:
        args += ["-t", f"{duration:.3f}"]
    args += ["-vn", "-ac", "1" if mono else "2", "-ar", str(sample_rate), "-f", "wav", str(target)]
    _run(args)
    return target


def detect_silences(path: str | Path, *, noise_db: float = -32.0, min_silence_s: float = 0.6) -> list[tuple[float, float]]:
    """Return silence boundaries - used to split audio at natural pauses, not mid-word."""
    tool = ffmpeg_path()
    if not tool:
        raise FFmpegUnavailable(describe()["install_hint"])
    result = subprocess.run(  # noqa: S603
        [tool, "-hide_banner", "-nostdin", "-i", str(path), "-af",
         f"silencedetect=noise={noise_db}dB:d={min_silence_s}", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    text = result.stderr or ""
    starts = [float(value) for value in re.findall(r"silence_start:\s*([\d.]+)", text)]
    ends = [float(value) for value in re.findall(r"silence_end:\s*([\d.]+)", text)]
    return list(zip(starts, ends))


def segment_audio(
    path: str | Path,
    out_dir: str | Path,
    *,
    max_seconds: int = DEFAULT_SEGMENT_S,
    prefer_silence: bool = True,
) -> list[Path]:
    """Split a long recording into chunks no longer than ``max_seconds``.

    Splits land on detected silences when possible so a sentence is never cut in half, which
    measurably improves transcription accuracy on the chunk boundaries.
    """
    info = probe(path)
    if info.duration_s <= max_seconds:
        return [Path(path)]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cut_points: list[float] = [0.0]
    if prefer_silence:
        silences = detect_silences(path)
        for start, end in silences:
            midpoint = (start + end) / 2
            if midpoint - cut_points[-1] >= max_seconds * 0.6:
                if midpoint < info.duration_s - 5:
                    cut_points.append(midpoint)
            if cut_points[-1] and info.duration_s - cut_points[-1] <= max_seconds:
                break
    # Top up with even cuts if silence detection did not find enough boundaries.
    position = cut_points[-1]
    while info.duration_s - position > max_seconds:
        position += max_seconds
        cut_points.append(position)
    cut_points.append(info.duration_s)

    chunks: list[Path] = []
    for index in range(len(cut_points) - 1):
        start = cut_points[index]
        length = cut_points[index + 1] - start
        if length <= 0.5:
            continue
        target = out_dir / f"{Path(path).stem}_part{index + 1:02d}.wav"
        extract_audio(path, target, start=start, duration=length)
        chunks.append(target)
    return chunks


# --- audio curator helpers ------------------------------------------------------------


def trim(path: str | Path, out_path: str | Path, *, start: float = 0.0, end: float | None = None) -> Path:
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    args = ["-ss", f"{start:.3f}", "-i", str(path)]
    if end is not None:
        args += ["-t", f"{max(0.0, end - start):.3f}"]
    args += ["-c", "copy", str(target)]
    try:
        _run(args)
    except FFmpegError:
        # Some containers cannot stream-copy a cut; re-encode instead.
        args = ["-ss", f"{start:.3f}", "-i", str(path)]
        if end is not None:
            args += ["-t", f"{max(0.0, end - start):.3f}"]
        args += [str(target)]
        _run(args)
    return target


def normalise_lufs(path: str | Path, out_path: str | Path, *, target_lufs: float = DEFAULT_LUFS) -> tuple[Path, float]:
    """Normalise loudness to ``target_lufs`` (EBU R128). Returns (path, achieved_lufs).

    Games routinely ship audio that is wildly inconsistent between sources; -14 LUFS is the
    streaming-standard target the specification asks for.
    """
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tool = ffmpeg_path()
    if not tool:
        raise FFmpegUnavailable(describe()["install_hint"])
    measure = subprocess.run(  # noqa: S603
        [tool, "-hide_banner", "-nostdin", "-i", str(path), "-af", f"loudnorm=I={target_lufs}:print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, timeout=600, check=False,
    )
    achieved = target_lufs
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", measure.stderr or "", re.DOTALL)
    if match:
        try:
            achieved = float(json.loads(match.group(0)).get("output_i", target_lufs))
        except (json.JSONDecodeError, TypeError, ValueError):
            achieved = target_lufs
    _run([
        "-i", str(path),
        "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
        "-ar", "44100",
        str(target),
    ])
    return target, achieved


def to_ogg(path: str | Path, out_path: str | Path, *, quality: int = 5) -> Path:
    """Convert to Ogg Vorbis, Godot's preferred compressed audio format."""
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _run(["-i", str(path), "-c:a", "libvorbis", "-qscale:a", str(quality), str(target)])
    return target


def peak_normalise(path: str | Path, out_path: str | Path, *, target_db: float = -3.0) -> Path:
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _run(["-i", str(path), "-af", f"volume={target_db}dB:precision=float", str(target)])
    return target


def fade(path: str | Path, out_path: str | Path, *, fade_in_s: float = 0.01, fade_out_s: float = 0.02) -> Path:
    """Short fades prevent the click that a hard cut produces in most engines."""
    info = probe(path)
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    filters = []
    if fade_in_s > 0:
        filters.append(f"afade=t=in:st=0:d={fade_in_s}")
    if fade_out_s > 0 and info.duration_s > fade_out_s:
        filters.append(f"afade=t=out:st={max(0.0, info.duration_s - fade_out_s):.3f}:d={fade_out_s}")
    args = ["-i", str(path)]
    if filters:
        args += ["-af", ",".join(filters)]
    args += [str(target)]
    _run(args)
    return target


def loop_ready(
    path: str | Path, out_path: str | Path, *, crossfade_s: float = 1.5, loop_length_s: float | None = None
) -> Path:
    """Make a track loop cleanly by crossfading its tail into its head.

    A naive loop of a musical track clicks or drops a beat; this is the standard fix.
    """
    info = probe(path)
    length = loop_length_s or info.duration_s
    if length <= crossfade_s * 2:
        return to_ogg(path, out_path)
    filter_complex = (
        f"[0:a]atrim=0:{length - crossfade_s:.3f},asetpts=PTS-STARTPTS[head];"
        f"[0:a]atrim={length - crossfade_s:.3f}:{length:.3f},asetpts=PTS-STARTPTS[tail];"
        f"[head][tail]acrossfade=d={crossfade_s}:c1=tri:c2=tri[out]"
    )
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _run(["-i", str(path), "-filter_complex", filter_complex, "-map", "[out]", str(target)])
    return target


def convert_for_game(
    path: str | Path,
    out_path: str | Path,
    *,
    kind: str = "sfx",
    loop: bool = False,
    target_lufs: float = DEFAULT_LUFS,
) -> dict[str, Any]:
    """One call that does the whole curation chain and reports what it did.

    This is the function the Audio Curator agent uses, so the FFmpeg detail lives in one
    place and the agent's job stays about taste and licensing.
    """
    source = Path(path)
    target = Path(out_path)
    steps: list[str] = []
    info_before = probe(source)

    working = source
    if kind == "sfx":
        working = fade(working, target.parent / f".tmp_fade_{source.name}", fade_in_s=0.005, fade_out_s=0.03)
        steps.append("fade in/out to remove clicks")

    if kind == "bgm" and loop:
        working = loop_ready(working, target.parent / f".tmp_loop_{source.name}", crossfade_s=1.5)
        steps.append("crossfaded the tail into the head for a seamless loop")

    normalised, achieved = normalise_lufs(working, target.parent / f".tmp_norm_{source.name}", target_lufs=target_lufs)
    steps.append(f"normalised to {achieved:.1f} LUFS (target {target_lufs})")

    final = to_ogg(normalised, target)
    steps.append("encoded to Ogg Vorbis")

    for leftover in target.parent.glob(f".tmp_*{source.stem}*"):
        try:
            leftover.unlink()
        except OSError:  # pragma: no cover
            pass

    info_after = probe(final)
    return {
        "path": str(final),
        "steps": steps,
        "duration_before_s": round(info_before.duration_s, 2),
        "duration_after_s": round(info_after.duration_s, 2),
        "lufs": round(achieved, 1),
        "size_bytes": final.stat().st_size if final.exists() else 0,
    }


# --- tester helpers -------------------------------------------------------------------


def extract_frames(
    video_path: str | Path, out_dir: str | Path, *, times: Sequence[float] = (), fps: float = 0.0, max_frames: int = 12
) -> list[Path]:
    """Pull frames from a recorded gameplay clip (used when a run was captured as video)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames: list[Path] = []
    info = probe(video_path)
    if times:
        moments = list(times)[:max_frames]
    elif fps > 0:
        step = 1.0 / fps
        moments = [step * index for index in range(1, min(max_frames, int(info.duration_s / step)) + 1)]
    else:
        count = min(max_frames, 8)
        moments = [info.duration_s * (index + 1) / (count + 1) for index in range(count)]
    for index, moment in enumerate(moments, start=1):
        target = out_dir / f"frame_{index:03d}.png"
        try:
            _run(["-ss", f"{moment:.3f}", "-i", str(video_path), "-frames:v", "1", str(target)])
            frames.append(target)
        except FFmpegError as exc:
            log.warning("Frame extraction at %.2fs failed: %s", moment, exc)
    return frames


def record_window_hint() -> str:
    """Advice shown by the Tester when it wants a video instead of stills."""
    return (
        "To capture gameplay video, run Godot windowed and use your OS recorder, then point "
        "the Tester at the file. FFmpeg will extract frames for review automatically."
    )
