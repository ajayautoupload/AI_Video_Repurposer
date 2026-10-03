from pathlib import Path

from django.conf import settings

from .ffmpeg_service import (
    get_video_duration,
    create_formatted_clip,
)
from .youtube_service import download_youtube_video
from .whisper_service import transcribe_video
from .clip_analyzer import analyze_transcript
from .normal_processor import (
    create_normal_segments,
    create_normal_clips,
)


def update_processing_progress(
    video,
    progress,
    stage
):
    """
    Update processing progress and stage
    in the database.
    """

    video.processing_progress = max(
        0,
        min(progress, 100)
    )

    video.processing_stage = stage

    video.save(
        update_fields=[
            "processing_progress",
            "processing_stage",
            "updated_at",
        ]
    )


def process_video(video):
    """
    Process a Video instance.

    This function handles only video processing.

    Publishing to YouTube, Instagram, and Facebook
    is intentionally kept outside this service.

    IMPORTANT:
    When the user selects vertical output, the first
    generated clip is intentionally kept horizontal.
    The vertical compositor later converts that clip
    into the final 1080x1920 blurred-background format.

    This prevents the original 16:9 framing from being
    cropped away before the compositor runs.
    """

    try:

        # --------------------------------
        # Start
        # --------------------------------

        video.status = "processing"

        update_processing_progress(
            video,
            0,
            "Starting processing"
        )

        # --------------------------------
        # Video source handling
        # --------------------------------

        if video.youtube_url:

            update_processing_progress(
                video,
                10,
                "Downloading video"
            )

            downloaded_file = (
                download_youtube_video(
                    video.youtube_url
                )
            )

            video.video_file = downloaded_file

            video.save(
                update_fields=[
                    "video_file",
                    "updated_at",
                ]
            )

        if not video.video_file:

            raise ValueError(
                "No video file is available for processing."
            )

        update_processing_progress(
            video,
            15,
            "Video source ready"
        )

        # --------------------------------
        # Duration detection
        # --------------------------------

        video_path = video.video_file.path

        update_processing_progress(
            video,
            20,
            "Detecting video duration"
        )

        duration = get_video_duration(
            video_path
        )

        video.duration = duration

        video.save(
            update_fields=[
                "duration",
                "updated_at",
            ]
        )

        # --------------------------------
        # FORMAT FOR FIRST PROCESSING PASS
        # --------------------------------
        #
        # Vertical final output is created by
        # apply_vertical_compositor() later.
        #
        # Therefore the source clip must remain
        # horizontal at this stage.
        #

        processing_output_format = (
            "horizontal"
            if video.output_format == "vertical"
            else video.output_format
        )

        # --------------------------------
        # VIRAL MODE
        # --------------------------------

        if video.processing_mode == "viral":

            # --------------------------------
            # Whisper transcription
            # --------------------------------

            update_processing_progress(
                video,
                30,
                "Transcribing video"
            )

            transcript_result = (
                transcribe_video(
                    video_path
                )
            )

            # --------------------------------
            # Analyze transcript
            # --------------------------------

            update_processing_progress(
                video,
                50,
                "Finding viral moments"
            )

            clip_candidates = (
                analyze_transcript(
                    transcript_result,
                    max_clips=10
                )
            )

            # --------------------------------
            # Generate clips
            # --------------------------------

            output_directory = (
                Path(settings.MEDIA_ROOT)
                / "processed"
                / "viral"
                / str(video.id)
            )

            output_directory.mkdir(
                parents=True,
                exist_ok=True
            )

            generated_clips = []

            total_clips = len(
                clip_candidates
            )

            if total_clips == 0:

                update_processing_progress(
                    video,
                    100,
                    "No viral clips found"
                )

            else:

                for index, candidate in enumerate(
                    clip_candidates,
                    start=1
                ):

                    start_time = (
                        candidate["start"]
                    )

                    end_time = (
                        candidate["end"]
                    )

                    output_file = (
                        output_directory
                        / f"clip_{index}.mp4"
                    )

                    create_formatted_clip(
                        input_path=video_path,
                        output_path=output_file,
                        output_format=(
                            processing_output_format
                        ),
                        start_time=start_time,
                        end_time=end_time,
                    )

                    generated_clips.append(
                        {
                            "number": index,

                            "path": (
                                f"{settings.MEDIA_URL}"
                                f"processed/viral/"
                                f"{video.id}/"
                                f"clip_{index}.mp4"
                            ),

                            "file_path": str(
                                output_file
                            ),

                            "start": start_time,

                            "end": end_time,

                            "duration": round(
                                end_time - start_time,
                                2
                            ),

                            "text": candidate[
                                "text"
                            ],

                            "score": candidate[
                                "score"
                            ],
                        }
                    )

                    progress = 60 + int(
                        (index / total_clips) * 35
                    )

                    update_processing_progress(
                        video,
                        progress,
                        (
                            f"Generating clip "
                            f"{index} of "
                            f"{total_clips}"
                        )
                    )

        # --------------------------------
        # NORMAL MODE
        # --------------------------------

        elif video.processing_mode == "normal":

            update_processing_progress(
                video,
                40,
                "Preparing normal video segments"
            )

            normal_segments = (
                create_normal_segments(
                    duration=video.duration,
                    clip_length=60
                )
            )

            output_directory = (
                Path(settings.MEDIA_ROOT)
                / "processed"
                / "normal"
                / str(video.id)
            )

            update_processing_progress(
                video,
                50,
                "Generating normal clips"
            )

            normal_clips = (
                create_normal_clips(
                    input_path=video_path,
                    segments=normal_segments,
                    output_directory=output_directory,
                    output_format=(
                        processing_output_format
                    ),
                )
            )

            generated_clips = []

            total_clips = len(
                normal_clips
            )

            for index, clip in enumerate(
                normal_clips,
                start=1
            ):

                absolute_path = Path(
                    clip["path"]
                )

                relative_path = (
                    absolute_path.relative_to(
                        settings.MEDIA_ROOT
                    )
                )

                media_path = (
                    str(relative_path)
                    .replace("\\", "/")
                )

                generated_clips.append(
                    {
                        "number": clip[
                            "number"
                        ],

                        "path": (
                            f"{settings.MEDIA_URL}"
                            f"{media_path}"
                        ),

                        "file_path": str(
                            absolute_path
                        ),

                        "start": clip[
                            "start"
                        ],

                        "end": clip[
                            "end"
                        ],

                        "duration": clip[
                            "duration"
                        ],

                        "text": (
                            "Normal sequential cut"
                        ),

                        "score": "—",
                    }
                )

                progress = 50 + int(
                    (index / total_clips) * 45
                )

                update_processing_progress(
                    video,
                    progress,
                    (
                        f"Generating clip "
                        f"{index} of "
                        f"{total_clips}"
                    )
                )

        else:

            raise ValueError(
                "Invalid processing mode."
            )

        # --------------------------------
        # Completed
        # --------------------------------

        video.status = "completed"

        update_processing_progress(
            video,
            100,
            "Processing completed"
        )

        return generated_clips

    except Exception as exc:

        video.status = "failed"

        video.processing_stage = (
            f"Processing failed: {exc}"
        )

        video.save(
            update_fields=[
                "status",
                "processing_stage",
                "updated_at",
            ]
        )

        raise
