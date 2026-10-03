from pathlib import Path

from .ffmpeg_service import create_formatted_clip


def create_normal_segments(
    duration,
    clip_length=60
):
    """
    Create sequential video segments.

    Args:
        duration:
            Total video duration in seconds.

        clip_length:
            Length of each clip in seconds.

    Returns:
        list:
            Video segments with start and end times.
    """

    segments = []

    start = 0

    while start < duration:

        end = min(
            start + clip_length,
            duration
        )

        segments.append(
            {
                "start": start,
                "end": end,
            }
        )

        start = end

    return segments


def create_normal_clips(
    input_path,
    segments,
    output_directory,
    output_format="vertical"
):
    """
    Create actual video clips from
    normal mode segments.

    Each segment is converted into the
    selected output format.
    """

    input_path = Path(
        input_path
    )

    output_directory = Path(
        output_directory
    )

    output_directory.mkdir(
        parents=True,
        exist_ok=True
    )

    created_clips = []

    for index, segment in enumerate(
        segments,
        start=1
    ):

        output_path = (
            output_directory
            / f"normal_clip_{index}.mp4"
        )

        clip_path = create_formatted_clip(
            input_path=input_path,
            output_path=output_path,
            output_format=output_format,
            start_time=segment["start"],
            end_time=segment["end"],
        )

        created_clips.append(
            {
                "number": index,
                "path": clip_path,
                "start": segment["start"],
                "end": segment["end"],
                "duration": round(
                    segment["end"]
                    - segment["start"],
                    2
                ),
            }
        )

    return created_clips