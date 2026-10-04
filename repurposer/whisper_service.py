_model = None


def transcribe_video(video_path):
    """
    Transcribe a video using OpenAI Whisper.

    Whisper is imported lazily so that Django/Render
    does not load Whisper, Numba, and llvmlite
    while the server is starting.
    """

    global _model

    # Import Whisper only when transcription is actually needed.
    import whisper

    # Load the model only once and reuse it.
    if _model is None:
        _model = whisper.load_model("tiny")

    result = _model.transcribe(
        str(video_path)
    )

    return result