# rpow2-miner

Bot mining RPOW2 untuk Android (Termux) — Samsung A15 Edition

Mining [RPOW2](https://rpow2.com) — tribute to Hal Finney's Reusable Proof of Work.
Fixed supply 21M tokens, community-mined via SHA256 Proof of Work.

## ⚡ Fitur

- 🚀 **Native C miner** — ~1.5M H/s per worker (20x lebih cepat dari Python)
- 🐍 **Python fallback** — otomatis kalau C binary tidak tersedia
- 🧵 **Multi-thread** — configurable 1/2/4/6/8 thread via Telegram
- 📊 **Telegram bot control** — status, balance, pause, resume
- 🔑 **Cookie helper script** — update cookie 1 command
- ⚠️ **Alert session expired** otomatis
- 📋 **Laporan berkala** tiap X jam
- 💓 **Auto-reconnect** & graceful shutdown
- 📝 **Log harian** ke file
- 📱 **Optimized** untuk Android/Termux

## 📊 Perbandingan Hashrate

| Mode | Hashrate | Token/jam |
|------|----------|-----------|
| Python 1 thread | ~340k H/s | ~24 |
| C native 1 thread | ~1.5M H/s | ~71 |
| C native 4 thread | ~5-6M H/s | ~280 |

## 📱 Requirements

- Android dengan Termux (F-Droid)
- Python 3.x
- curl
- clang (untuk compile C miner)
- Akun rpow2.com

## 🚀 Install

### 1. Install Termux
Download dari F-Droid (bukan Play Store):
https://f-droid.org/packages/com.termux/

### 2. Setup Termux
```bash
pkg update && pkg upgrade -y
pkg install python git curl clang -y
```

### 3. Clone repo
```bash
git clone https://github.com/USERNAME/rpow2-miner.git
cd rpow2-miner
```

### 4. Compile C miner (opsional tapi sangat direkomendasikan)
```bash
# Patch timing untuk Android compatibility
sed -i 's/args\[i\]\.cutoff_ms = cutoff_ms;/args[i].cutoff_ms = cutoff_ms ? (started + cutoff_ms) : 0;/' rpow-native-miner.c

# Compile
clang -O3 -o rpow-native-miner rpow-native-miner.c -lpthread
```

### 5. Setup config
```bash
cp config.example.json config.json
nano config.json
```

Isi:
- `telegram_bot_token` — dari @BotFather
- `allowed_chat_ids` — dari @userinfobot
- `mining_threads` — 1 (default), naikkan setelah test
- `sessions` — cookie dari rpow2.com (lihat cara di bawah)

### 6. Jalankan
```bash
termux-wake-lock
python rpow2_miner.py
```

## 🔑 Cara Dapat Cookie

```bash
# 1. Buka rpow2.com/#/login di browser
# 2. Masukkan email → Send
# 3. Cek email → long-press link → Copy
# 4. Jalankan:
bash get_cookie.sh "https://api.rpow2.com/auth/verify?token=..."
```

Script otomatis hit magic link, dapat cookie, simpan ke config.json.
Bot detect otomatis dalam ~10 detik tanpa restart.

## 🔄 Update Cookie (saat expired ~24 jam)

```bash
bash get_cookie.sh URL_MAGIC_LINK_BARU
```

## 📱 Telegram Commands

| Tombol | Fungsi |
|--------|--------|
| 📊 Status | Statistik + engine info |
| 💰 Balance | Cek balance semua akun |
| ⏸ Pause / ▶️ Resume | Kontrol mining |
| ⚙️ Threads | Atur 1/2/4/6/8 thread |
| 🔑 Update Cookie | Update cookie |
| ➕ Tambah Akun | Daftarkan akun baru |
