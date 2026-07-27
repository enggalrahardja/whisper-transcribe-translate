# Gap analysis against the target local-first architecture

| Area | Existing state | Gap / Stage implication |
|---|---|---|
| Provider boundary | `WhisperAdapter` and `TranslationAdapter` are concrete implementations | No capability/version contract or interchangeable provider interface yet; defer to Stage 2 |
| Local default | Local PyTorch Whisper `base` is default | Preserved, but planned `faster-whisper` live/final split is not implemented |
| Translation privacy | GoogleTranslator sends transcript text to a remote service | Local-only translation is not possible; UI/settings do not classify this network/privacy implication |
| Preprocessing | ffmpeg decode, mono downmix, 16 kHz resample; browser requests built-in AEC/NS/AGC | No server-side VAD, endpointing, calibrated noise handling, levels, or preprocessing telemetry |
| Live streaming | 3 s WAV chunks with 0.5 s overlap, sequential full-chunk inference | No token streaming, bounded queue/backpressure, sequence-number loss detection, or first-token optimization |
| Result states | Chunk output is labelled partial; stop copies it to final | No stable state, revision identity, final reprocessing, or provisional replacement semantics |
| Accuracy path | Same selected model/settings serve live and uploaded paths | No independently selectable fast-live and accurate-final models |
| Translation timing | File translation happens after full transcription | No live translation after stable text and no linked source/translation revisions |
| Diarization | None | No speaker identities or overlap-aware speaker attribution |
| Persistence provenance | Model name and language are stored | Missing provider, exact model/checkpoint version, hardware, config snapshot, latency, resources, and benchmark/dataset version |
| Observability | Logs, progress, worker heartbeat/status | Missing structured metrics, latency origins, RTF, resource sampling, audio loss/duplicate counters, and quality dashboard |
| Privacy controls | Filesystem/Mongo storage and retention settings exist | No enforced local-only profile, cloud egress guard, consent/audit trail, or per-result privacy classification |
| Benchmark evidence | No safe corpus or historical benchmark output | Stage 1 manifest/harness exists; values remain unmeasured until reviewed non-sensitive audio is supplied |
| Model catalogue | Download/availability UI lists names and sizes/status | Capability, accuracy status, streaming/translation/diarization, hardware, privacy, price, and limitations are not shown in UI; documentation now records them without changing UI |

## Highest-risk acceptance gaps

1. “Stable under 2 seconds” cannot be evaluated because stable events do not
   exist in the application.
2. Live “final under 5 seconds after endpoint” is not an accurate reprocessing
   path; finalization currently copies partial text.
3. A local profile cannot claim zero cloud calls when translation is selected,
   because the only translation provider is GoogleTranslator.
4. Accepted-chunk loss cannot be proven: hashes deduplicate payloads, but chunks
   have no client sequence number or explicit acknowledgement ledger.
5. Accuracy and hardware claims cannot be classified until the disabled dataset
   placeholders are populated and benchmarked on named hardware.
