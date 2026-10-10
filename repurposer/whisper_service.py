# ============================================================
# WHISPER SERVICE
# ============================================================

_model = None


def transcribe_video(video_path):
    """
    Transcribe a video using faster-whisper.

    The model is loaded only once and reused for later videos.

    CPU optimization:
        - tiny model
        - int8 computation

    Returns a dictionary compatible with the existing
    Whisper-style processing pipeline.
    """

    global _model

    from faster_whisper import WhisperModel

    # --------------------------------------------------------
    # Load model only once
    # --------------------------------------------------------

    if _model is None:
        print("Loading faster-whisper tiny model...")

        _model = WhisperModel(
            "tiny",
            device="cpu",
            compute_type="int8",
        )

        print("faster-whisper model loaded.")

    # --------------------------------------------------------
    # Transcribe
    # --------------------------------------------------------

    print(
        "Starting faster-whisper transcription:",
        video_path,
    )

    segments, info = _model.transcribe(
        str(video_path),
        beam_size=1,
        vad_filter=True,
        condition_on_previous_text=False,
    )

    # --------------------------------------------------------
    # Convert generator to normal list
    # --------------------------------------------------------

    segment_list = []

    full_text_parts = []

    for segment in segments:
        item = {
            "id": len(segment_list),
            "start": float(segment.start),
            "end": float(segment.end),
            "text": segment.text.strip(),
        }

        segment_list.append(item)

        if item["text"]:
            full_text_parts.append(item["text"])

    # --------------------------------------------------------
    # Return Whisper-compatible result
    # --------------------------------------------------------

    result = {
        "text": " ".join(full_text_parts).strip(),
        "segments": segment_list,
        "language": getattr(info, "language", None),
    }

    print(
        "Transcription completed:",
        len(segment_list),
        "segments",
    )

    return result