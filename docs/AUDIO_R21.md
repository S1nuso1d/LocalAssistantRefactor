# LocalAssistant r21 — Audio sources and adaptive VAD

## Goals

r21 adds a physical microphone input without replacing the existing system-loopback path. Both sources are optional and can run independently or at the same time.

## Runtime model

- `AudioWorker` owns the source lifecycle and one Whisper model instance.
- Each enabled source gets its own capture thread, SoundCard recorder, `PhraseSegmenter`, `AdaptiveNoiseGate`, retry policy, source generation and bounded queue (3 phrases).
- A single `whisper-transcriber` thread polls source queues round-robin. It is the only thread that calls the shared Whisper model, avoiding concurrent inference on one model instance.
- Queue capacity is per source. A high-volume system loopback source cannot evict pending microphone phrases. Phrases older than 30 seconds are discarded instead of being answered after a long inference backlog.
- Transcript delivery includes the source ID. The compatibility `phrase_detected(str)` signal is also emitted for existing internal integrations.

## Device selection and recovery

- New settings store both a human-readable name and SoundCard's backend-dependent device ID.
- A stored ID is matched exactly. It is never silently rebound to a different device with the same display name.
- Legacy settings that have only a name use an exact, case-insensitive name match and migrate to a backend ID when saved from the settings dialog.
- If a pinned device disappears, the source temporarily opens the current default device but remembers the requested pinned selection. A periodic device check notices when that device returns and reopens the requested endpoint.
- The system loopback source resolves a speaker endpoint and obtains its loopback microphone by backend ID first, with a display-name fallback for backends that reject the ID.

SoundCard documents device IDs as unique but backend-dependent. The library also recommends obtaining fresh device objects rather than retaining references indefinitely because hardware can change. See <https://soundcard.readthedocs.io/en/latest/>.

## Adaptive energy gate

- Every source owns an independent `AdaptiveNoiseGate`.
- A bounded rolling history estimates the background floor from its 20th percentile. The estimate adapts faster upward than downward.
- Start threshold is clamped and uses the base threshold as a floor; sensitivity scales the adaptive threshold.
- Release threshold is lower than start threshold (hysteresis), reducing phrase fragmentation around quiet syllables.
- Turning adaptive VAD off returns to the fixed legacy threshold. `faster-whisper`'s `vad_filter=True` remains enabled; the energy gate is only an inexpensive endpointing layer, not a replacement for model VAD.

## TTS and microphone behaviour

- System loopback is muted while TTS is speaking and for the configured tail period.
- Physical microphone capture is muted during TTS by default to avoid the assistant transcribing its own voice through speakers. Users can explicitly keep the microphone active during TTS; this is intended for headphone use only. SoundCard does not provide acoustic echo cancellation, so loudspeakers may feed TTS back into the microphone.
- Voice interruption/cancel-on-mic-activity and acoustic echo cancellation are explicitly not part of r21; they need a dedicated audio processing/UX stage and validation.

## Privacy and defaults

- System loopback remains enabled by default to preserve r20 behaviour.
- Microphone capture is disabled by default and must be explicitly enabled.
- Windows desktop-app microphone permissions must be granted by the user.
- Recognized user speech follows the existing filtering and local/remote Ollama policy. Accepted messages are stored in the SQLite conversation history; technical transcript logging remains redacted by default.

## Required Windows acceptance tests

1. System loopback only: existing r20 behaviour remains unchanged.
2. Microphone only: record speech from the selected physical microphone and verify source labels.
3. Both sources enabled: speak into the mic while audio plays through the selected output; verify both queues progress and transcripts are source-labeled.
4. Device selection with multiple similarly named devices: a saved backend ID must select the exact endpoint.
5. Disconnect a pinned mic and loopback output independently; verify fallback, retry and restoration when each endpoint returns.
6. Pause and resume while both sources are active; no pre-pause phrase may be delivered after resume.
7. Stop application while both sources are active and during Whisper inference; all capture/transcriber threads must exit after current native inference returns.
8. Test adaptive VAD at quiet, normal and noisy input levels; compare against fixed-threshold mode and check false positives/false negatives.
9. Enable TTS with a headset and with speakers; document feedback behaviour. No echo-cancellation claim should be made until that feature exists.
10. Verify Windows microphone privacy permission denial is shown as a source-specific unavailable state without disabling the working loopback source.
