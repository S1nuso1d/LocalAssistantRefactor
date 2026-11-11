"""Hardware-independent phrase segmentation for the audio worker.

Keeping this logic out of the QThread makes it unit-testable and lets the
worker stay a thin I/O shell around it.
"""
from __future__ import annotations

from collections import deque

import numpy as np


class PhraseSegmenter:
    """Energy-based endpointing with a short pre-roll.

    The pre-roll keeps the chunk(s) immediately *before* the level crossed the
    threshold, so quiet word onsets are not clipped before Whisper sees them.
    """

    def __init__(
        self,
        chunk_duration: float,
        min_speech: float,
        max_phrase: float,
        preroll_chunks: int = 1,
    ) -> None:
        self.chunk_duration = float(chunk_duration)
        self.min_speech = float(min_speech)
        self.max_phrase = float(max_phrase)
        self._preroll: deque[np.ndarray] = deque(maxlen=max(0, preroll_chunks))
        self.reset(clear_preroll=True)

    def reset(self, clear_preroll: bool = False) -> None:
        self._buffer: list[np.ndarray] = []
        self._speech_seconds = 0.0
        self._silence_seconds = 0.0
        self._in_speech = False
        if clear_preroll:
            self._preroll.clear()

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def feed(
        self,
        audio: np.ndarray,
        rms: float,
        threshold: float,
        silence_limit: float,
    ) -> np.ndarray | None:
        """Consume one chunk; return a finished phrase or None."""
        if rms >= threshold:
            if not self._in_speech:
                self._buffer.extend(self._preroll)
                self._preroll.clear()
            self._buffer.append(audio.copy())
            self._speech_seconds += self.chunk_duration
            self._silence_seconds = 0.0
            self._in_speech = True
        elif self._in_speech:
            self._buffer.append(audio.copy())
            self._silence_seconds += self.chunk_duration
        else:
            self._preroll.append(audio.copy())

        finished = self._in_speech and self._silence_seconds >= silence_limit
        too_long = self._speech_seconds >= self.max_phrase
        if not (finished or too_long):
            return None

        phrase = None
        if self._buffer and self._speech_seconds >= self.min_speech:
            phrase = np.concatenate(self._buffer)
        self.reset(clear_preroll=True)
        return phrase


def segment_is_reliable(
    no_speech_prob: float | None,
    avg_logprob: float | None,
    compression_ratio: float | None,
) -> bool:
    """Reject typical Whisper hallucinations on music/noise/silence."""
    if compression_ratio is not None and compression_ratio > 2.4:
        return False  # repetition loop
    if (
        no_speech_prob is not None and avg_logprob is not None
        and no_speech_prob > 0.6 and avg_logprob < -1.0
    ):
        return False
    if avg_logprob is not None and avg_logprob < -1.8:
        return False
    return True
