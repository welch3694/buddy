"""
Standalone helper: extract a Buddy voice profile from an OpenSLR/LibriSpeech .flac.

Not part of the app — kept under .cursor/tmp/ (gitignored). Run with:

  .\\.venv\\Scripts\\python.exe .cursor\\tmp\\extract_openslr_voice.py

Paste a Windows "Copy as path" .flac path. Pocket TTS clones that sample and
plays a preview so you can judge quality before naming/saving. Speaker first
name is suggested from SPEAKERS.TXT (override allowed). Quit with q / quit / exit.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sys
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
VOICES_DIR = REPO_ROOT / "voices"
PREVIEW_DIR = Path(__file__).resolve().parent / "voice_previews"

# Fixed line so clone quality is comparable across candidates.
PREVIEW_TEXT = (
    "Hi this is some preview text for a voice clone. It's a test of the voice quality and how it sounds."
)
PREVIEW_MAX_TOKENS = 120

LICENSE_TEMPLATE = """\
https://www.openslr.org/12/

LibriSpeech ASR corpus

Identifier: SLR12

{source_path}

Summary: Large-scale (1000 hours) corpus of read English speech

Category: Speech

License: CC BY 4.0

https://creativecommons.org/licenses/by/4.0/deed.en
"""

_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
# SPEAKERS.TXT: "14   | F | train-clean-360  | 25.03 | Kristin LeMoine"
_SPEAKER_LINE = re.compile(
    r"^\s*(\d+)\s*\|\s*[MF]\s*\|\s*[^|]+\|\s*[^|]+\|\s*(.+?)\s*$",
    re.IGNORECASE,
)

_pocket_model: Any | None = None


def _pick_device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def get_pocket_model() -> Any:
    """Load Pocket TTS once and keep it warm across previews."""
    global _pocket_model
    if _pocket_model is not None:
        return _pocket_model

    # Quiet the library's chatty startup logs in this interactive tool.
    logging.getLogger("pocket_tts").setLevel(logging.WARNING)
    logging.getLogger("pocket_tts.models.tts_model").setLevel(logging.WARNING)
    logging.getLogger("pocket_tts.utils.utils").setLevel(logging.WARNING)

    from pocket_tts import TTSModel

    device = _pick_device()
    print(f"  Loading Pocket TTS (device={device})… first load can take a bit.")
    t0 = time.perf_counter()
    model = TTSModel.load_model()
    if device == "cuda":
        model = model.cuda()
    elif device != "cpu":
        model = model.to(device)
    _pocket_model = model
    print(f"  Pocket TTS ready in {time.perf_counter() - t0:.1f}s.")
    return _pocket_model


def write_preview_wav(audio_tensor: Any, sample_rate: int, dest: Path) -> Path:
    import numpy as np
    from scipy.io import wavfile

    audio = audio_tensor.detach().float().cpu().numpy()
    if audio.ndim > 1:
        # Pocket returns [channels, samples]; mono-mix if needed.
        audio = audio.mean(axis=0) if audio.shape[0] > 1 else audio.squeeze(0)
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    dest.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(dest), sample_rate, pcm)
    return dest


def play_wav(path: Path) -> None:
    """Open with the OS default player so you can scrub / replay."""
    resolved = path.resolve()
    try:
        os.startfile(resolved)  # type: ignore[attr-defined]
    except AttributeError:
        # Non-Windows fallback
        import subprocess

        if sys.platform == "darwin":
            subprocess.Popen(["open", str(resolved)])
        else:
            subprocess.Popen(["xdg-open", str(resolved)])


def generate_clone_preview(audio_path: Path) -> Path:
    """Clone from audio_path via Pocket TTS and write/play a preview wav."""
    model = get_pocket_model()
    print(f"  Conditioning on: {audio_path.name}")
    t0 = time.perf_counter()
    voice_state = model.get_state_for_audio_prompt(str(audio_path), truncate=True)
    print(f"  Voice state ready ({time.perf_counter() - t0:.1f}s). Synthesizing…")
    t1 = time.perf_counter()
    audio = model.generate_audio(
        voice_state,
        PREVIEW_TEXT,
        max_tokens=PREVIEW_MAX_TOKENS,
        copy_state=True,
    )
    duration = audio.shape[-1] / model.sample_rate
    print(f"  Generated {duration:.1f}s in {time.perf_counter() - t1:.1f}s.")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = PREVIEW_DIR / f"preview_{audio_path.stem}_{stamp}.wav"
    write_preview_wav(audio, model.sample_rate, dest)
    print(f"  Preview wav: {dest}")
    play_wav(dest)
    return dest


def normalize_pasted_path(raw: str) -> Path:
    """Strip Copy-as-path quotes/spaces and return a Path."""
    text = raw.strip()
    # PowerShell / Explorer often wrap in double quotes; sometimes single.
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1]
    text = text.strip()
    # Rare: escaped quotes left behind
    text = text.strip("\"'")
    return Path(text)


def sanitize_voice_id(name: str) -> str:
    cleaned = name.strip().lower().replace(" ", "_")
    cleaned = re.sub(r"[^a-z0-9_-]", "", cleaned)
    if not cleaned or not _SAFE_NAME.match(cleaned):
        raise ValueError(
            f"Invalid voice name {name!r}. Use letters, numbers, _ or - "
            "(must start with a letter or digit)."
        )
    return cleaned


def find_speakers_txt(audio_path: Path) -> Path | None:
    """Walk up from the audio file until SPEAKERS.TXT is found (LibriSpeech root)."""
    for parent in audio_path.resolve().parents:
        candidate = parent / "SPEAKERS.TXT"
        if candidate.is_file():
            return candidate
    return None


@lru_cache(maxsize=4)
def load_speaker_names(speakers_txt: str) -> dict[str, str]:
    """Map speaker id (str) → full display name from SPEAKERS.TXT."""
    names: dict[str, str] = {}
    path = Path(speakers_txt)
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip() or line.lstrip().startswith(";"):
                continue
            match = _SPEAKER_LINE.match(line)
            if match:
                names[match.group(1)] = match.group(2).strip()
    return names


def speaker_id_from_audio(audio_path: Path) -> str | None:
    """LibriSpeech utt ids are speaker-chapter-index, e.g. 1034-121119-0000."""
    stem = audio_path.stem
    if "-" in stem:
        sid = stem.split("-", 1)[0]
        if sid.isdigit():
            return sid
    # Fallback: .../<speaker_id>/<chapter_id>/<file>.flac
    try:
        sid = audio_path.parent.parent.name
    except IndexError:
        return None
    return sid if sid.isdigit() else None


def first_name_from_full(full_name: str) -> str:
    """First whitespace token; for odd LibriVox tags like |CBW|Simon, use last segment."""
    token = full_name.strip().split()[0] if full_name.strip() else ""
    if "|" in token:
        parts = [p for p in token.split("|") if p]
        if parts:
            token = parts[-1]
    return token


def suggest_voice_name(audio_path: Path) -> tuple[str | None, str | None]:
    """
    Return (suggested_first_name, full_speaker_name) from SPEAKERS.TXT.
    Either may be None if lookup fails.
    """
    sid = speaker_id_from_audio(audio_path)
    if not sid:
        return None, None
    speakers_txt = find_speakers_txt(audio_path)
    if not speakers_txt:
        return None, None
    full = load_speaker_names(str(speakers_txt.resolve())).get(sid)
    if not full:
        return None, None
    first = first_name_from_full(full)
    return (first or None), full


def find_transcript_txt(audio_dir: Path) -> Path:
    txts = sorted(p for p in audio_dir.iterdir() if p.is_file() and p.suffix.lower() == ".txt")
    if not txts:
        raise FileNotFoundError(f"No .txt transcript found in {audio_dir}")
    if len(txts) > 1:
        names = ", ".join(p.name for p in txts)
        raise FileNotFoundError(
            f"Expected exactly one .txt in {audio_dir}, found {len(txts)}: {names}"
        )
    return txts[0]


def extract_ref_text(transcript_path: Path, audio_stem: str) -> str:
    """Find the line starting with the utterance id and return the spoken text."""
    prefix = audio_stem
    with transcript_path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            # LibriSpeech: "<utt_id> <words...>" (space or tab after id)
            if line == prefix:
                raise ValueError(f"Transcript line for {prefix!r} has no text after the id")
            if line.startswith(prefix) and len(line) > len(prefix) and line[len(prefix)].isspace():
                return line[len(prefix) :].strip()
    raise ValueError(
        f"No transcript line starting with {prefix!r} in {transcript_path.name}"
    )


def libri_relative_path(audio_path: Path) -> str:
    """
    Prefer LibriSpeech\\dev-clean\\... style path for license.txt.
    Fall back to the full path with backslashes.
    """
    parts = audio_path.resolve().parts
    lower = [p.lower() for p in parts]
    try:
        idx = lower.index("librispeech")
    except ValueError:
        return str(audio_path.resolve())
    rel = Path(*parts[idx:])
    return str(rel).replace("/", "\\")


def prompt(msg: str) -> str:
    try:
        return input(msg)
    except EOFError:
        print()
        return "q"


def process_one(audio_path: Path, voice_name: str) -> Path:
    if not audio_path.is_file():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    if audio_path.suffix.lower() != ".flac":
        raise ValueError(f"Expected a .flac file, got: {audio_path.suffix}")

    voice_id = sanitize_voice_id(voice_name)
    transcript_path = find_transcript_txt(audio_path.parent)
    ref_text = extract_ref_text(transcript_path, audio_path.stem)
    source_path = libri_relative_path(audio_path)

    dest_dir = VOICES_DIR / voice_id
    if dest_dir.exists():
        answer = prompt(f"  Folder already exists: {dest_dir}\n  Overwrite? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            raise RuntimeError("Aborted (voice folder already exists).")
        shutil.rmtree(dest_dir)

    dest_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(audio_path, dest_dir / "audio.flac")
    (dest_dir / "ref_text.txt").write_text(ref_text + "\n", encoding="utf-8")
    (dest_dir / "license.txt").write_text(
        LICENSE_TEMPLATE.format(source_path=source_path),
        encoding="utf-8",
    )
    return dest_dir


def prompt_voice_name(audio_path: Path) -> str | None:
    suggested, full_name = suggest_voice_name(audio_path)
    if full_name and suggested:
        print(f"  Speaker: {full_name}")
        entered = prompt(f"Voice name [{suggested}]> ").strip()
        return entered or suggested
    if suggested:
        entered = prompt(f"Voice name [{suggested}]> ").strip()
        return entered or suggested
    entered = prompt("Voice name> ").strip()
    return entered or None


def main() -> int:
    print("OpenSLR → Buddy voice extractor")
    print(f"Voices dir: {VOICES_DIR}")
    print(f"Previews:   {PREVIEW_DIR}")
    print("Paste a .flac path (Copy as path is fine). Empty / q / quit / exit to stop.")
    print("After each path, Pocket TTS plays a clone preview before you name/save.")
    print("Voice name defaults to the speaker's first name from SPEAKERS.TXT.")
    print("-" * 60)

    if not VOICES_DIR.is_dir():
        print(f"ERROR: voices directory not found: {VOICES_DIR}", file=sys.stderr)
        return 1

    while True:
        raw = prompt("\n.flac path> ")
        if raw.strip().lower() in ("", "q", "quit", "exit"):
            print("Bye.")
            break

        try:
            audio_path = normalize_pasted_path(raw)
            if not audio_path.is_file():
                raise FileNotFoundError(f"Audio file not found: {audio_path}")
            if audio_path.suffix.lower() != ".flac":
                raise ValueError(f"Expected a .flac file, got: {audio_path.suffix}")

            generate_clone_preview(audio_path)

            keep = prompt("Keep / save this voice? [y/N] ").strip().lower()
            if keep not in ("y", "yes"):
                print("  Skipped.")
                continue

            voice_name = prompt_voice_name(audio_path)
            if not voice_name:
                print("  Skipped (no voice name).")
                continue
            dest = process_one(audio_path, voice_name)
            print(f"  OK → {dest}")
            print(f"       audio.flac")
            print(f"       ref_text.txt")
            print(f"       license.txt")
        except Exception as exc:
            print(f"  ERROR: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
