Lanjutkan pada branch dan draft PR yang sama. Jangan buat PR baru.

Target: selesaikan dependency runtime CUDA 12 untuk `faster-whisper` tanpa merusak backend PyTorch CUDA 11.8.

## Stage 1 — Audit Runtime Existing

Audit:

* virtualenv dan dependency Python aktif
* versi `faster-whisper`
* versi `ctranslate2`
* versi PyTorch dan CUDA runtime PyTorch
* mekanisme start backend existing
* file dependency project
* environment loading existing
* deployment/start script existing

Konfirmasi lokasi library berikut setelah instalasi:

```text
libcublas.so.12
libcudnn.so.9
```

Jangan mengubah kode sebelum alur start backend existing dipahami.

## Stage 2 — Tambahkan Dependency CUDA 12

Tambahkan dependency Python yang diperlukan oleh CTranslate2:

```text
nvidia-cublas-cu12
nvidia-cudnn-cu12==9.*
```

Masukkan ke dependency management existing, bukan instalasi manual yang tidak terdokumentasi.

Jangan upgrade atau mengganti PyTorch hanya untuk menyelesaikan masalah ini.

Acceptance:

* PyTorch existing tetap memakai runtime CUDA existing.
* CTranslate2 dapat menemukan library CUDA 12 yang dibutuhkan.
* Dependency lock diperbarui sesuai mekanisme project.

## Stage 3 — Library Path Resolver

Buat resolver runtime yang mengambil lokasi library dari package Python:

```python
import os
import nvidia.cublas.lib
import nvidia.cudnn.lib

library_paths = [
    os.path.dirname(nvidia.cublas.lib.__file__),
    os.path.dirname(nvidia.cudnn.lib.__file__),
]
```

Gabungkan dengan `LD_LIBRARY_PATH` existing tanpa menghapus nilai lama.

Resolver harus:

* tidak hardcode path virtualenv
* tidak hardcode username atau lokasi project
* menghindari path duplikat
* gagal dengan error terstruktur jika package atau library tidak tersedia
* hanya diterapkan pada Linux

## Stage 4 — Integrasi Start Backend

Integrasikan library path ke mekanisme start backend existing.

Gunakan salah satu pola sesuai arsitektur existing:

```bash
export LD_LIBRARY_PATH="<cublas-path>:<cudnn-path>:${LD_LIBRARY_PATH}"
exec <backend-command-existing>
```

atau lakukan bootstrap sebelum import/inference CTranslate2.

Jangan membuat entrypoint paralel apabila start script existing dapat diperbaiki.

Pastikan environment sudah aktif sebelum worker melakukan inference faster-whisper.

## Stage 5 — Runtime Dependency Check

Tambahkan pemeriksaan:

```python
import ctypes

ctypes.CDLL("libcublas.so.12")
ctypes.CDLL("libcudnn.so.9")
```

Validasi juga:

```python
import ctranslate2

ctranslate2.get_cuda_device_count()
ctranslate2.get_supported_compute_types("cuda")
```

Dependency check harus lazy:

* PyTorch backend tidak gagal hanya karena dependency faster-whisper CUDA tidak tersedia.
* CPU faster-whisper tidak perlu menuntut CUDA library.
* Check CUDA 12 hanya dijalankan untuk `faster-whisper + cuda`.

## Stage 6 — Structured Error

Pertahankan kategori:

```text
dependency_incompatible
```

Bedakan detail:

```text
missing_cublas_cuda12
missing_cudnn9
cuda_device_unavailable
unsupported_compute_type
```

Error harus menyertakan:

* backend
* device
* compute type
* stage
* missing library
* remediation singkat

Jangan tampilkan stack trace penuh di UI user.

## Stage 7 — Validation

Jalankan dari environment start backend yang sebenarnya:

```python
import ctypes
import ctranslate2

ctypes.CDLL("libcublas.so.12")
ctypes.CDLL("libcudnn.so.9")

print(ctranslate2.get_cuda_device_count())
print(ctranslate2.get_supported_compute_types("cuda"))
```

Kemudian test:

```text
Backend: faster-whisper
Model: large-v3
Device: cuda
Compute Type: int8_float16
```

Acceptance:

* model berhasil dimuat
* inferensi benar-benar selesai, bukan hanya load
* transcript dan timestamp tersimpan
* tidak muncul `libcublas.so.12` atau `libcudnn.so.9` error
* worker tetap hidup
* backend PyTorch existing tetap lulus
* tidak ada model ganda tertinggal di GPU

Catat:

* peak VRAM
* peak RAM
* load duration
* inference duration
* status worker setelah inferensi

## Stage 8 — Regression

Jalankan:

* backend/adapter tests
* API tests
* web tests
* TypeScript
* production build
* `git diff --check`

Tambahkan regression test untuk:

* PyTorch tidak terpengaruh resolver CUDA 12
* faster-whisper CPU tidak menuntut CUDA library
* faster-whisper CUDA gagal terstruktur jika library hilang
* resolver mempertahankan `LD_LIBRARY_PATH` existing
* resolver tidak menduplikasi path

## Stage 9 — Documentation

Perbarui README:

* CTranslate2 terbaru memerlukan CUDA 12 cuBLAS dan cuDNN 9
* PyTorch CUDA 11.8 dapat tetap hidup berdampingan
* dependency yang dipasang
* bagaimana library path di-resolve
* perintah verifikasi
* troubleshooting `libcublas.so.12`
* troubleshooting `libcudnn.so.9`

## Final

Commit dan push ke branch serta draft PR yang sama.

Laporan akhir wajib berisi:

* root cause
* file yang berubah
* dependency yang ditambahkan
* mekanisme library-path final
* hasil load dan inferensi `large-v3`
* peak VRAM/RAM
* seluruh hasil test
* commit hash
* status branch dan working tree
