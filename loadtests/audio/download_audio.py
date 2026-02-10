#!/usr/bin/env python3
"""
Download LibriSpeech dev-clean audio clips and convert to PCM for load testing.

Downloads ~20 clips (5-15s, diverse speakers) from the LibriSpeech dev-clean
dataset, converts FLAC to raw PCM (16-bit LE, 16kHz mono), and saves them
to the clips/ directory with a manifest.json.

Usage:
    python loadtests/audio/download_audio.py

Note: www.openslr.org may be blocked by the dev container firewall.
      Run this script on the host machine if needed, then copy clips/ in.
"""

import json
import os
import struct
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

LIBRISPEECH_URL = "https://www.openslr.org/resources/12/dev-clean.tar.gz"
SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2  # 16-bit
MIN_DURATION_S = 5.0
MAX_DURATION_S = 15.0
TARGET_CLIPS = 20
MAX_PER_SPEAKER = 3

SCRIPT_DIR = Path(__file__).parent
CLIPS_DIR = SCRIPT_DIR / "clips"
MANIFEST_PATH = SCRIPT_DIR / "manifest.json"


def check_connectivity() -> bool:
    """Check if we can reach the download server."""
    try:
        req = urllib.request.Request(LIBRISPEECH_URL, method="HEAD")
        urllib.request.urlopen(req, timeout=10)
        return True
    except (urllib.error.URLError, OSError) as e:
        print(f"ERROR: Cannot reach {LIBRISPEECH_URL}")
        print(f"  Reason: {e}")
        print()
        print("This is likely due to the dev container firewall.")
        print("Options:")
        print("  1. Run this script on the host machine (outside the container)")
        print("  2. Add www.openslr.org to the firewall allowlist")
        print("  3. Manually download and extract clips to loadtests/audio/clips/")
        return False


def parse_transcripts(tar: tarfile.TarFile) -> dict[str, str]:
    """Parse all .trans.txt files from the tarball into {utterance_id: transcript}."""
    transcripts = {}
    for member in tar.getmembers():
        if member.name.endswith(".trans.txt"):
            f = tar.extractfile(member)
            if f is None:
                continue
            for line in f.read().decode("utf-8").strip().split("\n"):
                parts = line.split(" ", 1)
                if len(parts) == 2:
                    transcripts[parts[0]] = parts[1]
    return transcripts


def get_flac_candidates(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """Get all FLAC file members from the tarball."""
    return [m for m in tar.getmembers() if m.name.endswith(".flac")]


def extract_speaker_id(flac_path: str) -> str:
    """Extract speaker ID from LibriSpeech path like LibriSpeech/dev-clean/1272/128104/1272-128104-0000.flac."""
    filename = os.path.basename(flac_path)
    return filename.split("-")[0]


def extract_utterance_id(flac_path: str) -> str:
    """Extract utterance ID from path (filename without extension)."""
    return os.path.splitext(os.path.basename(flac_path))[0]


def flac_to_pcm(flac_bytes: bytes) -> tuple[bytes, float]:
    """Convert FLAC audio bytes to raw PCM (16-bit LE, 16kHz mono).

    Returns (pcm_bytes, duration_seconds).
    """
    try:
        import soundfile as sf
    except ImportError:
        print("ERROR: soundfile package not installed.")
        print("Install it with: uv add --group dev soundfile")
        sys.exit(1)

    import io

    import numpy as np

    data, sample_rate = sf.read(io.BytesIO(flac_bytes), dtype="int16")

    # Resample if needed (LibriSpeech is already 16kHz, but just in case)
    if sample_rate != SAMPLE_RATE:
        # Simple decimation/interpolation - good enough for load testing
        ratio = SAMPLE_RATE / sample_rate
        new_length = int(len(data) * ratio)
        indices = np.linspace(0, len(data) - 1, new_length).astype(int)
        data = data[indices]

    # Convert to mono if stereo
    if data.ndim > 1:
        data = data[:, 0]

    # Ensure int16
    pcm_bytes = data.astype(np.int16).tobytes()
    duration_s = len(data) / SAMPLE_RATE
    return pcm_bytes, duration_s


def download_and_extract():
    """Download LibriSpeech dev-clean and extract clips."""
    if not check_connectivity():
        sys.exit(1)

    CLIPS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Downloading LibriSpeech dev-clean from {LIBRISPEECH_URL}...")
    print("This is ~337MB and may take a few minutes.")

    # Download to temp file
    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        urllib.request.urlretrieve(LIBRISPEECH_URL, tmp_path, _report_progress)
        print("\nDownload complete.")

        print("Parsing tarball...")
        with tarfile.open(tmp_path, "r:gz") as tar:
            # Parse transcripts first
            transcripts = parse_transcripts(tar)
            print(f"  Found {len(transcripts)} transcripts")

            # Get FLAC candidates
            flac_members = get_flac_candidates(tar)
            print(f"  Found {len(flac_members)} FLAC files")

            # Select diverse clips
            selected = select_clips(tar, flac_members, transcripts)
            print(f"  Selected {len(selected)} clips")

            # Convert and save
            manifest = []
            for i, (member, transcript) in enumerate(selected):
                utterance_id = extract_utterance_id(member.name)
                speaker_id = extract_speaker_id(member.name)

                f = tar.extractfile(member)
                if f is None:
                    continue

                pcm_bytes, duration_s = flac_to_pcm(f.read())

                pcm_filename = f"{utterance_id}.pcm"
                pcm_path = CLIPS_DIR / pcm_filename
                pcm_path.write_bytes(pcm_bytes)

                manifest.append(
                    {
                        "filename": pcm_filename,
                        "duration_s": round(duration_s, 2),
                        "transcript": transcript,
                        "speaker_id": speaker_id,
                        "utterance_id": utterance_id,
                    }
                )
                print(f"  [{i + 1}/{len(selected)}] {pcm_filename} ({duration_s:.1f}s, speaker {speaker_id})")

        # Write manifest
        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
        print(f"\nManifest written to {MANIFEST_PATH}")
        print(f"PCM clips saved to {CLIPS_DIR}/")
        print(f"Total clips: {len(manifest)}")

    finally:
        # Clean up tarball
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
            print("Cleaned up temporary tarball.")


def select_clips(
    tar: tarfile.TarFile,
    flac_members: list[tarfile.TarInfo],
    transcripts: dict[str, str],
) -> list[tuple[tarfile.TarInfo, str]]:
    """Select ~TARGET_CLIPS FLAC files with diverse speakers and appropriate duration."""
    import random

    random.shuffle(flac_members)

    selected = []
    speaker_counts: dict[str, int] = {}

    for member in flac_members:
        if len(selected) >= TARGET_CLIPS:
            break

        utterance_id = extract_utterance_id(member.name)
        speaker_id = extract_speaker_id(member.name)

        # Skip if no transcript
        if utterance_id not in transcripts:
            continue

        # Limit per speaker
        if speaker_counts.get(speaker_id, 0) >= MAX_PER_SPEAKER:
            continue

        # Check duration by estimating from file size
        # FLAC compression ratio ~0.6 for speech, so estimate uncompressed size
        estimated_samples = member.size / 0.6 / BYTES_PER_SAMPLE
        estimated_duration = estimated_samples / SAMPLE_RATE

        if estimated_duration < MIN_DURATION_S or estimated_duration > MAX_DURATION_S:
            continue

        selected.append((member, transcripts[utterance_id]))
        speaker_counts[speaker_id] = speaker_counts.get(speaker_id, 0) + 1

    return selected


def _report_progress(block_num: int, block_size: int, total_size: int):
    """Progress callback for urlretrieve."""
    downloaded = block_num * block_size
    if total_size > 0:
        pct = min(100, downloaded * 100 // total_size)
        mb_downloaded = downloaded / (1024 * 1024)
        mb_total = total_size / (1024 * 1024)
        print(f"\r  {mb_downloaded:.1f}/{mb_total:.1f} MB ({pct}%)", end="", flush=True)


if __name__ == "__main__":
    download_and_extract()
