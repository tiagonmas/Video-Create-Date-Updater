#!/usr/bin/env python3
"""Update video creation timestamps with ExifTool for Immich."""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


QUICKTIME_EXTENSIONS = {".mp4", ".mov", ".m4v", ".3gp", ".3g2", ".qt"}
SIDECAR_ONLY_EXTENSIONS = {".divx"}
SUPPORTED_EXTENSIONS = QUICKTIME_EXTENSIONS | SIDECAR_ONLY_EXTENSIONS
IMMICH_DATE_PRIORITY = (
    "SubSecDateTimeOriginal",
    "SubSecCreateDate",
    "DateTimeOriginal",
    "CreationDate",
    "CreateDate",
    "SubSecMediaCreateDate",
    "MediaCreateDate",
    "DateTimeCreated",
    "GPSDateTime",
    "DateTimeUTC",
    "SonyDateTime2",
    "SourceImageCreateTime",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Set local MP4/QuickTime creation dates or create/update an XMP sidecar "
            "for DivX videos. Dates use the computer's local timezone."
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
        help="read current dates and show planned changes without writing to any files",
    )
    parser.add_argument(
        "-confirm",
        dest="confirm",
        action="store_true",
        default=True,
        help="ask before writing changes (default)",
    )
    parser.add_argument(
        "-no-confirm",
        dest="confirm",
        action="store_false",
        help="skip the confirmation prompt and apply changes immediately",
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
            "3GP, 3G2, QT, DIVX): " + ", ".join(unsupported)
        )
    if not videos:
        raise ValueError(f"no supported local video files matched: {pattern}")
    return videos


def exiftool_commands(video: Path, utc_date: str, local_date: str) -> list[list[str]]:
    sidecar = Path(f"{video}.xmp")
    if video.suffix.lower() in SIDECAR_ONLY_EXTENSIONS:
        if sidecar.is_symlink():
            raise ValueError(f"refusing to write through a sidecar symlink: {sidecar.name}")
        date_args = [
            f"-XMP-exif:DateTimeOriginal={local_date}",
            f"-XMP-photoshop:DateCreated={local_date}",
            f"-XMP-xmp:CreateDate={local_date}",
        ]
        if sidecar.exists():
            return [[
                "exiftool",
                "-overwrite_original",
                "-P",
                *date_args,
                str(sidecar),
            ]]
        return [["exiftool", "-o", str(sidecar), *date_args]]

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


def read_date_tags(path: Path, exiftool: str) -> list[tuple[str, str]]:
    result = subprocess.run(
        [exiftool, "-j", "-G1", "-a", "-s", "-time:all", "-api", "QuickTimeUTC=1", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"ExifTool could not read {path.name}: {detail}")

    try:
        records = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"ExifTool returned invalid metadata for {path.name}") from error

    dates: list[tuple[str, str]] = []
    for record in records:
        for qualified_name, value in record.items():
            tag_name = qualified_name.rsplit(":", 1)[-1]
            if tag_name not in IMMICH_DATE_PRIORITY or not isinstance(value, (str, int, float)):
                continue
            rendered = str(value).strip()
            if not rendered or rendered.startswith("0000:00:00"):
                continue
            dates.append((qualified_name, rendered))
    return dates


def describe_changes(video: Path, utc_date: str, local_date: str, exiftool: str) -> None:
    sidecar = Path(f"{video}.xmp")
    if video.suffix.lower() in SIDECAR_ONLY_EXTENSIONS and sidecar.is_symlink():
        raise RuntimeError(f"refusing to write through a sidecar symlink: {sidecar.name}")
    print(f"\nFile: {video.name}")
    date_sources: list[tuple[str, str, str]] = []
    if sidecar.is_file() and not sidecar.is_symlink():
        date_sources.extend(("sidecar", name, value) for name, value in read_date_tags(sidecar, exiftool))
    date_sources.extend(("video", name, value) for name, value in read_date_tags(video, exiftool))

    best = None
    for candidate_name in IMMICH_DATE_PRIORITY:
        best = next((entry for entry in date_sources if entry[1].rsplit(":", 1)[-1] == candidate_name), None)
        if best:
            break
    print("  CURRENT DATE (best Immich candidate)")
    if best:
        source, name, value = best
        print(f"    Date:   {value}")
        print(f"    Source: {name} in the {source}")
    else:
        print("    No recognized date is currently set in metadata.")

    other_dates = list(dict.fromkeys(entry for entry in date_sources if entry != best))
    if other_dates:
        print("  Other recognized dates currently stored:")
        for source, name, value in other_dates:
            print(f"    {value} — {name} in the {source}")

    print("  NEW DATE (requested)")
    print(f"    Local time: {local_date}")

    if video.suffix.lower() in SIDECAR_ONLY_EXTENSIONS:
        print("  CHANGES TO APPLY")
        action = "update" if sidecar.exists() else "create"
        print(f"  Will {action} {sidecar.name} with:")
        print(f"    XMP-exif:DateTimeOriginal = {local_date}")
        print(f"    XMP-photoshop:DateCreated = {local_date}")
        print(f"    XMP-xmp:CreateDate = {local_date}")
        print("  The video file itself will not be changed.")
    else:
        print(f"    QuickTime UTC storage value: {utc_date}")
        print("  CHANGES TO APPLY")
        print("  Will update the video with:")
        print(f"    QuickTime Create/Modify, Track Create/Modify, and Media Create/Modify dates = {utc_date} UTC")
        print(f"    QuickTime Keys:CreationDate and embedded XMP date fields = {local_date}")
        if sidecar.is_file() and not sidecar.is_symlink():
            print(f"  Will also update the date fields in {sidecar.name} = {local_date}")


def confirm_changes() -> bool:
    try:
        answer = input("Apply these changes? [y/N]: ").strip().casefold()
    except EOFError:
        answer = ""
    return answer in {"y", "yes"}


def main() -> int:
    options = parse_args()
    try:
        utc_date, local_date = resolve_date(options.date)
        videos = find_videos(options.file)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    exiftool = shutil.which("exiftool")
    if exiftool is None:
        print("error: ExifTool is required; install it and ensure 'exiftool' is on PATH", file=sys.stderr)
        return 2

    try:
        for video in videos:
            describe_changes(video, utc_date, local_date, exiftool)
    except RuntimeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    if options.dry_run:
        print("\nDRY RUN — no metadata was written")
        return 0

    if options.confirm and not confirm_changes():
        print("Cancelled — no files were changed.")
        return 0

    failed = False
    for video in videos:
        for command in exiftool_commands(video, utc_date, local_date):
            command[0] = exiftool
            result = subprocess.run(command, check=False)
            failed |= result.returncode != 0
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
