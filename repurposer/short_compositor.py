"""
Vertical short compositor.

Creates a 1080x1920 (9:16) final video from a source clip:
- Full-frame blurred/zoomed copy of the source as background.
- Original source video kept on top, fitted to 16:9.
- Original source audio is preserved.
- Optional background music is mixed at a low volume.
- Existing source files are never overwritten.
"""

from pathlib import Path
import subprocess


OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920

# BGM volume.
# 0.15 means approximately 15% of the BGM's original level.
BGM_VOLUME = 0.50


def create_vertical_short(
    input_path,
    output_path,
    bgm_path=None,
    bgm_volume=BGM_VOLUME,
):
    """
    Create a 1080x1920 vertical short.

    Layout:

        ┌───────────────────┐
        │                   │
        │  BLURRED SOURCE   │
        │    BACKGROUND     │
        │                   │
        │ ┌───────────────┐ │
        │ │               │ │
        │ │ ORIGINAL      │ │
        │ │ 16:9 VIDEO    │ │
        │ │               │ │
        │ └───────────────┘ │
        │                   │
        │  BLURRED SOURCE   │
        │    BACKGROUND     │
        │                   │
        └───────────────────┘

    Original source video and source file are never modified.
    """

    input_path = Path(input_path)
    output_path = Path(output_path)

    # ---------------------------------------------------------
    # INPUT VALIDATION
    # ---------------------------------------------------------

    if not input_path.is_file():
        raise FileNotFoundError(
            f"Input video not found: {input_path}"
        )

    if bgm_path:
        bgm_path = Path(bgm_path)

        if not bgm_path.is_file():
            raise FileNotFoundError(
                f"Background music not found: {bgm_path}"
            )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # ---------------------------------------------------------
    # VIDEO FILTER
    # ---------------------------------------------------------
    #
    # Source is split into:
    #
    # [bg_src]   -> zoomed + cropped + blurred background
    # [main_src] -> original 16:9 video
    #
    # Background:
    #   scale -> crop -> zoom -> blur -> darken
    #
    # Main:
    #   scale to 1080 width
    #
    # ---------------------------------------------------------

    video_filter = (
        "[0:v]"
        "split=2[bg_src][main_src];"

        # -----------------------------
        # BACKGROUND
        # -----------------------------
        "[bg_src]"
        "scale=1080:1920:"
        "force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "scale=1180:2098,"
        "crop=1080:1920,"
        "gblur=sigma=28,"
        "eq=brightness=-0.10:saturation=0.90"
        "[bg];"

        # -----------------------------
        # MAIN VIDEO
        # -----------------------------
        "[main_src]"
        "scale=1080:-2:"
        "force_original_aspect_ratio=decrease"
        "[main];"

        # -----------------------------
        # COMBINE
        # -----------------------------
        "[bg][main]"
        "overlay=0:(H-h)/2:"
        "shortest=1"
        "[vout]"
    )

    # ---------------------------------------------------------
    # BASE FFMPEG COMMAND
    # ---------------------------------------------------------

    command = [
        "ffmpeg",
        "-y",

        "-i",
        str(input_path),
    ]

    # ---------------------------------------------------------
    # WITH BACKGROUND MUSIC
    # ---------------------------------------------------------

    if bgm_path:

        # Loop BGM indefinitely.
        command.extend(
            [
                "-stream_loop",
                "-1",
                "-i",
                str(bgm_path),
            ]
        )

        audio_filter = (
            # -------------------------------------------------
            # ORIGINAL AUDIO
            # -------------------------------------------------
            "[0:a]"
            "aresample=48000,"
            "aformat="
            "sample_fmts=fltp:"
            "sample_rates=48000:"
            "channel_layouts=stereo,"
            "volume=1.0"
            "[voice];"

            # -------------------------------------------------
            # BACKGROUND MUSIC
            # -------------------------------------------------
            "[1:a]"
            "aresample=48000,"
            "aformat="
            "sample_fmts=fltp:"
            "sample_rates=48000:"
            "channel_layouts=stereo,"
            f"volume={float(bgm_volume)}"
            "[music];"

            # -------------------------------------------------
            # MIX ORIGINAL AUDIO + BGM
            #
            # No aggressive sidechain compression here.
            # This ensures BGM is actually audible.
            # -------------------------------------------------
            "[voice][music]"
            "amix="
            "inputs=2:"
            "duration=first:"
            "dropout_transition=2:"
            "normalize=0"
            "[aout]"
        )

        command.extend(
            [
                "-filter_complex",
                video_filter + ";" + audio_filter,

                "-map",
                "[vout]",

                "-map",
                "[aout]",
            ]
        )

    # ---------------------------------------------------------
    # WITHOUT BACKGROUND MUSIC
    # ---------------------------------------------------------

    else:

        command.extend(
            [
                "-filter_complex",
                video_filter,

                "-map",
                "[vout]",

                "-map",
                "0:a?",
            ]
        )

    # ---------------------------------------------------------
    # OUTPUT SETTINGS
    # ---------------------------------------------------------

    command.extend(
        [
            "-c:v",
            "libx264",

            "-preset",
            "medium",

            "-crf",
            "23",

            "-pix_fmt",
            "yuv420p",

            "-c:a",
            "aac",

            "-b:a",
            "192k",

            "-movflags",
            "+faststart",

            str(output_path),
        ]
    )

    # ---------------------------------------------------------
    # RUN FFMPEG
    # ---------------------------------------------------------

    subprocess.run(
        command,
        check=True,
    )

    return output_path