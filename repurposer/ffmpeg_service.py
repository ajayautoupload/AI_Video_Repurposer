from pathlib import Path
import subprocess


def get_video_duration(video_path):
    """
    Get the total video duration in seconds
    using FFprobe.
    """

    video_path = Path(video_path)

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
    )

    duration = result.stdout.strip()

    if not duration:
        raise ValueError(
            "Could not determine video duration."
        )

    return float(duration)


def convert_to_mp4(input_path, output_path):
    """
    Convert a video to MP4 using FFmpeg.

    Video:
        H.264

    Audio:
        AAC
    """

    input_path = Path(input_path)
    output_path = Path(output_path)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    command = [
        "ffmpeg",
        "-y",

        "-i",
        str(input_path),

        "-map",
        "0:v:0",

        "-map",
        "0:a?",

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-crf",
        "23",

        "-pix_fmt",
        "yuv420p",

        "-profile:v",
        "high",

        "-level",
        "4.0",

        "-r",
        "30",

        "-c:a",
        "aac",

        "-b:a",
        "128k",

        "-ar",
        "48000",

        "-ac",
        "2",

        "-movflags",
        "+faststart",

        str(output_path),
    ]

    subprocess.run(
        command,
        check=True,
    )

    return str(output_path)


def create_clip(
    input_path,
    output_path,
    start_time,
    end_time
):
    """
    Create a video clip between start_time
    and end_time using FFmpeg.
    """

    input_path = Path(input_path)
    output_path = Path(output_path)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    start_time = float(start_time)
    end_time = float(end_time)

    if start_time < 0:
        raise ValueError(
            "start_time cannot be negative."
        )

    if end_time <= start_time:
        raise ValueError(
            "end_time must be greater than start_time."
        )

    duration = end_time - start_time

    command = [
        "ffmpeg",
        "-y",

        "-ss",
        str(start_time),

        "-i",
        str(input_path),

        "-t",
        str(duration),

        "-map",
        "0:v:0",

        "-map",
        "0:a?",

        "-c:v",
        "libx264",

        "-preset",
        "veryfast",

        "-crf",
        "23",

        "-pix_fmt",
        "yuv420p",

        "-profile:v",
        "high",

        "-level",
        "4.0",

        "-r",
        "30",

        "-c:a",
        "aac",

        "-b:a",
        "128k",

        "-ar",
        "48000",

        "-ac",
        "2",

        "-movflags",
        "+faststart",

        str(output_path),
    ]

    subprocess.run(
        command,
        check=True,
    )

    return str(output_path)


def create_formatted_clip(
    input_path,
    output_path,
    output_format="vertical",
    start_time=None,
    end_time=None
):
    """
    Create a video clip in vertical or horizontal format.

    vertical:
        1080x1920

    horizontal:
        1920x1080

    If start_time and end_time are provided,
    only that part of the source video is processed.

    If they are not provided,
    the complete source video is processed.
    """

    input_path = Path(input_path)
    output_path = Path(output_path)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    # --------------------------------
    # Output dimensions
    # --------------------------------

    if output_format == "vertical":

        width = 1080
        height = 1920

    elif output_format == "horizontal":

        width = 1920
        height = 1080

    else:

        raise ValueError(
            "Invalid output format. "
            "Use 'vertical' or 'horizontal'."
        )

    # --------------------------------
    # Validate clip timing
    # --------------------------------

    clip_duration = None

    if start_time is not None:

        start_time = float(
            start_time
        )

        if start_time < 0:
            raise ValueError(
                "start_time cannot be negative."
            )

    if end_time is not None:

        end_time = float(
            end_time
        )

        if end_time <= 0:
            raise ValueError(
                "end_time must be greater than zero."
            )

    if (
        start_time is not None
        and end_time is not None
    ):

        if end_time <= start_time:
            raise ValueError(
                "end_time must be greater than "
                "start_time."
            )

        clip_duration = (
            end_time - start_time
        )

    # --------------------------------
    # Video crop/scale
    # --------------------------------

    video_filter = (
        f"scale={width}:{height}:"
        "force_original_aspect_ratio=increase,"
        f"crop={width}:{height},"
        "setsar=1"
    )

    # --------------------------------
    # FFmpeg command
    # --------------------------------

    command = [
        "ffmpeg",
        "-y",
    ]

    # --------------------------------
    # Start time
    # --------------------------------

    if start_time is not None:

        command.extend(
            [
                "-ss",
                str(start_time),
            ]
        )

    # --------------------------------
    # Input
    # --------------------------------

    command.extend(
        [
            "-i",
            str(input_path),
        ]
    )

    # --------------------------------
    # Clip duration
    # --------------------------------

    if clip_duration is not None:

        command.extend(
            [
                "-t",
                str(clip_duration),
            ]
        )

    # --------------------------------
    # Video processing
    # --------------------------------

    command.extend(
        [
            "-vf",
            video_filter,

            "-map",
            "0:v:0",

            "-map",
            "0:a?",

            "-c:v",
            "libx264",

            "-preset",
            "veryfast",

            "-crf",
            "23",

            "-pix_fmt",
            "yuv420p",

            "-profile:v",
            "high",

            "-level",
            "4.0",

            "-r",
            "30",

            "-c:a",
            "aac",

            "-b:a",
            "128k",

            "-ar",
            "48000",

            "-ac",
            "2",

            "-movflags",
            "+faststart",

            str(output_path),
        ]
    )

    subprocess.run(
        command,
        check=True,
    )

    return str(output_path)