# ============================================
# Viral Clip Analyzer
# ============================================


# --------------------------------------------
# Strong individual hook words
# --------------------------------------------

HOOK_WORDS = [
    "but",
    "because",
    "how",
    "why",
    "secret",
    "important",
    "never",
    "always",
    "best",
    "worst",
    "mistake",
    "problem",
    "truth",
    "really",
    "actually",
    "remember",
    "listen",
    "wait",
    "look",
    "believe",
    "impossible",
    "danger",
    "warning",
    "happened",
    "happens",
    "reason",
    "first",
    "last",
    "before",
    "after",
]


# --------------------------------------------
# Curiosity / hook phrases
# --------------------------------------------

HOOK_PHRASES = [
    "you won't believe",
    "you will not believe",
    "the truth is",
    "the real reason",
    "what happened next",
    "here's why",
    "this is why",
    "the reason why",
    "the biggest mistake",
    "one thing",
    "the problem is",
    "nobody knows",
    "no one knows",
    "the secret",
    "listen carefully",
    "wait for it",
    "what if",
    "can you believe",
    "you need to know",
    "you should know",
    "this changes everything",
    "everything changed",
    "it turns out",
    "as it turns out",
]


# --------------------------------------------
# Emotional / high-interest words
# --------------------------------------------

EMOTIONAL_WORDS = [
    "love",
    "hate",
    "fear",
    "angry",
    "cry",
    "crying",
    "happy",
    "sad",
    "shocked",
    "shock",
    "surprise",
    "surprised",
    "scared",
    "afraid",
    "dead",
    "death",
    "kill",
    "killed",
    "fight",
    "fighting",
    "win",
    "won",
    "lose",
    "lost",
    "betray",
    "betrayed",
    "dangerous",
    "danger",
    "crazy",
    "insane",
    "terrible",
    "amazing",
    "incredible",
]


# --------------------------------------------
# Contrast words
# --------------------------------------------

CONTRAST_WORDS = [
    "but",
    "however",
    "although",
    "instead",
    "yet",
    "except",
    "until",
    "suddenly",
    "then",
    "finally",
]


MIN_CLIP_DURATION = 30
TARGET_CLIP_DURATION = 90
MAX_CLIP_DURATION = 180


def _normalize_text(text):
    """
    Normalize transcript text so that
    scoring is more reliable.
    """

    return " ".join(
        text.lower().strip().split()
    )


def _contains_word(text, word):
    """
    Check for a complete word instead of
    matching random parts of another word.
    """

    words = text.split()

    return word in words


def calculate_segment_score(text):
    """
    Calculate viral potential score
    for one transcript segment.

    The score combines:

    - Hook words
    - Hook phrases
    - Questions
    - Emotional language
    - Curiosity
    - Numbers
    - Contrast
    - Sentence length
    """

    text = _normalize_text(text)

    if not text:
        return 0

    score = 0

    # ----------------------------------------
    # Hook words
    # ----------------------------------------

    for word in HOOK_WORDS:

        if _contains_word(text, word):
            score += 1


    # ----------------------------------------
    # Hook phrases
    # ----------------------------------------

    for phrase in HOOK_PHRASES:

        if phrase in text:
            score += 3


    # ----------------------------------------
    # Emotional words
    # ----------------------------------------

    emotional_matches = 0

    for word in EMOTIONAL_WORDS:

        if _contains_word(text, word):
            emotional_matches += 1

    score += min(
        emotional_matches * 2,
        6
    )


    # ----------------------------------------
    # Question signal
    # ----------------------------------------

    if "?" in text:
        score += 3

    # Whisper sometimes removes punctuation,
    # so question-like words are also useful.

    question_starts = [
        "why ",
        "how ",
        "what ",
        "when ",
        "where ",
        "who ",
        "can ",
        "could ",
        "would ",
        "did ",
        "do ",
        "does ",
        "is ",
        "are ",
    ]

    for question_start in question_starts:

        if text.startswith(question_start):
            score += 2
            break


    # ----------------------------------------
    # Contrast / turning-point signal
    # ----------------------------------------

    for word in CONTRAST_WORDS:

        if _contains_word(text, word):
            score += 1


    # ----------------------------------------
    # Numbers
    # ----------------------------------------

    has_number = any(
        character.isdigit()
        for character in text
    )

    if has_number:
        score += 2


    # ----------------------------------------
    # Exclamation signal
    # ----------------------------------------

    if "!" in text:
        score += 2


    # ----------------------------------------
    # Sentence / information density
    # ----------------------------------------

    word_count = len(
        text.split()
    )

    if word_count >= 8:
        score += 1

    if word_count >= 15:
        score += 1

    if word_count >= 25:
        score += 1


    return score


def _get_segment_duration(segment):
    """
    Return transcript segment duration.
    """

    start = float(
        segment.get("start", 0)
    )

    end = float(
        segment.get("end", start)
    )

    return max(
        0,
        end - start
    )


def _build_clip(
    segments,
    center_index,
    target_duration=TARGET_CLIP_DURATION
):
    """
    Build a meaningful clip around a
    strong transcript segment.

    Surrounding transcript segments are
    included so that the clip keeps context.
    """

    if not segments:
        return None

    center_segment = segments[
        center_index
    ]

    center_start = float(
        center_segment.get(
            "start",
            0
        )
    )

    center_end = float(
        center_segment.get(
            "end",
            center_start
        )
    )

    clip_start = center_start
    clip_end = center_end

    left_index = center_index - 1
    right_index = center_index + 1

    # ----------------------------------------
    # Expand around strong moment
    # ----------------------------------------

    while (
        clip_end - clip_start
        < target_duration
    ):

        added_segment = False

        if left_index >= 0:

            previous_segment = segments[
                left_index
            ]

            previous_start = float(
                previous_segment.get(
                    "start",
                    clip_start
                )
            )

            clip_start = previous_start

            left_index -= 1

            added_segment = True


        if (
            clip_end - clip_start
            >= target_duration
        ):
            break


        if right_index < len(segments):

            next_segment = segments[
                right_index
            ]

            next_end = float(
                next_segment.get(
                    "end",
                    clip_end
                )
            )

            clip_end = next_end

            right_index += 1

            added_segment = True


        if not added_segment:
            break


    # ----------------------------------------
    # Minimum duration
    # ----------------------------------------

    if (
        clip_end - clip_start
        < MIN_CLIP_DURATION
    ):

        while (
            clip_end - clip_start
            < MIN_CLIP_DURATION
        ):

            expanded = False

            if left_index >= 0:

                previous_segment = segments[
                    left_index
                ]

                clip_start = float(
                    previous_segment.get(
                        "start",
                        clip_start
                    )
                )

                left_index -= 1

                expanded = True


            if (
                clip_end - clip_start
                >= MIN_CLIP_DURATION
            ):
                break


            if right_index < len(segments):

                next_segment = segments[
                    right_index
                ]

                clip_end = float(
                    next_segment.get(
                        "end",
                        clip_end
                    )
                )

                right_index += 1

                expanded = True


            if not expanded:
                break


    # ----------------------------------------
    # Maximum duration
    # ----------------------------------------

    if (
        clip_end - clip_start
        > MAX_CLIP_DURATION
    ):

        clip_end = (
            clip_start
            + MAX_CLIP_DURATION
        )


    return {
        "start": round(
            clip_start,
            2
        ),

        "end": round(
            clip_end,
            2
        ),

        "duration": round(
            clip_end - clip_start,
            2
        ),

        "text": center_segment.get(
            "text",
            ""
        ).strip(),

        "score": calculate_segment_score(
            center_segment.get(
                "text",
                ""
            )
        ),
    }


def _clips_overlap(
    clip_a,
    clip_b
):
    """
    Check whether two clips overlap heavily.
    """

    start_a = clip_a["start"]
    end_a = clip_a["end"]

    start_b = clip_b["start"]
    end_b = clip_b["end"]

    overlap_start = max(
        start_a,
        start_b
    )

    overlap_end = min(
        end_a,
        end_b
    )

    if overlap_end <= overlap_start:
        return False

    overlap_duration = (
        overlap_end
        - overlap_start
    )

    duration_a = (
        end_a
        - start_a
    )

    duration_b = (
        end_b
        - start_b
    )

    smaller_duration = min(
        duration_a,
        duration_b
    )

    if smaller_duration <= 0:
        return False

    overlap_ratio = (
        overlap_duration
        / smaller_duration
    )

    return overlap_ratio >= 0.60


def analyze_transcript(
    transcript_result,
    max_clips=10
):
    """
    Analyze Whisper transcript and find
    meaningful viral clip candidates.

    Clips are:

    - At least 30 seconds
    - Around 90 seconds when possible
    - Maximum 180 seconds
    - Maximum 10 clips
    - Non-overlapping
    """

    segments = transcript_result.get(
        "segments",
        []
    )

    if not segments:
        return []

    candidates = []

    # ----------------------------------------
    # Score every transcript segment
    # ----------------------------------------

    for index, segment in enumerate(
        segments
    ):

        text = segment.get(
            "text",
            ""
        ).strip()

        if not text:
            continue

        score = calculate_segment_score(
            text
        )

        candidates.append(
            {
                "index": index,
                "score": score,
            }
        )


    # ----------------------------------------
    # Strongest moments first
    # ----------------------------------------

    candidates.sort(
        key=lambda item: item["score"],
        reverse=True
    )


    final_clips = []


    # ----------------------------------------
    # Build clips around strong moments
    # ----------------------------------------

    for candidate in candidates:

        clip = _build_clip(
            segments,
            candidate["index"]
        )

        if clip is None:
            continue


        if (
            clip["duration"]
            < MIN_CLIP_DURATION
        ):
            continue


        duplicate = False


        for existing_clip in final_clips:

            if _clips_overlap(
                clip,
                existing_clip
            ):

                duplicate = True
                break


        if duplicate:
            continue


        final_clips.append(
            clip
        )


        if len(final_clips) >= max_clips:
            break


    # ----------------------------------------
    # Show clips in video order
    # ----------------------------------------

    final_clips.sort(
        key=lambda item: item["start"]
    )


    return final_clips