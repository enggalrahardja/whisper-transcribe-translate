# Stage 18 pass/fail matrix

| Area | Status | Evidence |
|---|---|---|
| Bahasa Indonesia | **PASS** | Stage 18/15 non-sensitive local base benchmark |
| Bahasa Inggris | **PASS** | Stage 18/15 non-sensitive local base benchmark |
| Code-switching | **PASS** | Stage 18/15 non-sensitive local base benchmark |
| Technical terminology | **PASS** | Stage 18/15 non-sensitive local base benchmark |
| Quiet microphone fixture | **PASS** | Stage 18/15 non-sensitive local base benchmark |
| Background noise fixture | **PASS** | Stage 18/15 non-sensitive local base benchmark |
| Far-field fixture | **PASS** | Stage 18/15 non-sensitive local base benchmark |
| Multi-speaker ASR fixture | **PASS** | Stage 18/15 non-sensitive local base benchmark |
| Overlapping-speech ASR fixture | **PASS** | Stage 18/15 non-sensitive local base benchmark |
| PCM/VAD/state/accurate-final/glossary/downstream queues | **PASS** | API acceptance and regression suite |
| Reconnect and sequence anomaly handling | **PASS** | PCM/VAD/session isolation tests |
| Queue pressure and worker isolation | **PASS** | bounded queue/backpressure/failure-isolation tests |
| Persistence degradation and runtime restore | **PASS** | repository/write-behind/reconnect restore tests |
| Local translation real-model E2E | **PENDING** | Marian checkpoint was unavailable; lifecycle uses deterministic/fake-provider acceptance tests |
| Local diarization real-model E2E | **PENDING** | SpeechBrain checkpoint was not pinned/available; clustering lifecycle tests pass |
| Physical microphone E2E | **PENDING** | Physical microphone/device E2E was not available to the headless runner |
| API restart with deployed persistence | **PENDING** | Repository restore tests pass, but API process restart against deployment Mongo/storage was not executed |
| Production deployment security | **PENDING** | Automated auth/redaction tests pass; deployed TLS/origin/secret-manager acceptance was not executed |
| OpenAI optional provider | **NOT_RUN** | key, billing approval, and cloud-dataset consent were not all available |
