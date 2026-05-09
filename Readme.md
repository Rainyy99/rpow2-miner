# rpow2-miner

Bot mining RPOW2 untuk Android (Termux) — Samsung A15 Edition

Mining [RPOW2](https://rpow2.com) — tribute to Hal Finney's original
Reusable Proof of Work system. Fixed supply 21M tokens, community-mined.

## Fitur

- ⛏ Multi-akun paralel
- 📊 Telegram bot control lengkap
- 🔑 Update cookie mudah via script helper
- ⚠️ Alert session expired otomatis
- 📋 Laporan berkala tiap X jam
- 💓 Auto-reconnect & graceful shutdown
- 📝 Log ke file harian
- 📱 Optimized untuk Android/Termux

## Requirements

- Android (Termux)
- Python 3.x
- curl
- Akun rpow2.com

## Install

### 1. Install Termux
Download dari F-Droid (bukan Play Store):
https://f-droid.org/packages/com.termux/

### 2. Setup Termux
```bash
pkg update && pkg upgrade -y
pkg install python git curl -y
```

### 3. Clone repo
```bash
git clone https://github.com/USERNAME/rpow2-miner.git
cd rpow2-miner
```

### 4. Setup config
```bash
cp config.example.json config.json
nano config.json
```

Isi:
- `telegram_bot_token` — dari @BotFather di Telegram
- `allowed_chat_ids` — dari @userinfobot di Telegram
- `sessions` — cookie dari rpow2.com (lihat cara di bawah)

### 5. Jalankan
```bash
termux-wake-lock
python rpow2_miner.py
```

## Cara Dapat Cookie (Wajib sebelum mining)

```bash
# 1. Buka rpow2.com/#/login di browser
# 2. Masukkan email → klik Send
# 3. Cek email → long-press link → Copy link
# 4. Jalankan di Termux:
bash get_cookie.sh "https://api.rpow2.com/auth/verify?token=TOKEN"
```

Script otomatis:
- Hit magic link endpoint
- Dapat cookie session
- Simpan ke config.json
- Bot detect otomatis dalam ~10 detik

## Update Cookie (saat expired ~24 jam)

```bash
bash get_cookie.sh URL_MAGIC_LINK_BARU
```

Atau untuk akun ke-2:
```bash
bash get_cookie.sh URL 1
```

## Telegram Commands

| Tombol | Fungsi |
|--------|--------|
| 📊 Status | Statistik mining real-time |
| 💰 Balance | Cek balance semua akun |
| ⏸ Pause | Hentikan mining sementara |
| ▶️ Resume | Lanjutkan mining |
| 🔑 Update Cookie | Update cookie via Telegram |
| ➕ Tambah Akun | Daftarkan akun baru |