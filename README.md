# Video Create Date Updater

A small Python command-line tool that updates the embedded creation date metadata in local MP4/QuickTime video files using [ExifTool](https://exiftool.org/). It also updates a matching `.xmp` sidecar when one exists. For DivX files, which this tool does not edit in place, it creates or updates an XMP sidecar only.

## Why these fields

Immich checks embedded and sidecar date tags in priority order. Its [metadata service](https://github.com/immich-app/immich/blob/main/server/src/services/metadata.service.ts) checks `DateTimeOriginal`, `CreationDate`, `CreateDate`, and `MediaCreateDate` among the date candidates; a valid XMP sidecar date takes precedence over dates read from the video. The tool therefore writes:

- QuickTime `CreateDate`, `TrackCreateDate`, and `MediaCreateDate`, plus their corresponding modify dates. The QuickTime integer timestamps are stored as UTC.
- QuickTime `Keys:CreationDate`, which carries the local date with its UTC offset.
- `XMP-exif:DateTimeOriginal`, `XMP-photoshop:DateCreated`, and `XMP-xmp:CreateDate` in the movie and, when present, its `.xmp` sidecar.

The chosen capture date is consequently available through the higher-priority tags Immich reads. The filesystem modification date is preserved (`-P`) and is not treated as the capture date. ExifTool documents that QuickTime integer date tags are stored as UTC; `QuickTimeUTC` is enabled to write those values consistently. See the [ExifTool QuickTime tag documentation](https://exiftool.org/TagNames/QuickTime.html).

For DivX, the tool writes these XMP date properties to `VIDEO.divx.xmp` and leaves the video unchanged. Immich recognizes the `VIDEO.ext.xmp` naming form and reads `exif:DateTimeOriginal` before the other supported XMP date fields. See Immich's [XMP sidecar documentation](https://docs.immich.app/features/xmp-sidecars/).

## Requirements

- Python 3.10 or newer (standard library only)
- ExifTool installed and available as `exiftool` on `PATH`

## Usage

Change to the folder containing the videos. `-file` accepts a filename or a quoted wildcard, and it only matches files directly in the current folder:

```sh
cd /storage/onedrive/Videos
python3 ~/repos/video-create-date-updater/update_video_create_date.py \
  -file 'Alorna*.mp4' -date '1983-05-14 16:30:00' -dry-run
```

For a DivX video, preview creation of its sidecar (substitute its correct date):

```sh
cd /storage/onedrive/Videos/Home
python3 ~/repos/video-create-date-updater/update_video_create_date.py \
  -file 'Cassete16wmm.divx' -date 'YYYY-MM-DD HH:MM:SS' -dry-run
```

Remove `-dry-run` to create `Cassete16wmm.divx.xmp`. This changes only the sidecar, not the DivX file. If Immich already has this video in an external library, run the Sidecar Metadata **Discover** job and then **Sync** so it associates and reads the new sidecar.

When the preview is correct, run the same command without `-dry-run` to write the dates:

```sh
python3 ~/repos/video-create-date-updater/update_video_create_date.py \
  -file 'Alorna*.mp4' -date '1983-05-14 16:30:00'
```

`-date` is a local wall-clock time in the computer's configured timezone. Quote the value so the shell passes it as one argument. To see all arguments:

```sh
python3 ~/repos/video-create-date-updater/update_video_create_date.py -h
```

## Arguments

| Argument | Description |
| --- | --- |
| `-h`, `--help` | Show help. |
| `-file PATTERN` | Required filename or wildcard. Only files in the current folder are considered; paths, subfolders, and symlinks are rejected or skipped. Quote wildcard patterns. |
| `-date 'YYYY-MM-DD HH:MM:SS'` | Required correct capture date and time, interpreted in the computer's local timezone. |
| `-dry-run` | List the planned video and sidecar targets and values. It does not open media files or invoke ExifTool, so it cannot change media contents or filesystem timestamps. |

Supported video extensions are `.mp4`, `.mov`, `.m4v`, `.3gp`, `.3g2`, `.qt`, and `.divx`. QuickTime formats are edited in place with ExifTool's backup disabled; keep a separate backup before applying bulk changes. DivX files use a sidecar-only update and the video itself is left untouched.
