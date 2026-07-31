# Instruksi Codex — Multi-Backend Whisper

Implementasikan dukungan dua backend transcription:

1. `Whisper PyTorch`
2. `faster-whisper`

Kerjakan bertahap. Jangan commit sampai seluruh stage selesai dan tervalidasi.

## Target

User dapat memilih:

* Backend transcription
* Model Whisper
* Device
* Compute type yang kompatibel dengan backend dan device

Konfigurasi utama untuk laptop RTX 4070 8 GB:

```text
Backend: faster-whisper
Model: large-v3
Device: CUDA
Compute type: int8_float16
Concurrency: 1
```

Backend PyTorch existing harus tetap tersedia dan tidak mengalami regression.

---

# Stage 1 — Audit Existing Architecture

Audit terlebih dahulu:

* Whisper adapter/model loader yang sekarang digunakan
* Mapping nama model
* Model cache lifecycle
* Worker transcription
* Job configuration dan metadata
* API request/response terkait model
* UI pemilihan model
* Environment variable terkait device dan compute type
* Test existing
* Dependency Python existing

Dokumentasikan:

* File yang perlu diubah
* Alur konfigurasi dari UI sampai worker
* Bagian yang masih terikat langsung ke PyTorch
* Interface adapter yang bisa dibuat generic
* Metadata job yang saat ini sudah tersimpan

Jangan mengubah kode pada stage ini.

Acceptance:

* Root cause coupling PyTorch teridentifikasi.
* Flow UI → API → queue → worker → adapter terdokumentasi.
* Tidak mengarang route, schema, field, atau component yang belum ada.

---

# Stage 2 — Backend Abstraction

Buat abstraction layer generic untuk transcription backend.

Contoh konsep:

```python
class TranscriptionBackend:
    def load_model(self, config):
        ...

    def transcribe(self, audio_path, options):
        ...

    def unload_model(self):
        ...

    def get_runtime_metadata(self):
        ...
```

Implementasi backend:

```text
pytorch
faster-whisper
```

Gunakan interface dan result contract yang sama untuk kedua backend.

Normalized transcription result minimal mempertahankan data existing:

* text
* segments
* start
* end
* language
* confidence atau probability jika sebelumnya tersedia
* duration
* runtime metadata

Jangan mengubah contract frontend tanpa kebutuhan.

Acceptance:

* Worker tidak memanggil library Whisper secara langsung.
* Worker hanya memanggil backend adapter generic.
* Backend PyTorch existing tetap berfungsi.
* Test adapter existing tetap lulus.

---

# Stage 3 — Implementasi Faster-Whisper

Tambahkan dependency:

```text
faster-whisper
```

Gunakan:

```python
from faster_whisper import WhisperModel
```

Konfigurasi awal:

```python
WhisperModel(
    "large-v3",
    device="cuda",
    compute_type="int8_float16",
)
```

Transcription baseline:

```python
segments, info = model.transcribe(
    audio_path,
    beam_size=5,
    vad_filter=True,
)
```

Perhatikan bahwa `segments` adalah generator. Inference harus dikonsumsi secara penuh sebelum job dianggap selesai.

Normalize hasil faster-whisper ke contract existing.

Mapping probability:

* Gunakan `avg_logprob`, `no_speech_prob`, atau data segment yang benar-benar tersedia.
* Jangan mengarang confidence formula baru.
* Jika confidence existing membutuhkan transformasi, dokumentasikan formula dan tambah test.

Model mapping harus eksplisit:

```text
tiny
base
small
medium
large-v3
```

Jika UI existing masih memiliki nilai `large`, pertahankan compatibility mapping:

```text
large → large-v3
```

Acceptance:

* `large-v3 + cuda + int8_float16` berhasil diload.
* File audio dapat ditranskripsi.
* Segment timestamp tetap tersedia.
* Tidak ada perubahan silent terhadap transcript result.
* Backend failure menghasilkan error yang jelas.

---

# Stage 4 — Compute Type Compatibility

Buat compatibility matrix, jangan membiarkan kombinasi invalid masuk ke worker.

## Faster-Whisper

CUDA:

```text
float16
int8_float16
int8
```

CPU:

```text
int8
float32
```

## Whisper PyTorch

CUDA:

```text
float16
float32
```

CPU:

```text
float32
```

Gunakan hanya compute type yang benar-benar didukung oleh implementasi dan dependency terpasang.

Lakukan runtime capability check dengan CTranslate2:

```python
import ctranslate2

ctranslate2.get_cuda_device_count()
ctranslate2.get_supported_compute_types("cuda")
ctranslate2.get_supported_compute_types("cpu")
```

API harus mengembalikan capability aktual, bukan hanya daftar hardcoded.

Acceptance:

* UI tidak menawarkan kombinasi invalid.
* Backend tetap memvalidasi ulang input dari API.
* Error capability menyebut backend, device, compute type, dan rekomendasi konfigurasi valid.

---

# Stage 5 — Model Cache dan GPU Lifecycle

Pertahankan prinsip single active model cache.

Cache identity harus mencakup:

```text
backend
model
device
compute_type
```

Contoh:

```text
faster-whisper:large-v3:cuda:int8_float16
```

Saat salah satu nilai berubah:

1. Hapus referensi model lama.
2. Jalankan garbage collection.
3. Bersihkan cache CUDA jika backend memakai PyTorch.
4. Release object CTranslate2 untuk faster-whisper.
5. Verifikasi model lama tidak lagi digunakan.
6. Load model baru.

Jangan menyimpan PyTorch dan faster-whisper model secara bersamaan di GPU.

Lock harus mencakup:

* model switching
* model loading
* inference
* model unloading

Concurrency GPU tetap:

```text
1
```

Acceptance:

* Hanya satu model aktif.
* Switching backend tidak meninggalkan model lama di VRAM.
* Metadata active model akurat.
* OOM tidak mematikan worker.

---

# Stage 6 — OOM Handling

Pisahkan error:

```text
OOM saat load
OOM saat inference
dependency tidak kompatibel
compute type tidak didukung
CUDA tidak tersedia
model download gagal
```

Untuk faster-whisper `large-v3` CUDA OOM, tampilkan rekomendasi:

```text
Gunakan compute type int8_float16 atau int8.
Pastikan tidak ada model backend lain aktif.
Gunakan CPU jika VRAM masih tidak cukup.
```

Untuk PyTorch OOM:

```text
Gunakan model medium atau lebih kecil.
Gunakan CPU.
Atau pindah ke faster-whisper int8_float16.
```

Worker harus:

* menandai job gagal
* menyimpan error terstruktur
* release model cache jika perlu
* tetap tersedia untuk job berikutnya
* tidak melakukan fallback backend otomatis

Acceptance:

* Tidak ada silent fallback.
* Error UI dapat dipahami user.
* Worker tetap hidup setelah OOM.

---

# Stage 7 — Configuration API

Tambahkan backend ke konfigurasi transcription existing tanpa membuat route paralel yang tidak perlu.

Data configuration minimal:

```json
{
  "backend": "faster-whisper",
  "model": "large-v3",
  "device": "cuda",
  "computeType": "int8_float16"
}
```

Tambahkan capability response berdasarkan runtime:

```json
{
  "backends": [
    {
      "id": "pytorch",
      "label": "Whisper PyTorch",
      "available": true
    },
    {
      "id": "faster-whisper",
      "label": "faster-whisper",
      "available": true
    }
  ],
  "devices": [],
  "computeTypes": {},
  "models": []
}
```

Sesuaikan dengan API pattern existing. Jangan membuat nama field atau route baru sebelum audit memastikan kebutuhan tersebut.

Simpan snapshot konfigurasi pada job:

* requested backend
* active backend
* requested model
* active model
* device
* compute type
* backend library version
* model load duration
* inference duration
* VRAM sebelum load
* VRAM setelah load jika tersedia

Acceptance:

* Job lama tanpa field backend tetap menggunakan PyTorch existing atau default existing yang terdokumentasi.
* Job baru menyimpan backend snapshot.
* API tetap backward-compatible.

---

# Stage 8 — UI Backend Selector

Tambahkan selector backend pada area pemilihan model transcription existing.

Urutan field:

```text
Transcription Backend
Whisper Model
Device
Compute Type
```

## Backend Selector

Pilihan:

```text
Whisper PyTorch
faster-whisper
```

Deskripsi:

### Whisper PyTorch

```text
Backend kompatibel dengan implementasi Whisper existing. Membutuhkan VRAM lebih besar untuk model besar.
```

### faster-whisper

```text
Backend berbasis CTranslate2 yang lebih hemat memori dan dioptimalkan untuk inference.
```

## Dynamic Behaviour

Saat backend berubah:

* Refresh daftar compute type.
* Pertahankan model jika masih kompatibel.
* Reset compute type jika tidak kompatibel.
* Jangan langsung load model hanya karena dropdown berubah.
* Model dimuat ketika konfigurasi disimpan atau job dijalankan, mengikuti flow existing.

Untuk:

```text
faster-whisper
large-v3
cuda
```

Default compute type:

```text
int8_float16
```

Untuk:

```text
pytorch
large-v3
cuda
```

Tampilkan warning:

```text
Model large-v3 dengan Whisper PyTorch membutuhkan VRAM tinggi dan dapat gagal pada GPU 8 GB.
```

Untuk faster-whisper `large-v3 + cuda + int8_float16`, tampilkan recommendation badge:

```text
Recommended for 8 GB VRAM
```

Jangan hardcode nama GPU user pada UI.

---

# Stage 9 — UI Runtime Information

Tambahkan informasi backend aktif pada job detail atau monitoring existing.

Tampilkan:

```text
Backend
Model
Device
Compute Type
Model Status
Load Duration
Inference Duration
```

Model status:

```text
Not loaded
Loading
Ready
Running
Released
Failed
```

Jika OOM:

```text
CUDA memory is insufficient for the selected backend configuration.
```

Tambahkan rekomendasi contextual berdasarkan backend, bukan error generic.

Jangan menampilkan technical stack trace penuh kepada user biasa. Stack trace tetap tersedia pada log atau detail diagnostic existing.

Acceptance:

* User dapat membedakan requested configuration dan active runtime.
* Status tidak menyatakan model ready sebelum load selesai.
* UI tidak menampilkan data palsu ketika metadata belum tersedia.

---

# Stage 10 — Persisted Settings dan Defaults

Pertahankan default existing untuk backward compatibility.

Tambahkan recommended preset:

```text
Backend: faster-whisper
Model: large-v3
Device: cuda
Compute type: int8_float16
```

Jangan otomatis mengubah konfigurasi user existing.

Jika saved configuration lama tidak memiliki backend:

```text
Gunakan backend default existing.
```

Jika faster-whisper dependency tidak tersedia:

* Selector tetap dapat menampilkan backend sebagai unavailable.
* Jangan menyebabkan seluruh halaman error.
* Tampilkan alasan backend tidak tersedia.

Acceptance:

* Existing configuration tetap dapat dibaca.
* Migration tidak merusak job lama.
* Backend unavailable ditangani dengan graceful degradation.

---

# Stage 11 — Testing

Tambahkan test untuk:

## Backend Abstraction

* PyTorch adapter contract
* faster-whisper adapter contract
* normalized result
* model mapping `large → large-v3`

## Validation

* faster-whisper + CUDA + int8_float16 valid
* faster-whisper + CPU + int8 valid
* PyTorch + CUDA + float16 valid
* invalid compute type ditolak
* CUDA unavailable ditolak dengan pesan jelas

## Cache

* backend sama menggunakan ulang model
* model berubah melakukan unload
* backend berubah melakukan unload
* compute type berubah melakukan unload
* hanya satu model aktif

## OOM

* OOM load
* OOM inference
* worker tetap hidup
* cache dibersihkan
* tidak ada automatic fallback

## UI

* backend selector tampil
* compute type berubah sesuai backend
* warning PyTorch large-v3
* recommendation faster-whisper
* unavailable backend state
* saved selection dikirim ke API

## Regression

* Upload transcription existing
* Live transcription existing
* Timestamp
* Confidence
* Translation pipeline
* Post-processing
* Job persistence
* Monitoring

---

# Stage 12 — Local Acceptance

Gunakan satu audio sample yang sama untuk kedua backend.

Test matrix:

```text
PyTorch / medium / CUDA / float16
faster-whisper / medium / CUDA / float16
faster-whisper / large-v3 / CUDA / int8_float16
faster-whisper / large-v3 / CPU / int8
```

Untuk setiap test catat:

* berhasil load atau gagal
* peak VRAM
* peak RAM
* load duration
* transcription duration
* transcript output
* segment count
* detected language
* worker status setelah selesai

Acceptance utama perangkat ini:

```text
faster-whisper
large-v3
cuda
int8_float16
```

Harus:

* berhasil load tanpa OOM
* berhasil transcription
* worker tetap available
* tidak ada model PyTorch tertinggal di VRAM
* transcript dan timestamp tersimpan
* job metadata menampilkan backend aktif dengan benar

Jika masih OOM, coba:

```text
faster-whisper
large-v3
cuda
int8
```

Jangan mengubah model secara otomatis tanpa persetujuan user.

---

# Stage 13 — Documentation

Perbarui README:

* Perbedaan Whisper PyTorch dan faster-whisper
* Installation dependency
* CUDA requirement
* Compute type matrix
* Recommended configuration untuk GPU 8 GB
* CPU fallback
* Memory considerations
* Model cache lifecycle
* OOM troubleshooting
* Cara memeriksa proses GPU dengan `nvidia-smi`

Tambahkan contoh konfigurasi:

```text
Recommended RTX 4070 Laptop 8 GB:
Backend: faster-whisper
Model: large-v3
Device: cuda
Compute type: int8_float16
Concurrency: 1
```

Jangan menyatakan bahwa `large-v3` pasti berjalan pada semua GPU 8 GB. Nyatakan sebagai konfigurasi rekomendasi yang tetap harus divalidasi pada runtime.

---

# Final Validation

Jalankan seluruh validasi project existing:

* Python test
* Type checking
* Frontend test
* Production build
* Lint
* Regression transcription
* Regression translation
* Regression monitoring
* `git diff --check`

Laporan akhir harus berisi:

* Root cause sebelumnya
* Arsitektur backend final
* File yang berubah
* Dependency baru
* Compatibility matrix
* Hasil test setiap backend
* Hasil `large-v3 + faster-whisper + int8_float16`
* Peak VRAM dan RAM
* Known limitation
* Status commit

Jangan commit sebelum diminta.
