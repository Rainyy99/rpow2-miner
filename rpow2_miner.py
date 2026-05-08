#!/usr/bin/env python3
"""
RPOW2 Mining Bot - Android/Termux Edition
Samsung Galaxy A15 Optimized
Fitur: Multi-akun, Telegram control, Update cookie via Telegram
"""

import hashlib, struct, time, json, http.client, ssl
import urllib.request, urllib.parse, threading, logging
import os, sys, signal
from datetime import datetime

# ══════════════════════════════════════════
#  LOAD CONFIG
# ══════════════════════════════════════════
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config():
    if not os.path.exists(CONFIG_FILE):
        print("❌ config.json tidak ditemukan!")
        sys.exit(1)
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

CFG             = load_config()
BOT_TOKEN       = CFG["telegram_bot_token"]
ALLOWED_IDS     = set(str(x) for x in CFG["allowed_chat_ids"])
SESSIONS        = CFG["sessions"]          # list, bisa diupdate saat runtime
API_BASE        = CFG.get("api_base", "api.rpow2.com")
CHALLENGE_TO    = CFG.get("challenge_timeout_sec", 300)
REPORT_INTERVAL = CFG.get("report_interval_hours", 2) * 3600

# ══════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            f"logs/bot_{datetime.now().strftime('%Y%m%d')}.log",
            encoding="utf-8"
        )
    ]
)
log = logging.getLogger("rpow2")

# ══════════════════════════════════════════
#  SSL
# ══════════════════════════════════════════
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode    = ssl.CERT_NONE

# ══════════════════════════════════════════
#  GLOBAL STATE
# ══════════════════════════════════════════
sessions_lock = threading.Lock()
stats_lock    = threading.Lock()
mining_active = threading.Event()
mining_active.set()

global_stats = {
    "total_mined": 0,
    "start_time" : time.time(),
    "accounts"   : {}
}

# {chat_id: acc_index} — user yang sedang nunggu input cookie baru
waiting_cookie = {}
waiting_lock   = threading.Lock()

# ══════════════════════════════════════════
#  TELEGRAM HELPERS
# ══════════════════════════════════════════
def tg_request(method, payload):
    try:
        url  = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
        data = json.dumps(payload).encode()
        req  = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        log.warning(f"Telegram {method} error: {e}")
        return {}

def tg_send(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    tg_request("sendMessage", payload)

def tg_notify_all(text, keyboard=None):
    for cid in ALLOWED_IDS:
        tg_send(cid, text, keyboard)

def tg_answer_cb(cb_id, text="✅"):
    tg_request("answerCallbackQuery", {
        "callback_query_id": cb_id,
        "text": text
    })

# ══════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════
def main_keyboard():
    return [
        [
            {"text": "📊 Status",       "callback_data": "cmd_status"},
            {"text": "💰 Balance",      "callback_data": "cmd_balance"},
        ],
        [
            {"text": "⏸ Pause",        "callback_data": "cmd_stop"},
            {"text": "▶️ Resume",       "callback_data": "cmd_resume"},
        ],
        [
            {"text": "🔑 Update Cookie","callback_data": "cmd_update_cookie"},
        ],
        [
            {"text": "❓ Help",         "callback_data": "cmd_help"},
        ]
    ]

def account_select_keyboard():
    """Keyboard pilih akun untuk update cookie"""
    rows = []
    with sessions_lock:
        sessions_copy = list(SESSIONS)
    for i, session in enumerate(sessions_copy):
        me = api_call("GET", "/me", session)
        if "error" not in me:
            label = me.get("email", f"Akun #{i+1}")
        else:
            label = f"Akun #{i+1} ❌ expired"
        rows.append([{
            "text"         : f"📧 {label}",
            "callback_data": f"cmd_pick_acc_{i}"
        }])
    rows.append([{"text": "❌ Batal", "callback_data": "cmd_cancel"}])
    return rows

# ══════════════════════════════════════════
#  API HELPER
# ══════════════════════════════════════════
def api_call(method, path, session, data=None, retries=5):
    for attempt in range(retries):
        try:
            conn    = http.client.HTTPSConnection(API_BASE, context=ctx, timeout=30)
            headers = {"Cookie": session}
            body    = None
            if method == "POST":
                headers["Content-Type"] = "application/json"
                body = json.dumps(data if data is not None else {})
            conn.request(method, path, body=body, headers=headers)
            r      = conn.getresponse()
            result = r.read().decode()
            conn.close()
            return json.loads(result)
        except (ssl.SSLEOFError, ssl.SSLError, ConnectionResetError, OSError):
            if attempt < retries - 1:
                time.sleep(1 + attempt)
                continue
            return {"error": "SSL_ERROR"}
        except json.JSONDecodeError:
            if attempt < retries - 1:
                time.sleep(1)
                continue
            return {"error": "JSON_ERROR"}
        except Exception as e:
            return {"error": "EXCEPTION", "message": str(e)}

# ══════════════════════════════════════════
#  MINING ENGINE
# ══════════════════════════════════════════
def count_trailing_zero_bits(data):
    bits = 0
    for byte in reversed(data):
        if byte == 0:
            bits += 8
            continue
        b = byte
        while b & 1 == 0:
            bits += 1
            b >>= 1
        break
    return bits

def mine(nonce_prefix_hex, difficulty_bits, label, timeout=None):
    if timeout is None:
        timeout = CHALLENGE_TO
    nonce_prefix = bytes.fromhex(nonce_prefix_hex)
    buffer       = bytearray(nonce_prefix + b"\x00" * 8)
    nonce        = 0
    hashes       = 0
    start        = time.time()
    last_report  = start

    while True:
        if not mining_active.is_set():
            log.info(f"[{label}] ⏸ Dijeda...")
            mining_active.wait()
            log.info(f"[{label}] ▶️ Dilanjutkan!")

        if time.time() - start > timeout:
            log.info(f"[{label}] ⏰ Timeout, ambil challenge baru")
            return None

        struct.pack_into("<Q", buffer, len(nonce_prefix), nonce)
        digest = hashlib.sha256(buffer).digest()

        if count_trailing_zero_bits(digest) >= difficulty_bits:
            elapsed = time.time() - start
            rate    = hashes / elapsed if elapsed > 0 else 0
            log.info(f"[{label}] 🎯 KETEMU! {hashes:,} hashes | {elapsed:.1f}s | {rate:,.0f} H/s")
            return nonce

        nonce  += 1
        hashes += 1

        now = time.time()
        if now - last_report >= 15:
            elapsed = now - start
            rate    = hashes / elapsed if elapsed > 0 else 0
            log.info(f"[{label}] ⛏  {hashes:,} | {rate:,.0f} H/s | {elapsed:.0f}s")
            last_report = now

# ══════════════════════════════════════════
#  MINING WORKER (per akun)
# ══════════════════════════════════════════
def mine_worker(acc_index):
    label = f"Acc{acc_index + 1}"

    def get_session():
        with sessions_lock:
            return SESSIONS[acc_index]

    # Login check awal
    me = api_call("GET", "/me", get_session())
    if "error" in me:
        tg_notify_all(
            f"❌ <b>Login Gagal — Akun #{acc_index + 1}</b>\n\n"
            f"Cookie expired atau salah.\n"
            f"Tekan 🔑 Update Cookie di menu bot."
        )
        log.error(f"[{label}] Login gagal")

        # Tetap loop, tunggu cookie diupdate via Telegram
        while True:
            time.sleep(30)
            me = api_call("GET", "/me", get_session())
            if "error" not in me:
                log.info(f"[{label}] Cookie baru valid, mining dimulai!")
                break

    email       = me["email"]
    local_mined = 0
    start_time  = time.time()
    warned      = False

    with stats_lock:
        global_stats["accounts"][email] = {
            "mined"     : 0,
            "start_time": start_time,
            "last_token": "-",
            "status"    : "⛏ Mining",
            "acc_index" : acc_index
        }

    log.info(f"[{label}] ✅ Login: {email} | Balance: {me['balance']}")

    while True:
        try:
            mining_active.wait()

            current_session = get_session()
            challenge = api_call("POST", "/challenge", current_session)

            if "error" in challenge:
                msg = str(challenge.get("message", ""))
                if any(k in msg.lower() for k in ("unauthorized","login","session","expired","401")):
                    if not warned:
                        warned = True
                        with stats_lock:
                            if email in global_stats["accounts"]:
                                global_stats["accounts"][email]["status"] = "⚠️ Expired"
                        tg_notify_all(
                            f"⚠️ <b>Session Expired!</b>\n\n"
                            f"📧 <code>{email}</code>\n\n"
                            f"Tekan tombol di bawah untuk update cookie:",
                            main_keyboard()
                        )
                    time.sleep(30)
                    continue
                time.sleep(5)
                continue

            # Cookie valid, reset warning
            if warned:
                warned = False
                # Update email kalau ganti akun
                me2 = api_call("GET", "/me", get_session())
                if "error" not in me2:
                    email = me2["email"]

            cid    = challenge["challenge_id"]
            diff   = challenge["difficulty_bits"]
            prefix = challenge["nonce_prefix"]

            log.info(f"[{label}] 📋 Challenge: {cid[:16]}... | {diff} bits")

            with stats_lock:
                if email in global_stats["accounts"]:
                    global_stats["accounts"][email]["status"] = f"⛏ Mining ({diff} bits)"

            solution = mine(prefix, diff, label)
            if solution is None:
                continue

            result = api_call("POST", "/mint", current_session,
                              {"challenge_id": cid, "solution_nonce": str(solution)})

            if "error" in result:
                if "expired" in str(result.get("message","")).lower():
                    continue
                time.sleep(2)
                continue

            local_mined += 1
            token = result.get("token", {}).get("id", "?")

            with stats_lock:
                global_stats["total_mined"] += 1
                if email in global_stats["accounts"]:
                    global_stats["accounts"][email]["mined"]      = local_mined
                    global_stats["accounts"][email]["last_token"] = token
                    global_stats["accounts"][email]["status"]     = "✅ Idle"
                total = global_stats["total_mined"]

            elapsed = time.time() - start_time
            rate    = local_mined / (elapsed / 3600) if elapsed > 0 else 0
            tok_short = token[:24] + "..." if len(token) > 24 else token

            log.info(f"[{label}] ✅ Token #{local_mined} | Total: {total}")

            tg_notify_all(
                f"⛏ <b>Token Berhasil Di-mine!</b>\n\n"
                f"📧 {email}\n"
                f"🎫 <code>{tok_short}</code>\n"
                f"🏠 Lokal #{local_mined}  |  🌐 Total #{total}\n"
                f"📈 {rate:.1f} token/jam\n"
                f"🕐 {datetime.now().strftime('%H:%M:%S')}"
            )

        except Exception as e:
            log.error(f"[{label}] Error: {e}")
            time.sleep(5)

# ══════════════════════════════════════════
#  STATUS & BALANCE
# ══════════════════════════════════════════
def build_status():
    with stats_lock:
        uptime = int(time.time() - global_stats["start_time"])
        h, rem = divmod(uptime, 3600)
        m      = rem // 60
        total  = global_stats["total_mined"]
        accs   = dict(global_stats["accounts"])
    state = "▶️ Running" if mining_active.is_set() else "⏸ Paused"

    lines = [
        f"📊 <b>RPOW2 Status</b>",
        f"",
        f"{state}  |  Uptime: {h}j {m}m",
        f"🌐 Total mined: <b>{total} token</b>",
        f""
    ]
    for email, s in accs.items():
        acc_up = int(time.time() - s["start_time"])
        rate   = s["mined"] / (acc_up / 3600) if acc_up > 0 else 0
        tok    = s["last_token"]
        tok_s  = tok[:20] + "..." if len(tok) > 20 else tok
        lines.append(
            f"👤 <b>{email.split('@')[0]}</b>\n"
            f"   {s['status']}  |  {s['mined']} token  |  {rate:.1f}/jam\n"
            f"   Last: <code>{tok_s}</code>"
        )
    return "\n".join(lines)

def build_balance():
    lines = ["💰 <b>Balance Semua Akun</b>\n"]
    with sessions_lock:
        sessions_copy = list(SESSIONS)
    for i, session in enumerate(sessions_copy, 1):
        me = api_call("GET", "/me", session)
        if "error" not in me:
            lines.append(
                f"📧 {me['email']}\n"
                f"   💵 Balance: <b>{me['balance']}</b>  |  Minted: {me['minted']}"
            )
        else:
            lines.append(f"Akun #{i}: ❌ Session expired")
    return "\n\n".join(lines)

# ══════════════════════════════════════════
#  UPDATE COOKIE HANDLER
# ══════════════════════════════════════════
def process_new_cookie(chat_id, acc_index, raw_cookie):
    """Validasi dan terapkan cookie baru"""
    new_cookie = raw_cookie.strip()

    # Validasi format
    if not new_cookie.startswith("rpow_session="):
        tg_send(chat_id,
            "❌ <b>Format salah!</b>\n\n"
            "Cookie harus dimulai dengan:\n"
            "<code>rpow_session=eyJ...</code>\n\n"
            "Coba kirim ulang cookie yang benar.",
            main_keyboard()
        )
        return

    # Test cookie
    tg_send(chat_id, "⏳ Mengecek cookie baru...")
    me = api_call("GET", "/me", new_cookie)

    if "error" in me:
        tg_send(chat_id,
            "❌ <b>Cookie tidak valid!</b>\n\n"
            "Pastikan:\n"
            "1. Sudah login di rpow2.com\n"
            "2. Copy yang benar dari DevTools\n"
            "3. Format: <code>rpow_session=eyJ...</code>",
            main_keyboard()
        )
        return

    email = me.get("email", "?")

    # Terapkan cookie baru di memori
    with sessions_lock:
        SESSIONS[acc_index] = new_cookie

    # Simpan permanen ke config.json
    try:
        with open(CONFIG_FILE, "r") as f:
            cfg_data = json.load(f)
        cfg_data["sessions"][acc_index] = new_cookie
        save_config(cfg_data)
        saved = "✅ Tersimpan permanen ke config.json"
    except Exception as e:
        saved = f"⚠️ Gagal simpan ke file: {e}"

    # Update status di global_stats
    with stats_lock:
        for em, s in global_stats["accounts"].items():
            if s.get("acc_index") == acc_index:
                global_stats["accounts"][em]["status"] = "🔄 Cookie updated"
                break

    log.info(f"Cookie Akun #{acc_index + 1} diupdate via Telegram → {email}")

    tg_send(chat_id,
        f"✅ <b>Cookie Berhasil Diupdate!</b>\n\n"
        f"📧 Akun: {email}\n"
        f"💵 Balance: {me.get('balance','?')}\n"
        f"🎫 Minted: {me.get('minted','?')}\n\n"
        f"{saved}\n\n"
        f"⛏ Mining akan otomatis dilanjutkan.",
        main_keyboard()
    )

# ══════════════════════════════════════════
#  COMMAND HANDLER
# ══════════════════════════════════════════
def handle_cmd(chat_id, cmd):
    cmd = cmd.strip().lower().split("@")[0]

    if cmd in ("/start", "/menu", "cmd_menu"):
        tg_send(chat_id,
            "👋 <b>RPOW2 Miner Bot</b>\n\nPilih aksi:",
            main_keyboard()
        )

    elif cmd in ("/status", "cmd_status"):
        tg_send(chat_id, build_status(), main_keyboard())

    elif cmd in ("/balance", "cmd_balance"):
        tg_send(chat_id, "⏳ Mengambil balance...")
        tg_send(chat_id, build_balance(), main_keyboard())

    elif cmd in ("/stop", "cmd_stop"):
        mining_active.clear()
        tg_send(chat_id,
            "⏸ <b>Mining dijeda.</b>\n"
            "Tekan ▶️ Resume untuk lanjut.",
            main_keyboard()
        )

    elif cmd in ("/resume", "cmd_resume"):
        mining_active.set()
        tg_send(chat_id, "▶️ <b>Mining dilanjutkan!</b>", main_keyboard())

    elif cmd in ("/updatecookie", "cmd_update_cookie"):
        tg_send(chat_id,
            "🔑 <b>Update Cookie</b>\n\n"
            "Pilih akun yang cookienya mau diupdate:",
            account_select_keyboard()
        )

    elif cmd in ("/cancel", "cmd_cancel"):
        with waiting_lock:
            waiting_cookie.pop(chat_id, None)
        tg_send(chat_id, "❌ Dibatalkan.", main_keyboard())

    elif cmd in ("/help", "cmd_help"):
        tg_send(chat_id,
            "❓ <b>Daftar Command</b>\n\n"
            "/status       — Statistik real-time\n"
            "/balance      — Cek balance semua akun\n"
            "/stop         — Pause mining\n"
            "/resume       — Lanjutkan mining\n"
            "/updatecookie — Update cookie akun\n"
            "/help         — Bantuan ini\n\n"
            "<i>Atau pakai tombol di bawah 👇</i>",
            main_keyboard()
        )

    else:
        # Cek apakah ini pilih akun untuk update cookie
        if cmd.startswith("cmd_pick_acc_"):
            try:
                acc_index = int(cmd.replace("cmd_pick_acc_", ""))
                with sessions_lock:
                    valid_index = 0 <= acc_index < len(SESSIONS)

                if not valid_index:
                    tg_send(chat_id, "❌ Akun tidak valid.", main_keyboard())
                    return

                with waiting_lock:
                    waiting_cookie[chat_id] = acc_index

                # Ambil info akun yang dipilih
                with sessions_lock:
                    session = SESSIONS[acc_index]
                me = api_call("GET", "/me", session)
                email_info = me.get("email","?") if "error" not in me else "Session expired"

                tg_send(chat_id,
                    f"🔑 <b>Update Cookie — Akun #{acc_index + 1}</b>\n"
                    f"📧 {email_info}\n\n"
                    f"Sekarang buka browser, login ke rpow2.com:\n\n"
                    f"<b>Langkah:</b>\n"
                    f"1. Buka rpow2.com → Login\n"
                    f"2. Tap F12 / DevTools\n"
                    f"3. Application → Cookies\n"
                    f"4. Copy nilai <code>rpow_session</code>\n\n"
                    f"Lalu <b>kirim cookie</b> ke sini (paste langsung):\n"
                    f"Format: <code>rpow_session=eyJ...</code>\n\n"
                    f"Atau /cancel untuk batal."
                )
            except ValueError:
                tg_send(chat_id, "❌ Error memilih akun.", main_keyboard())
        else:
            tg_send(chat_id, "❓ Tidak dikenal. Ketik /help", main_keyboard())

# ══════════════════════════════════════════
#  TELEGRAM POLLING
# ══════════════════════════════════════════
def telegram_polling():
    offset = None
    log.info("📡 Telegram polling aktif...")

    while True:
        try:
            params = {"timeout": 30, "limit": 10}
            if offset:
                params["offset"] = offset
            url = (f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?"
                   + urllib.parse.urlencode(params))
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=40) as r:
                updates = json.loads(r.read()).get("result", [])

            for upd in updates:
                offset = upd["update_id"] + 1

                # ── Pesan teks ──────────────────────────
                if "message" in upd:
                    msg     = upd["message"]
                    chat_id = str(msg["chat"]["id"])
                    text    = msg.get("text", "").strip()

                    if chat_id not in ALLOWED_IDS:
                        tg_send(chat_id, "⛔ Akses ditolak.")
                        log.warning(f"Ditolak: {chat_id}")
                        continue

                    # Cek apakah user lagi nunggu input cookie
                    with waiting_lock:
                        waiting = chat_id in waiting_cookie
                        acc_idx = waiting_cookie.get(chat_id)

                    if waiting and not text.startswith("/"):
                        # Ini input cookie baru
                        with waiting_lock:
                            waiting_cookie.pop(chat_id, None)
                        process_new_cookie(chat_id, acc_idx, text)
                    elif text.startswith("/"):
                        handle_cmd(chat_id, text)
                    else:
                        tg_send(chat_id,
                            "Ketik /help untuk daftar command, atau pilih menu:",
                            main_keyboard()
                        )

                # ── Inline keyboard callback ────────────
                elif "callback_query" in upd:
                    cb      = upd["callback_query"]
                    chat_id = str(cb["message"]["chat"]["id"])
                    data    = cb.get("data", "")

                    if chat_id not in ALLOWED_IDS:
                        tg_answer_cb(cb["id"], "⛔ Ditolak")
                        continue

                    tg_answer_cb(cb["id"])
                    handle_cmd(chat_id, data)

        except Exception as e:
            log.warning(f"Polling error: {e}")
            time.sleep(5)

# ══════════════════════════════════════════
#  AUTO REPORT
# ══════════════════════════════════════════
def auto_report():
    while True:
        time.sleep(REPORT_INTERVAL)
        try:
            tg_notify_all(
                f"📋 <b>Laporan Berkala</b>\n\n" + build_status(),
                main_keyboard()
            )
        except Exception as e:
            log.warning(f"Auto-report error: {e}")

# ══════════════════════════════════════════
#  GRACEFUL SHUTDOWN
# ══════════════════════════════════════════
def on_shutdown(sig, frame):
    with stats_lock:
        total = global_stats["total_mined"]
    uptime = int(time.time() - global_stats["start_time"])
    h, rem = divmod(uptime, 3600)
    m      = rem // 60
    log.info(f"\n🛑 Bot dihentikan. Total: {total} | Uptime: {h}j {m}m")
    tg_notify_all(
        f"🛑 <b>Bot Dihentikan</b>\n\n"
        f"📊 Total mined: {total} token\n"
        f"⏱ Uptime: {h}j {m}m"
    )
    sys.exit(0)

signal.signal(signal.SIGINT,  on_shutdown)
signal.signal(signal.SIGTERM, on_shutdown)

# ══════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════
def main():
    log.info("=" * 52)
    log.info("  RPOW2 Mining Bot — Termux/Android Edition")
    log.info("=" * 52)

    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN":
        log.error("❌ Isi telegram_bot_token di config.json dulu!")
        sys.exit(1)

    if not SESSIONS or SESSIONS[0] == "rpow_session=YOUR_COOKIE_HERE":
        log.error("❌ Isi sessions (cookie) di config.json dulu!")
        sys.exit(1)

    # Validasi semua akun
    log.info(f"  Memeriksa {len(SESSIONS)} akun...")
    for i, session in enumerate(SESSIONS):
        me = api_call("GET", "/me", session)
        if "error" not in me:
            log.info(f"  Akun #{i+1}: ✅ {me['email']} | Balance: {me['balance']}")
        else:
            log.warning(f"  Akun #{i+1}: ❌ Session expired — gunakan /updatecookie di Telegram")

    # Notif startup
    tg_notify_all(
        f"🚀 <b>RPOW2 Bot Started!</b>\n\n"
        f"⛏ {len(SESSIONS)} akun dimuat\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"Gunakan menu di bawah untuk kontrol:",
        main_keyboard()
    )

    # Thread Telegram polling
    threading.Thread(target=telegram_polling, daemon=True).start()

    # Thread auto-report
    threading.Thread(target=auto_report, daemon=True).start()

    # Thread mining per akun
    for i in range(len(SESSIONS)):
        threading.Thread(target=mine_worker, args=(i,), daemon=True).start()
        time.sleep(0.5)

    log.info(f"\n🚀 Semua thread aktif. Ctrl+C untuk stop.\n")

    # Keep alive + heartbeat
    while True:
        time.sleep(60)
        with stats_lock:
            total = global_stats["total_mined"]
        log.info(f"💓 Heartbeat | Total mined: {total}")

if __name__ == "__main__":
    main()
