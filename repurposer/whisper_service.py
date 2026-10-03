import whisper


def transcribe_video(video_path):
    """
    Transcribe a video using OpenAI Whisper.
    """

    model = whisper.load_model("tiny")

    result = model.transcribe(
        str(video_path)
    )

    return result