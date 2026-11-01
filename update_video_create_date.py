#!/usr/bin/env python3
"""Update video creation timestamps with ExifTool for Immich."""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".m4v", ".3gp", ".3g2", ".qt"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Set the embedded creation dates of local MP4/QuickTime videos and their "
            "matching XMP sidecars. Dates are given in the computer's local timezone."
        ),
        allow_abbrev=False,
    )
    parser.add_argument(
        "-file",
        required=True,
        metavar="PATTERN",
        help="local filename or quoted wildcard pattern (matches this folder only)",
    )
    parser.add_argument(
        "-date",
        required=True,
        metavar="'YYYY-MM-DD HH:MM:SS'",
        help="correct capture date/time in the computer's local timezone",
    )
    parser.add_argument(
        "-dry-run",
        dest="dry_run",
        action="store_true",
        help="show planned targets and values without opening or changing media files",
    )
    return parser.parse_args()


def resolve_date(value: str) -> tuple[str, str]:
    try:
        naive = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError as error:
        raise ValueError("-date must use YYYY-MM-DD HH:MM:SS") from error

    local = naive.astimezone()
    utc = local.astimezone(timezone.utc)
    return (
        utc.strftime("%Y:%m:%d %H:%M:%S"),
        local.isoformat(timespec="seconds"),
    )


def find_videos(pattern: str) -> list[Path]:
    if os.path.isabs(pattern) or "/" in pattern or "\\" in pattern or ".." in pattern:
        raise ValueError("-file must be a filename or wildcard in the current folder")

    paths = [Path(item) for item in sorted(glob.glob(pattern, recursive=False))]
    videos: list[Path] = []
    unsupported: list[str] = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            unsupported.append(path.name)
        else:
            videos.append(path)

    if unsupported:
        raise ValueError(
            "matched files with unsupported extensions (supported: MP4, MOV, M4V, "
            "3GP, 3G2, QT): " + ", ".join(unsupported)
        )
    if not videos:
        raise ValueError(f"no supported local video files matched: {pattern}")
    return videos


def exiftool_commands(video: Path, utc_date: str, local_date: str) -> list[list[str]]:
    video_args = [
        "exiftool",
        "-overwrite_original",
        "-P",
        "-api",
        "LargeFileSupport=1",
        "-api",
        "QuickTimeUTC=1",
        f"-QuickTime:CreateDate={utc_date}",
        f"-QuickTime:ModifyDate={utc_date}",
        f"-QuickTime:TrackCreateDate={utc_date}",
        f"-QuickTime:TrackModifyDate={utc_date}",
        f"-QuickTime:MediaCreateDate={utc_date}",
        f"-QuickTime:MediaModifyDate={utc_date}",
        f"-QuickTime:Keys:CreationDate={local_date}",
        f"-XMP-exif:DateTimeOriginal={local_date}",
        f"-XMP-photoshop:DateCreated={local_date}",
        f"-XMP-xmp:CreateDate={local_date}",
        str(video),
    ]

    # Immich checks an XMP sidecar before embedded video dates. Keep a paired sidecar
    # aligned so its higher-priority DateTimeOriginal cannot mask the updated movie.
    sidecar = Path(f"{video}.xmp")
    if sidecar.is_file() and not sidecar.is_symlink():
        sidecar_args = [
            "exiftool",
            "-overwrite_original",
            "-P",
            "-api",
            "LargeFileSupport=1",
            f"-XMP-exif:DateTimeOriginal={local_date}",
            f"-XMP-photoshop:DateCreated={local_date}",
            f"-XMP-xmp:CreateDate={local_date}",
            str(sidecar),
        ]
        return [video_args, sidecar_args]
    return [video_args]


def main() -> int:
    options = parse_args()
    try:
        utc_date, local_date = resolve_date(options.date)
        videos = find_videos(options.file)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if options.dry_run:
        print("DRY RUN — no media files were opened or changed")
        print(f"Date in local timezone: {local_date}")
        print(f"QuickTime UTC date:    {utc_date}")
        for video in videos:
            sidecar = Path(f"{video}.xmp")
            suffix = f" and {sidecar.name}" if sidecar.is_file() and not sidecar.is_symlink() else ""
            print(f"Would update: {video.name}{suffix}")
        return 0

    exiftool = shutil.which("exiftool")
    if exiftool is None:
        print("error: ExifTool is required; install it and ensure 'exiftool' is on PATH", file=sys.stderr)
        return 2

    failed = False
    for video in videos:
        for command in exiftool_commands(video, utc_date, local_date):
            command[0] = exiftool
            result = subprocess.run(command, check=False)
            failed |= result.returncode != 0
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
