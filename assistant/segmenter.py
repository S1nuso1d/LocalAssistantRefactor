"""Hardware-independent phrase segmentation for the audio worker.

Keeping this logic out of the QThread makes it unit-testable and lets the
worker stay a thin I/O shell around it.
"""
from __future__ import annotations

import queue
from collections import deque
import math

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
        end_threshold: float | None = None,
    ) -> np.ndarray | None:
        """Consume one chunk; return a finished phrase or None.

        ``threshold`` starts speech; the lower ``end_threshold`` provides
        hysteresis so a quiet syllable is not split into multiple phrases.
        """
        release_threshold = threshold if end_threshold is None else end_threshold
        active_threshold = release_threshold if self._in_speech else threshold
        if rms >= active_threshold:
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



class AdaptiveNoiseGate:
    """Rolling-percentile noise estimator with start/stop hysteresis.

    It is deliberately an energy gate, not a replacement for Whisper VAD.
    A low rolling percentile follows the background floor while remaining
    resistant to short speech bursts.  The resulting threshold is bounded.
    """

    def __init__(
        self,
        base_threshold: float = 0.008,
        sensitivity: float = 1.0,
        enabled: bool = True,
        window_chunks: int = 120,
    ) -> None:
        self.base_threshold = max(0.001, min(0.05, float(base_threshold)))
        self.sensitivity = max(0.5, min(2.0, float(sensitivity)))
        self.enabled = bool(enabled)
        self._history: deque[float] = deque(maxlen=max(12, int(window_chunks)))
        self._noise_floor = self.base_threshold / 3.0

    @property
    def start_threshold(self) -> float:
        if not self.enabled:
            return self.base_threshold
        minimum = self.base_threshold * 0.5
        adaptive = self._noise_floor * (3.0 / self.sensitivity)
        return max(minimum, min(0.05, adaptive))

    @property
    def end_threshold(self) -> float:
        return self.start_threshold * (0.65 if self.enabled else 1.0)

    @property
    def noise_floor(self) -> float:
        return self._noise_floor

    def observe(self, rms: float) -> None:
        if not self.enabled:
            return
        value = float(rms)
        if not math.isfinite(value):
            return
        value = max(0.0, min(value, 1.0))
        self._history.append(value)
        if len(self._history) < 8:
            estimate = sorted(self._history)[max(0, int((len(self._history) - 1) * 0.2))]
        else:
            estimate = float(np.percentile(np.fromiter(self._history, dtype=np.float32), 20))
        # Adapt faster when the environment becomes louder than when it gets
        # quieter, avoiding abrupt sensitivity jumps after brief pauses.
        alpha = 0.18 if estimate > self._noise_floor else 0.06
        self._noise_floor += alpha * (estimate - self._noise_floor)
        self._noise_floor = max(0.0001, min(0.05, self._noise_floor))


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


def put_drop_oldest(target: "queue.Queue", item) -> bool:
    """Enqueue *item*; when full, discard the oldest entry instead.

    Returns True if something was dropped. Used so a slow recogniser never
    makes the capture thread block or build an unbounded backlog.
    """
    dropped = False
    while True:
        try:
            target.put_nowait(item)
            return dropped
        except queue.Full:
            try:
                target.get_nowait()
                dropped = True
            except queue.Empty:
                pass
