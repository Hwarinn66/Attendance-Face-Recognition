# Absen — absensi karyawan dengan verifikasi wajah

Sistem absensi karyawan: check-in/check-out lewat **aplikasi HP** dengan verifikasi wajah di server, dan
**web admin** untuk mengatur shift, pengguna, laporan, dan log aktivitas.

Verifikasi wajah berlapis dan semuanya diputuskan di server:

1. kualitas gambar;
2. tantangan **toleh kiri/kanan** acak;
3. konsistensi antar-frame;
4. deteksi wajah palsu pasif (PAD);
5. pencocokan wajah 1:1.

Sistem ini juga mencakup aturan jam kerja yang bisa diatur, jejak audit, dan alur privasi data biometrik.

| Bagian | Teknologi |
| --- | --- |
| Backend | Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, PostgreSQL 17 |
| Visi komputer | OpenCV (detektor YuNet), ONNX Runtime (pengenal SFace / ArcFace, PAD MiniFASNet), cukup CPU |
| Web admin | Next.js 16 (App Router), React 19, TypeScript 6 (strict), Tailwind CSS 4, TanStack Query |
| Aplikasi HP | Flutter (Android; iOS butuh Mac + Xcode) |
| Tes | pytest + HTTPX, Vitest + Testing Library, Playwright, flutter test |

Bacaan lanjutan (berbahasa Inggris):
- [ARCHITECTURE.md](ARCHITECTURE.md)
- [SECURITY.md](SECURITY.md)
- [docs/BIOMETRICS.md](docs/BIOMETRICS.md)
- [PROGRESS.md](PROGRESS.md): apa yang sudah dan belum terverifikasi.

> **Status:** sudah berfungsi dan dites (lihat PROGRESS.md), tetapi **belum tersertifikasi siap produksi**:
> - Model PAD belum dievaluasi dengan dataset serangan (foto/video/topeng).
> - Aplikasi tidak bisa membuktikan bahwa gambar benar-benar berasal dari kamera fisik.
>
> Baca SECURITY.md sebelum dipakai sungguhan.

---

## 1. Kebutuhan

* Python 3.12–3.13 dan [uv](https://docs.astral.sh/uv/) ≥ 0.13
* Node.js ≥ 20.9 (dikembangkan dengan versi 24) dan npm
* PostgreSQL 17. Pilih salah satu:
  * Docker;
  * server lokal;
  * versi portabel bawaan (`scripts/dev_postgres.py`).
* Flutter SDK dan Android SDK (Android Studio) untuk membangun aplikasi HP.
* Tidak perlu GPU.

## 2. Instalasi

```bash
cd backend && uv sync          # membuat backend/.venv dari uv.lock
cd ../frontend && npm ci       # memasang dependensi dari package-lock.json
cd ../mobile && flutter pub get
```

## 3. Konfigurasi lingkungan

Salin [.env.example](.env.example) ke `backend/.env`, lalu isi dengan nilai sebenarnya. Variabel wajib:

| Variabel | Keterangan |
| --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://user:pass@host:port/db` |
| `BIOMETRIC_ENCRYPTION_KEYS` | `kid:base64(32 byte)`. Buat dengan perintah di `.env.example` dan simpan di secret manager. |
| `ALLOWED_ORIGINS` | origin browser untuk web admin (HTTPS di produksi) |
| `COOKIE_SECURE` | `false` hanya untuk pengembangan di `http://localhost` |
| `PAD_MODE` | `required` (bawaan, wajib di produksi) atau `disabled` (hanya untuk pengembangan) |

Pengaturan liveness, semuanya opsional:

| Variabel | Bawaan | Keterangan |
| --- | --- | --- |
| `LIVENESS_CHALLENGE` | `active` | `active` = tantangan gerak kepala acak; `passive` = hanya menatap lurus (lebih lemah) |
| `CHALLENGE_ACTIONS` | `turn_left,turn_right` | Gerakan yang boleh diminta. `look_up`/`look_down` didukung tetapi nonaktif karena paling sering gagal di HP. |
| `CHALLENGE_STEPS` | `2` | Jumlah gerakan. Untuk kiri/kanan, 2 langkah memberi 4 kemungkinan urutan; 3 langkah memberi 8. |

Backend menolak menyala dengan konfigurasi produksi yang tidak aman, yaitu:
- cookie tidak aman;
- PAD dimatikan;
- origin kosong atau masih HTTP;
- provider visi palsu untuk tes;
- password seed pengembangan.

## 4. Menyiapkan database

Dengan PostgreSQL sendiri:

```bash
cd backend
uv run alembic upgrade head          # membuat skema
uv run alembic check                 # memastikan model dan migrasi cocok
uv run python -m app.cli create-admin --email admin@perusahaan.com   # meminta password yang kuat
```

Tanpa Docker atau instalasi PostgreSQL (Windows/Linux/macOS x86_64):

```bash
python scripts/dev_postgres.py init   # mengunduh PostgreSQL 17 (SHA-1 diverifikasi), berjalan di 127.0.0.1:55432
# URL koneksi untuk DATABASE_URL / TEST_DATABASE_URL / E2E_DATABASE_URL ditulis ke .local/pg.env
python scripts/dev_postgres.py start|stop|status
```

**Data contoh untuk pengembangan** (hanya saat `APP_ENV=development`):
1. Isi `DEV_SEED_ADMIN_EMAIL` dan `DEV_SEED_ADMIN_PASSWORD` dengan password yang kuat.
2. Jalankan `uv run python -m app.cli seed-dev`.

Tidak ada akun administrator bawaan.

**Masuk dengan ID:** pengguna bisa masuk dengan email atau dengan ID login opsional (huruf besar/kecil tidak
dibedakan).
- Perintah di bawah memberi sebuah akun ID login sekaligus password sementara.
- Perintah ini melewati aturan kekuatan password, jadi password pendek seperti `Admin` diterima.
- Perintah ini ditolak kecuali `APP_ENV=development`. Jangan pernah jalankan pada database yang akan dipakai
  di produksi.

```bash
uv run python -m app.cli dev-set-login --email admin@example.com --username Admin123 --password Admin
```

## 5. Bobot model dan lisensi

Bobot model tidak ikut di repo. Unduh dan verifikasi dengan SHA-256 terhadap
[backend/weights/manifest.json](backend/weights/manifest.json):

```bash
cd backend
uv run python scripts/fetch_models.py
# Konversi PAD sekali saja. PyTorch hanya dipakai di sini, tidak saat aplikasi berjalan.
# Di Linux tambahkan --index https://download.pytorch.org/whl/cpu agar tidak mengunduh versi CUDA.
uv run --no-project --python 3.13 --with torch==2.14.1 --with onnx==1.23.2 --with onnxscript \
       --with onnxruntime==1.31.0 --with "numpy<3" python scripts/convert_minifasnet.py
uv run python -m app.cli check-models         # keluar dengan kode 1 jika verifikasi tidak akan tersedia
```

| Peran | Model | Lisensi |
| --- | --- | --- |
| Detektor | OpenCV Zoo YuNet 2023mar | MIT |
| Pengenal (bawaan) | OpenCV Zoo SFace 2021dec, 128 dimensi | Apache-2.0 |
| Pengenal (opsional) | InsightFace ArcFace (`w600k_mbf`, `--with-arcface`) | **khusus riset non-komersial** |
| PAD | minivision MiniFASNetV2 + MiniFASNetV1SE (dikonversi ke ONNX, kesetaraan hasil dicek) | Apache-2.0 |

Rincian, ambang batas, dan hasil evaluasi ada di [docs/BIOMETRICS.md](docs/BIOMETRICS.md).

## 6. Menjalankan secara lokal

```bash
# terminal 1: API di http://127.0.0.1:8000 (dokumentasi OpenAPI di /api/v1/docs, kecuali di produksi)
cd backend && uv run uvicorn --factory app.main:app_factory --host 127.0.0.1 --port 8000 --reload --timeout-graceful-shutdown 5

# terminal 2: web admin di http://127.0.0.1:3000 (meneruskan /api ke API_ORIGIN, bawaan http://127.0.0.1:8000)
cd frontend && npm run dev
```

Dua aplikasi, dua peran:

* **Aplikasi HP** (`mobile/`). Semua orang memakainya untuk mendaftarkan wajah serta check-in dan check-out.
  - Administrator juga karyawan, jadi mereka absen dengan cara yang sama.
  - Endpoint absensi dan pendaftaran wajah hanya menerima sesi aplikasi (bearer token), jadi tidak bisa dipanggil
    dari sesi web.
* **Web admin**. Khusus administrator; akun karyawan ditolak saat login. Menunya:
  * **Ringkasan**: angka-angka hari ini.
    * Angka berubah **langsung (realtime)** tanpa refresh halaman ketika ada yang check-in atau check-out,
      koreksi diajukan atau ditinjau, atau absensi dihapus.
    * Muncul notifikasi dengan nama karyawan. Verifikasi wajah yang dicurigai memakai foto atau video
      memunculkan peringatan.
    * Lencana di samping logo menunjukkan *Langsung*, *Menyambung…*, atau *Terputus*. Selama koneksi
      langsung tidak tersedia, web memperbarui data tiap 30 detik.
  * **Absensi**: catatan absensi, pengecualian, koreksi, laporan dan ekspor CSV, serta pemeriksaan wajah.
    * Administrator bisa **menghapus** absensi yang tidak sah (misalnya check-in padahal tidak di kantor).
      Ada dua syarat: bukan absensinya sendiri, dan alasan wajib diisi.
    * Catatan itu hilang dari laporan, CSV, dan riwayat karyawan.
    * Salinan lengkap beserta alasannya tetap tersimpan di log aktivitas. Koreksi yang masih menunggu untuk
      hari itu otomatis ditolak.
  * **Pengguna**: profil, ID login dan password, shift, serta data wajah, semuanya dalam satu dialog.
  * **Jadwal**: shift, lokasi kerja, dan hari libur.
  * **Pengaturan**: aturan sistem dan log aktivitas (audit).

Password disimpan dalam bentuk hash (Argon2id). Administrator bisa mengatur atau mengganti password, dan
memilih apakah password itu wajib diganti saat login berikutnya. Password tidak pernah bisa dilihat.

Docker Compose sudah ditulis tetapi **belum diverifikasi** karena Docker tidak tersedia saat pengembangan.
Lihat bagian atas [docker-compose.yml](docker-compose.yml). `API_ORIGIN` ditanamkan ke web admin saat build.

### Cara verifikasi wajah bekerja

Saat check-in, check-out, atau pendaftaran wajah:
1. Aplikasi meminta tantangan ke server. Server memilih urutan gerakan secara acak, misalnya *toleh kiri →
   kembali ke tengah → toleh kanan → kembali ke tengah*.
2. Aplikasi menampilkan petunjuk dengan panah dan merekam frame wajah. Layar dibuat putih terang supaya wajah
   tetap tersinari di tempat yang agak gelap.
3. Frame dikirim ke server, yang memeriksa semuanya:

| Lapisan | Yang diperiksa |
| --- | --- |
| Kualitas | wajah terdeteksi, hanya satu orang, cukup besar, di tengah, tidak terlalu gelap/terang/buram |
| Tantangan gerak | urutan toleh kiri/kanan sesuai permintaan, selalu kembali ke tengah, dengan durasi minimum. Anggukan kecil saat menoleh diabaikan. |
| Kontinuitas | posisi wajah tidak melompat antar-frame, dan wajah lurus di awal, di antara gerakan, dan di akhir harus orang yang sama |
| PAD | model MiniFASNet menilai apakah wajah asli atau foto/layar; gagal tertutup jika model tidak tersedia |
| Pencocokan | wajah lurus dibandingkan dengan template terdaftar milik akun itu (1:1) |

Identitas hanya dinilai dari **wajah lurus**. Pada uji nyata di HP, kemiripan antara wajah yang menoleh dan
wajah lurus orang yang sama hanya 0,09, jauh di bawah ambang 0,363. Karena itu frame yang menoleh cukup dicek
kontinuitas posisinya.

Hasil verifikasi hanya bisa dipakai sekali, untuk akun dan sesi itu saja, maksimal 120 detik.

### Aplikasi HP (Flutter) — uji coba dengan laptop sebagai server

`mobile/` adalah aplikasi Android/iOS untuk karyawan: login, pendaftaran wajah, check-in dan check-out, serta
riwayat. Administrasi tetap di web.

**Cara aplikasi berkomunikasi dengan backend**

* Aplikasi login lewat `POST /api/v1/auth/token` dan mengirim `Authorization: Bearer <token>`. Pemeriksaan
  cookie, CSRF, dan Origin untuk web tidak berubah.
* HP hanya merekam frame dan semua keputusan diambil server. Aliran kamera depan diubah menjadi JPEG yang tegak
  dan tidak dicerminkan.
* Rotasi dikalibrasi otomatis: aplikasi mencoba 0/90/180/270° sampai `/biometrics/quality-check` menemukan wajah.

**Menjalankan uji coba**

```bash
# backend bisa diakses dari Wi-Fi (HP dan laptop di jaringan yang sama, profil jaringan Windows "Private")
cd backend && uv run uvicorn --factory app.main:app_factory --host 0.0.0.0 --port 8000 --timeout-graceful-shutdown 5

cd mobile
flutter test                      # tes unit + semua layar dirender terhadap backend tiruan
# Screenshot PNG setiap layar (font Roboto/Material Icons asli) untuk meninjau desain:
SCREENSHOT_DIR=/path/ke/folder flutter test test/screens_test.dart
flutter build apk --release       # -> build/app/outputs/flutter-apk/app-release.apk
```

Pasang APK di HP, lalu di pengaturan aplikasi isi alamat server dengan `http://<ip-laptop>:8000`.

**Catatan build uji coba**

* Manifest Android mengizinkan HTTP tanpa enkripsi (`usesCleartextTraffic`) hanya untuk uji coba di jaringan
  lokal ini. Build rilis ditandatangani dengan kunci debug.
* Untuk penggunaan sungguhan diperlukan:
  * server HTTPS;
  * kunci penandatanganan yang benar;
  * penghapusan `usesCleartextTraffic`.
* Build iOS membutuhkan Mac dengan Xcode dan akun Apple Developer.
* Shift dengan geofence (batas lokasi) belum didukung di aplikasi.
* Build rilis berjalan tanpa R8 (`shrink=false` di `mobile/android/gradle.properties`).
  * Alasannya: plugin berbasis JNI (`package:jni`, `shared_preferences_android`) mencari kelas, method, dan field
    Kotlin berdasarkan nama dari Dart, dan R8 tidak bisa melihat pemakaian itu.
  * Yang terjadi saat R8 aktif: kelas yang diganti namanya membuat aplikasi gagal saat dibuka, dengan
    `NoSuchMethodError` lalu `ClassNotFoundException` (terlihat di Samsung Galaxy A56).
  * Sebelum mengaktifkan R8 lagi, pertahankan semua kelas yang disebut paket-paket itu di Dart. Setelah itu periksa
    APK dengan `apkanalyzer dex packages --defined-only`.

## 7. Tes

```bash
cd backend
uv run pytest                 # unit + integrasi; butuh TEST_DATABASE_URL (nama database harus berakhiran _test)
uv run pytest -m models tests/models   # tes komponen dengan model ASLI (butuh bobot model yang sudah diunduh)

cd frontend
npm test                      # Vitest + Testing Library
npm run e2e                   # Playwright: menjalankan API dengan APP_ENV=test dan provider visi palsu
                              # berlabel, terhadap E2E_DATABASE_URL (harus berakhiran _e2e; isinya dikosongkan)

cd mobile
flutter analyze && flutter test
```

Tes integrasi dan E2E memakai `VISION_PROVIDER=fake`, yaitu **data uji** berlabel jelas.
- Yang diganti hanya keluaran model.
- Logika kualitas, tantangan, kontinuitas, dan keputusan tetap berjalan sungguhan.

Lulus tes ini **tidak** membuktikan akurasi biometrik di dunia nyata. Untuk itu, pakai `pytest -m models` dan
skrip evaluasi berikut:

```bash
uv run python scripts/evaluate_face_verification.py   # protokol LFW, FAR/FRR/EER (lihat docs/BIOMETRICS.md)
uv run python scripts/evaluate_pad.py <folder-dataset>  # APCER/BPCER/ACER pada dataset PAD berlisensi
```

## 8. Lint dan pemeriksaan tipe

```bash
cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy app
cd frontend && npm run lint && npm run typecheck && npm run build
cd mobile && flutter analyze
```

## 9. Mengatasi masalah di aplikasi HP

* **Aplikasi tidak bisa terhubung ke server**:
  * Pastikan HP dan laptop berada di Wi-Fi yang sama.
  * Pastikan profil jaringan Windows "Private" dan backend berjalan dengan `--host 0.0.0.0`.
  * IP laptop bisa berubah (misalnya setelah ganti hotspot). Perbarui alamat server di pengaturan aplikasi.
* **Kamera tidak bisa dibuka**: izinkan akses kamera untuk aplikasi Absen di Pengaturan HP, lalu tutup aplikasi
  lain yang sedang memakai kamera.
* **"Wajah tidak terbaca"**:
  * Pastikan cahaya merata dari depan, wajah di dalam lingkaran, dan hanya satu orang yang terlihat.
  * Sebelum menekan tombol mulai, layar pratinjau sudah memberi petunjuk.
* **"Gerakan kepala tidak terdeteksi sesuai urutan"**:
  * Mulai dengan wajah menghadap lurus.
  * Putar kepala dengan jelas (sekitar 25°) ke arah panah, lalu kembali ke tengah setiap kali.
  * Tahan HP tetap diam: yang bergerak kepala, bukan HP.
  * Urutan gerakan memang selalu berubah di setiap percobaan.
* **"Verifikasi gagal" berulang kali**: setelah 10 percobaan dalam 15 menit, akun dibatasi sementara. Jika wajah
  terkunci untuk ditinjau, administrator bisa mereset data wajah di menu **Pengguna**.

## 10. Mengatasi masalah bobot model

* `check-models` melaporkan `UNAVAILABLE (... not found)`: jalankan `fetch_models.py`, ditambah langkah konversi
  untuk PAD.
* `SHA-256 mismatch`: file rusak atau bukan versi yang dipatok. Hapus lalu unduh lagi. Layanan tidak pernah
  memuat model yang belum terverifikasi.
* `/api/v1/health/ready` menunjukkan `"verification": "unavailable"`: verifikasi absensi ditolak (gagal tertutup)
  sampai model yang hilang dipulihkan. Di produksi, readiness probe juga mengembalikan 503.
* `PAD_MODE=disabled` sengaja ditolak di produksi.

## 11. Pertimbangan untuk produksi

**Jaringan dan TLS**
* Pasang TLS di depan web admin. Set `COOKIE_SECURE=true` (cookie lalu memakai awalan `__Host-`) dan isi
  `ALLOWED_ORIGINS` dengan alamat HTTPS.
* Set `TRUST_FORWARDED_FOR=true` hanya di belakang proxy yang menimpa header `X-Forwarded-For`.

**Kunci, data, dan migrasi**
* Simpan `BIOMETRIC_ENCRYPTION_KEYS` di secret manager. Untuk rotasi, tambahkan kunci baru di depan lalu jalankan
  `python -m app.cli rotate-template-keys`.
* Jalankan `python -m app.cli purge` setiap hari (retensi data).
* Cadangkan PostgreSQL dengan enkripsi, dan batasi akses jaringan ke database.
* Jalankan migrasi sebagai langkah tersendiri (`alembic upgrade head`) sebelum meluncurkan versi backend baru.

**Biometrik dan hukum**
* Kalibrasi ambang pencocokan dan PAD dengan kamera dan populasi pengguna sendiri sebelum dipakai
  (docs/BIOMETRICS.md).
* Mintalah tinjauan hukum atas teks persetujuan biometrik sesuai yurisdiksi Anda.

**Batas percobaan:** disimpan di database sehingga berlaku bersama untuk semua instance, kecuali pembatas
pemeriksaan kualitas yang berlaku per proses.

**Dashboard realtime** (Server-Sent Events di `/api/v1/events/attendance`):
* Reverse proxy tidak boleh menahan (buffer) path itu.
  * Respons sudah mengirim `X-Accel-Buffering: no` (dipatuhi nginx) dan `Cache-Control: no-transform`.
  * Batas waktu idle proxy harus lebih lama dari heartbeat 15 detik.
* Setiap proses backend memakai satu koneksi PostgreSQL tambahan (`LISTEN`) selama ada minimal satu web admin
  yang terbuka.
* Setiap stream berakhir tiap `LIVE_EVENTS_STREAM_SECONDS` (120 detik) dan browser menyambung ulang. Meski begitu,
  jalankan uvicorn dengan `--timeout-graceful-shutdown` (Dockerfile memakai 10 detik) agar stream yang terbuka
  tidak menunda restart.
