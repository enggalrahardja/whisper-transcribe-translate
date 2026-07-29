# Benchmark dataset

`manifest.json` is the source of truth. Stage 15 adds repository-authored,
non-sensitive synthetic audio and references checked against the exact speech
synthesis scripts. The fixture is intended for reproducibility and relative
smoke comparisons; it is not evidence of natural meeting-room accuracy.

Recommended recording format is lossless mono PCM WAV. Preserve an original
copy outside the repository if transformations are used, and record the actual
file format, sample rate, channels, environment, and SHA-256 in the manifest.
The application itself accepts WAV, MP3, OGG, FLAC, M4A, and common video
containers; the benchmark should prefer WAV to reduce codec variance.

Reference transcripts should be verbatim, including code-switching and
disfluencies. Store one plain UTF-8 text file per case. Reference translations
should preserve names, numbers, dates, and technical terms. Use `null` when a
translation is intentionally not evaluated.
