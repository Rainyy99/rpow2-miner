#!/usr/bin/env python3
"""
RPOW2 Mining Bot - Android/Termux Edition
Samsung Galaxy A15 Optimized

Changelog:
- v1.0: Basic mining bot
- v2.0: Telegram bot control + cookie helper
- v3.0: Native C miner integration (20x hashrate boost)
        Multi-thread configurable (1/2/4/6/8)
        Auto-fallback ke Python kalau C binary tidak ada

GitHub: github.com/USERNAME/rpow2-miner
"""

import hashlib, struct, time, json, http.client, ssl
import urllib.request, urllib.parse, threading, logging
import os, sys, signal, subprocess
from datetime import datetime

# ══════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config():
    if not os.path.exists(CONFIG_FILE):
        print("❌ config.json tidak ditemukan!")
        print("   Buat: cp config.example.json config.json")
        sys.exit(1)
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
        return True
    except Exception as e:
        log.error(f"Gagal simpan config: {e}")
        return False

CFG             = load_config()
BOT_TOKEN       = CFG["telegram_bot_token"]
ALLOWED_IDS     = set(str(x) for x in CFG["allowed_chat_ids"])
SESSIONS        = CFG["sessions"]
API_BASE        = CFG.get("api_base", "api.rpow2.com")
CHALLENGE_TO    = CFG.get("challenge_timeout_sec", 600)
REPORT_INTERVAL = CFG.get("report_interval_hours", 2) * 3600
MINING_THREADS  = CFG.get("mining_threads", 1)

# ══════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════
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

# ══════════════════════════════════════════════════════
#  SSL
# ══════════════════════════════════════════════════════
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode    = ssl.CERT_NONE

# ══════════════════════════════════════════════════════
#  GLOBAL STATE
# ══════════════════════════════════════════════════════
sessions_lock = threading.Lock()
stats_lock    = threading.Lock()
mining_active = threading.Event()
mining_active.set()

global_stats = {
    "total_mined": 0,
    "start_time" : time.time(),
    "accounts"   : {}
}

user_state      = {}
user_state_lock = threading.Lock()

# ══════════════════════════════════════════════════════
#  TELEGRAM HELPERS
# ══════════════════════════════════════════════════════
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
    payload = {
        "chat_id"   : chat_id,
        "text"      : text,
        "parse_mode": "HTML"
    }
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    tg_request("sendMessage", payload)

def tg_notify_all(text, keyboard=None):
    for cid in ALLOWED_IDS:
        tg_send(cid, text, keyboard)

def tg_answer_cb(cb_id, text="✅"):
    tg_request("answerCallbackQuery", {
        "callback_query_id": cb_id,
        "text"             : text
    })

# ══════════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════════
def main_keyboard():
    return [
        [
            {"text": "📊 Status",                        "callback_data": "cmd_status"},
            {"text": "💰 Balance",                       "callback_data": "cmd_balance"},
        ],
        [
            {"text": "⏸ Pause",                         "callback_data": "cmd_stop"},
            {"text": "▶️ Resume",                        "callback_data": "cmd_resume"},
        ],
        [
            {"text": f"⚙️ Threads ({MINING_THREADS})",   "callback_data": "cmd_threads"},
        ],
        [
            {"text": "🔑 Update Cookie",                 "callback_data": "cmd_update_cookie"},
        ],
        [
            {"text": "➕ Tambah Akun",                   "callback_data": "cmd_add_account"},
            {"text": "❓ Help",                          "callback_data": "cmd_help"},
        ]
    ]

def account_select_keyboard(mode="cookie"):
    rows = []
    with sessions_lock:
        sessions_copy = list(SESSIONS)
    for i, session in enumerate(sessions_copy):
        me = api_call("GET", "/me", session)
        if "error" not in me:
            label  = me.get("email", f"Akun #{i+1}")
            status = "✅"
        else:
            label  = f"Akun #{i+1}"
            status = "❌"
        rows.append([{
            "text"         : f"{status} {label}",
            "callback_data": f"cmd_pick_{mode}_{i}"
        }])
    rows.append([{"text": "❌ Batal", "callback_data": "cmd_cancel"}])
    return rows

# ══════════════════════════════════════════════════════
#  API CALL
# ══════════════════════════════════════════════════════
def api_call(method, path, session, data=None, retries=5):
    for attempt in range(retries):
        try:
            conn    = http.client.HTTPSConnection(API_BASE, context=ctx, timeout=60)
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
        except (ssl.SSLEOFError, ssl.SSLError, ConnectionResetError, OSError) as e:
            if attempt < retries - 1:
                time.sleep(1 + attempt)
                continue
            return {"error": "SSL_ERROR", "message": str(e)}
        except json.JSONDecodeError:
            if attempt < retries - 1:
                time.sleep(1)
                continue
            return {"error": "JSON_ERROR"}
        except Exception as e:
            return {"error": "EXCEPTION", "message": str(e)}

# ══════════════════════════════════════════════════════
#  SESSION MANAGEMENT
# ══════════════════════════════════════════════════════
def apply_new_session(chat_id, acc_index, new_cookie, email):
    is_new = False
    with sessions_lock:
        if acc_index is None or acc_index >= len(SESSIONS):
            SESSIONS.append(new_cookie)
            acc_index = len(SESSIONS) - 1
            is_new = True
        else:
            SESSIONS[acc_index] = new_cookie

    try:
        cfg_data = load_config()
        if acc_index >= len(cfg_data["sessions"]):
            cfg_data["sessions"].append(new_cookie)
        else:
            cfg_data["sessions"][acc_index] = new_cookie
        save_config(cfg_data)
        saved_msg = "✅ Tersimpan permanen ke config.json"
    except Exception as e:
        saved_msg = f"⚠️ Gagal simpan: {e}"

    with stats_lock:
        for em, s in global_stats["accounts"].items():
            if s.get("acc_index") == acc_index:
                global_stats["accounts"][em]["status"] = "🔄 Updated"
                break

    log.info(f"Session Akun #{acc_index+1} diupdate → {email}")

    me = api_call("GET", "/me", new_cookie)
    balance = int(me.get("balance_base_units","0")) // 10_000_000 if "error" not in me else "?"
    minted  = int(me.get("minted_base_units","0"))  // 10_000_000 if "error" not in me else "?"

    tg_send(chat_id,
        f"✅ <b>Cookie Berhasil Diupdate!</b>\n\n"
        f"📧 {email}\n"
        f"💵 Balance: {balance} RPOW\n"
        f"🎫 Minted: {minted} RPOW\n\n"
        f"{saved_msg}\n\n"
        f"⛏ Mining otomatis dilanjutkan.",
        main_keyboard()
    )

    if is_new:
        threading.Thread(
            target=mine_worker, args=(acc_index,), daemon=True
        ).start()
        log.info(f"Worker baru untuk Akun #{acc_index+1}")

def process_user_input(chat_id, text):
    with user_state_lock:
        state = user_state.get(chat_id)
    if not state:
        return False

    mode      = state.get("mode")
    acc_index = state.get("acc_index")

    with user_state_lock:
        user_state.pop(chat_id, None)

    if mode in ("cookie", "add_account"):
        new_cookie = text.strip()
        if not new_cookie.startswith("rpow_session="):
            tg_send(chat_id,
                "❌ <b>Format salah!</b>\n\n"
                "Harus dimulai:\n"
                "<code>rpow_session=eyJ...</code>\n\n"
                "Atau gunakan script:\n"
                "<code>bash ~/rpow2-miner/get_cookie.sh TOKEN</code>",
                main_keyboard()
            )
            return True

        tg_send(chat_id, "⏳ Memverifikasi cookie...")
        me = api_call("GET", "/me", new_cookie)
        if "error" in me:
            tg_send(chat_id,
                "❌ <b>Cookie tidak valid!</b>\n\n"
                "Gunakan script helper:\n"
                "<code>bash ~/rpow2-miner/get_cookie.sh TOKEN</code>",
                main_keyboard()
            )
            return True

        apply_new_session(chat_id, acc_index, new_cookie, me.get("email","?"))
        return True

    return False

# ══════════════════════════════════════════════════════
#  MINING ENGINE — C native dengan Python fallback
# ══════════════════════════════════════════════════════
def count_trailing_zero_bits(data: bytes) -> int:
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

def mine(nonce_prefix_hex: str, difficulty_bits: int,
         label: str, timeout: int = None):
    """
    Mining engine.
    Prioritas: C native binary → Python fallback
    C output JSON:
      found:    {"type":"found","solution_nonce":N,"hashes":N,"digest":"..."}
      expired:  {"type":"expired","hashes":N}
      progress: {"type":"progress","hashes":N,"nonce":N}
    """
    if timeout is None:
        timeout = CHALLENGE_TO

    if not mining_active.is_set():
        log.info(f"[{label}] ⏸ Dijeda...")
        mining_active.wait()
        log.info(f"[{label}] ▶️ Dilanjutkan!")

    binary = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "rpow-native-miner"
    )

    if os.path.exists(binary):
        return _mine_c(nonce_prefix_hex, difficulty_bits, label, timeout, binary)
    else:
        log.warning(f"[{label}] rpow-native-miner tidak ada, pakai Python")
        return _mine_python(nonce_prefix_hex, difficulty_bits, label, timeout)


def _mine_c(nonce_prefix_hex, difficulty_bits, label, timeout, binary):
    """Mining menggunakan native C binary — ~1.5M H/s per worker"""
    start     = time.time()
    cutoff_ms = int(timeout * 1000)

    cmd = [
        binary,
        "--prefix",      nonce_prefix_hex,
        "--difficulty",  str(difficulty_bits),
        "--workers",     str(MINING_THREADS),
        "--cutoff-ms",   str(cutoff_ms),
        "--progress-ms", "15000"
    ]

    log.info(f"[{label}] 🚀 C miner | {MINING_THREADS} workers | diff {difficulty_bits}b | cutoff {timeout}s")

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )

        solution_nonce = None

        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                t    = data.get("type", "")

                if t == "found":
                    solution_nonce = int(data["solution_nonce"])
                    hashes  = int(data.get("hashes", 0))
                    elapsed = time.time() - start
                    rate    = hashes / elapsed if elapsed > 0 else 0
                    log.info(
                        f"[{label}] 🎯 C KETEMU! nonce={solution_nonce} "
                        f"| {hashes:,} hashes | {elapsed:.1f}s | {rate:,.0f} H/s"
                    )
                    break

                elif t == "progress":
                    hashes  = int(data.get("hashes", 0))
                    elapsed = time.time() - start
                    rate    = hashes / elapsed if elapsed > 0 else 0
                    log.info(f"[{label}] ⛏  {hashes:,} | {rate:,.0f} H/s | {elapsed:.0f}s")

                    if not mining_active.is_set():
                        proc.terminate()
                        proc.wait()
                        log.info(f"[{label}] ⏸ Dijeda...")
                        mining_active.wait()
                        log.info(f"[{label}] ▶️ Dilanjutkan, restart C miner...")
                        start_nonce = data.get("nonce", 0)
                        new_cmd = cmd + ["--start", str(start_nonce)]
                        proc = subprocess.Popen(
                            new_cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            text=True,
                            bufsize=1
                        )

                elif t == "expired":
                    hashes  = int(data.get("hashes", 0))
                    elapsed = time.time() - start
                    log.info(f"[{label}] ⏰ C expired | {hashes:,} hashes | {elapsed:.0f}s")
                    break

            except (json.JSONDecodeError, KeyError, ValueError):
                if line:
                    log.debug(f"[{label}] C: {line}")

        proc.wait()
        return solution_nonce

    except Exception as e:
        log.error(f"[{label}] C miner error: {e}, fallback Python")
        return _mine_python(nonce_prefix_hex, difficulty_bits, label, timeout)


def _mine_python(nonce_prefix_hex, difficulty_bits, label, timeout):
    """Fallback Python miner — ~340k H/s"""
    nonce_prefix = bytes.fromhex(nonce_prefix_hex)
    num_threads  = MINING_THREADS
    result_queue = []
    result_lock  = threading.Lock()
    stop_event   = threading.Event()
    start        = time.time()

    def worker(thread_id, start_nonce, step):
        buffer      = bytearray(nonce_prefix + b"\x00" * 8)
        nonce       = start_nonce
        hashes      = 0
        last_report = time.time()

        while not stop_event.is_set():
            if time.time() - start > timeout:
                stop_event.set()
                return
            struct.pack_into("<Q", buffer, len(nonce_prefix), nonce)
            digest = hashlib.sha256(bytes(buffer)).digest()
            if count_trailing_zero_bits(digest) >= difficulty_bits:
                with result_lock:
                    if not result_queue:
                        result_queue.append(nonce)
                stop_event.set()
                return
            nonce  += step
            hashes += 1
            now = time.time()
            if thread_id == 0 and now - last_report >= 15:
                elapsed = now - start
                rate    = (hashes * num_threads) / elapsed if elapsed > 0 else 0
                log.info(f"[{label}] ⛏ (PY) {hashes*num_threads:,} | {rate:,.0f} H/s | {elapsed:.0f}s")
                last_report = now

    workers = []
    for i in range(num_threads):
        t = threading.Thread(target=worker, args=(i, i, num_threads), daemon=True)
        t.start()
        workers.append(t)
    for t in workers:
        t.join()

    if result_queue:
        nonce   = result_queue[0]
        elapsed = time.time() - start
        log.info(f"[{label}] 🎯 (PY) KETEMU! nonce={nonce} | {elapsed:.1f}s")
        return nonce

    log.info(f"[{label}] ⏰ (PY) Timeout")
    return None

# ══════════════════════════════════════════════════════
#  MINING WORKER
# ══════════════════════════════════════════════════════
def mine_worker(acc_index: int):
    label = f"Acc{acc_index + 1}"

    def get_session():
        with sessions_lock:
            if acc_index < len(SESSIONS):
                return SESSIONS[acc_index]
            return None

    # Tunggu sampai session valid
    while True:
        session = get_session()
        if session:
            me = api_call("GET", "/me", session)
            if "error" not in me:
                break
        log.warning(f"[{label}] Session invalid, menunggu update...")
        tg_notify_all(
            f"⚠️ <b>Session Expired — Akun #{acc_index+1}</b>\n\n"
            f"Jalankan di Termux:\n"
            f"<code>bash ~/rpow2-miner/get_cookie.sh TOKEN</code>\n\n"
            f"Atau update via 🔑 Update Cookie",
            main_keyboard()
        )
        old = get_session()
        while True:
            time.sleep(10)
            try:
                cfg_fresh      = load_config()
                fresh_sessions = cfg_fresh.get("sessions", [])
                if acc_index < len(fresh_sessions):
                    with sessions_lock:
                        SESSIONS[acc_index] = fresh_sessions[acc_index]
            except Exception:
                pass
            new = get_session()
            if new != old:
                me2 = api_call("GET", "/me", new)
                if "error" not in me2:
                    me = me2
                    break
            old = new

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

    balance = int(me.get("balance_base_units","0")) // 10_000_000
    minted  = int(me.get("minted_base_units","0"))  // 10_000_000
    log.info(f"[{label}] ✅ {email} | Balance: {balance} RPOW | Minted: {minted} RPOW")

    while True:
        try:
            mining_active.wait()

            session   = get_session()
            challenge = api_call("POST", "/challenge", session)

            if "error" in challenge:
                msg = str(challenge.get("message", ""))
                if any(k in msg.lower() for k in
                       ("unauthorized","login","session","expired","401")):
                    if not warned:
                        warned = True
                        with stats_lock:
                            if email in global_stats["accounts"]:
                                global_stats["accounts"][email]["status"] = "⚠️ Expired"
                        tg_notify_all(
                            f"⚠️ <b>Session Expired!</b>\n\n"
                            f"📧 <code>{email}</code>\n\n"
                            f"Jalankan:\n"
                            f"<code>bash ~/rpow2-miner/get_cookie.sh TOKEN</code>",
                            main_keyboard()
                        )
                    time.sleep(30)
                    continue

                log.warning(f"[{label}] Challenge error: {msg}")
                time.sleep(5)
                continue

            if warned:
                warned = False
                me2 = api_call("GET", "/me", get_session())
                if "error" not in me2:
                    email = me2["email"]

            cid    = challenge["challenge_id"]
            diff   = challenge["difficulty_bits"]
            prefix = challenge["nonce_prefix"]

            log.info(f"[{label}] 📋 {cid[:16]}... | {diff} bits")

            with stats_lock:
                if email in global_stats["accounts"]:
                    global_stats["accounts"][email]["status"] = f"⛏ Mining ({diff}b)"

            solution = mine(prefix, diff, label, CHALLENGE_TO)
            if solution is None:
                continue

            # Retry mint sampai 5x
            result = {"error": "init"}
            for mint_attempt in range(5):
                result = api_call("POST", "/mint", session,
                                  {"challenge_id": cid, "solution_nonce": str(solution)},
                                  retries=3)
                if "error" not in result:
                    break
                msg = str(result.get("message",""))
                if "expired" in msg.lower():
                    break
                log.warning(f"[{label}] Mint retry {mint_attempt+1}/5: {msg}")
                time.sleep(3 * (mint_attempt + 1))

            if "error" in result:
                msg = str(result.get("message",""))
                if "expired" in msg.lower():
                    log.info(f"[{label}] Challenge expired, ambil baru...")
                    continue
                log.warning(f"[{label}] Mint gagal: {msg}")
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

            elapsed   = time.time() - start_time
            rate      = local_mined / (elapsed / 3600) if elapsed > 0 else 0
            tok_short = token[:24] + "..." if len(token) > 24 else token

            log.info(f"[{label}] ✅ #{local_mined} | Total: {total} | {rate:.1f}/hr")

            tg_notify_all(
                f"⛏ <b>Token Berhasil Di-mine!</b>\n\n"
                f"📧 {email}\n"
                f"🎫 <code>{tok_short}</code>\n"
                f"🏠 #{local_mined}  |  🌐 Total #{total}\n"
                f"📈 {rate:.1f} token/jam\n"
                f"🕐 {datetime.now().strftime('%H:%M:%S')}"
            )

        except Exception as e:
            log.error(f"[{label}] Error: {e}")
            time.sleep(5)

# ══════════════════════════════════════════════════════
#  STATUS & BALANCE
# ══════════════════════════════════════════════════════
def fmt(base_units_str):
    """Format base_units ke RPOW (1 RPOW = 10,000,000 base_units)"""
    try:
        return int(base_units_str) // 10_000_000
    except:
        return 0

def build_status():
    with stats_lock:
        uptime = int(time.time() - global_stats["start_time"])
        h, rem = divmod(uptime, 3600)
        m      = rem // 60
        total  = global_stats["total_mined"]
        accs   = dict(global_stats["accounts"])
    state  = "▶️ Running" if mining_active.is_set() else "⏸ Paused"
    binary = os.path.exists(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "rpow-native-miner"
    ))
    engine = "⚡ C native" if binary else "🐍 Python"

    lines = [
        f"📊 <b>RPOW2 Status</b>",
        f"",
        f"{state}  |  Uptime: {h}j {m}m",
        f"🌐 Total: <b>{total} token</b>",
        f"🔧 Engine: {engine} | Threads: {MINING_THREADS}",
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
            balance = fmt(me.get("balance_base_units","0"))
            minted  = fmt(me.get("minted_base_units","0"))
            daily   = fmt(me.get("daily_minted_base_units","0"))
            cap     = fmt(me.get("daily_mint_cap_base_units","0"))
            lines.append(
                f"📧 {me['email']}\n"
                f"   💵 Balance: <b>{balance} RPOW</b>\n"
                f"   🎫 Total Minted: {minted} RPOW\n"
                f"   📅 Hari ini: {daily}/{cap} RPOW"
            )
        else:
            lines.append(
                f"Akun #{i}: ❌ Expired\n"
                f"   <code>bash ~/rpow2-miner/get_cookie.sh TOKEN</code>"
            )
    return "\n\n".join(lines)

# ══════════════════════════════════════════════════════
#  COMMAND HANDLER
# ══════════════════════════════════════════════════════
def handle_cmd(chat_id, cmd):
    global MINING_THREADS
    cmd = cmd.strip().split("@")[0]

    if cmd in ("/start", "/menu", "cmd_menu"):
        tg_send(chat_id, "👋 <b>RPOW2 Miner Bot</b>\n\nPilih aksi:", main_keyboard())

    elif cmd in ("/status", "cmd_status"):
        tg_send(chat_id, build_status(), main_keyboard())

    elif cmd in ("/balance", "cmd_balance"):
        tg_send(chat_id, "⏳ Mengambil balance...")
        tg_send(chat_id, build_balance(), main_keyboard())

    elif cmd in ("/stop", "cmd_stop"):
        mining_active.clear()
        tg_send(chat_id, "⏸ <b>Mining dijeda.</b>", main_keyboard())

    elif cmd in ("/resume", "cmd_resume"):
        mining_active.set()
        tg_send(chat_id, "▶️ <b>Mining dilanjutkan!</b>", main_keyboard())

    elif cmd in ("/cancel", "cmd_cancel"):
        with user_state_lock:
            user_state.pop(chat_id, None)
        tg_send(chat_id, "❌ Dibatalkan.", main_keyboard())

    elif cmd in ("/threads", "cmd_threads"):
        binary = os.path.exists(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "rpow-native-miner"
        ))
        engine_note = "⚡ C native aktif" if binary else "🐍 Python mode"
        tg_send(chat_id,
            f"⚙️ <b>Setting Mining Threads</b>\n\n"
            f"Saat ini: <b>{MINING_THREADS} thread</b>\n"
            f"Engine: {engine_note}\n\n"
            f"Pilih jumlah thread:",
            [
                [
                    {"text": "1️⃣ 1 thread",  "callback_data": "cmd_set_threads_1"},
                    {"text": "2️⃣ 2 thread",  "callback_data": "cmd_set_threads_2"},
                ],
                [
                    {"text": "4️⃣ 4 thread ⭐","callback_data": "cmd_set_threads_4"},
                    {"text": "6️⃣ 6 thread",  "callback_data": "cmd_set_threads_6"},
                ],
                [
                    {"text": "8️⃣ 8 thread 🔥","callback_data": "cmd_set_threads_8"},
                ],
                [{"text": "❌ Batal", "callback_data": "cmd_cancel"}]
            ]
        )

    elif cmd.startswith("cmd_set_threads_"):
        try:
            n = int(cmd.replace("cmd_set_threads_", ""))
            if n not in (1, 2, 4, 6, 8):
                raise ValueError
            MINING_THREADS = n
            cfg_data = load_config()
            cfg_data["mining_threads"] = n
            save_config(cfg_data)
            label_map = {
                1: "Hemat baterai",
                2: "2x hashrate",
                4: "4x hashrate ⭐ Recommended",
                6: "6x hashrate, agak panas",
                8: "8x hashrate MAX, sangat panas"
            }
            tg_send(chat_id,
                f"✅ <b>Thread diubah ke {n}</b>\n\n"
                f"<i>{label_map.get(n,'')}</i>\n\n"
                f"Aktif untuk challenge berikutnya.",
                main_keyboard()
            )
            log.info(f"Mining threads → {n} via Telegram")
        except (ValueError, IndexError):
            tg_send(chat_id, "❌ Tidak valid.", main_keyboard())

    elif cmd in ("/updatecookie", "cmd_update_cookie"):
        tg_send(chat_id,
            "🔑 <b>Update Cookie</b>\n\nPilih akun:",
            account_select_keyboard("cookie")
        )

    elif cmd in ("/addaccount", "cmd_add_account"):
        with user_state_lock:
            user_state[chat_id] = {"mode": "add_account", "acc_index": None}
        tg_send(chat_id,
            "➕ <b>Tambah Akun Baru</b>\n\n"
            "1️⃣ Buka rpow2.com/#/login\n"
            "2️⃣ Masukkan email → Send\n"
            "3️⃣ Cek email → long-press link → Copy\n"
            "4️⃣ Di Termux:\n"
            "<code>bash ~/rpow2-miner/get_cookie.sh URL</code>\n"
            "5️⃣ Paste cookie di sini\n\n"
            "Atau /cancel batal."
        )

    elif cmd in ("/help", "cmd_help"):
        tg_send(chat_id,
            "❓ <b>Panduan RPOW2 Bot</b>\n\n"
            "<b>Mining:</b>\n"
            "📊 Status — statistik + engine info\n"
            "💰 Balance — cek saldo semua akun\n"
            "⏸ Pause / ▶️ Resume\n"
            "⚙️ Threads — atur 1/2/4/6/8 thread\n\n"
            "<b>Cookie:</b>\n"
            "🔑 Update Cookie — update via Telegram\n"
            "➕ Tambah Akun — akun baru\n\n"
            "<b>Update Cookie (cara termudah):</b>\n"
            "1. Buka rpow2.com/#/login\n"
            "2. Masukkan email → Send\n"
            "3. Gmail → long-press link → Copy\n"
            "4. Di Termux:\n"
            "<code>bash ~/rpow2-miner/get_cookie.sh URL</code>",
            main_keyboard()
        )

    elif cmd.startswith("cmd_pick_cookie_"):
        try:
            acc_index = int(cmd.split("_")[-1])
            with user_state_lock:
                user_state[chat_id] = {"mode": "cookie", "acc_index": acc_index}
            tg_send(chat_id,
                f"🔑 <b>Update Cookie — Akun #{acc_index+1}</b>\n\n"
                f"Cara termudah:\n"
                f"1. Buka rpow2.com/#/login\n"
                f"2. Email → Send\n"
                f"3. Gmail → long-press link → Copy\n"
                f"4. <code>bash ~/rpow2-miner/get_cookie.sh URL</code>\n\n"
                f"Atau paste cookie langsung:\n"
                f"<code>rpow_session=eyJ...</code>\n\n"
                f"Atau /cancel batal."
            )
        except (ValueError, IndexError):
            tg_send(chat_id, "❌ Akun tidak valid.", main_keyboard())

    else:
        tg_send(chat_id, "❓ Ketik /help", main_keyboard())

# ══════════════════════════════════════════════════════
#  TELEGRAM POLLING
# ══════════════════════════════════════════════════════
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

                if "message" in upd:
                    msg     = upd["message"]
                    chat_id = str(msg["chat"]["id"])
                    text    = msg.get("text", "").strip()
                    if not text:
                        continue
                    if chat_id not in ALLOWED_IDS:
                        tg_send(chat_id, "⛔ Akses ditolak.")
                        continue
                    if text.startswith("/"):
                        handle_cmd(chat_id, text)
                    else:
                        handled = process_user_input(chat_id, text)
                        if not handled:
                            tg_send(chat_id, "Ketik /help atau pilih menu:", main_keyboard())

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

# ══════════════════════════════════════════════════════
#  AUTO REPORT
# ══════════════════════════════════════════════════════
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

# ══════════════════════════════════════════════════════
#  GRACEFUL SHUTDOWN
# ══════════════════════════════════════════════════════
def on_shutdown(sig, frame):
    with stats_lock:
        total = global_stats["total_mined"]
    uptime = int(time.time() - global_stats["start_time"])
    h, rem = divmod(uptime, 3600)
    m      = rem // 60
    log.info(f"\n🛑 Dihentikan. Total: {total} | Uptime: {h}j {m}m")
    tg_notify_all(
        f"🛑 <b>Bot Dihentikan</b>\n\n"
        f"📊 Total: {total} token\n"
        f"⏱ Uptime: {h}j {m}m"
    )
    sys.exit(0)

signal.signal(signal.SIGINT,  on_shutdown)
signal.signal(signal.SIGTERM, on_shutdown)

# ══════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════
def main():
    log.info("=" * 54)
    log.info("  RPOW2 Mining Bot v3.0 — Termux/Android Edition")
    log.info("=" * 54)

    if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN":
        log.error("❌ Isi telegram_bot_token di config.json!")
        sys.exit(1)

    # Cek curl
    try:
        subprocess.run(["curl", "--version"], capture_output=True, check=True)
        log.info("✅ curl tersedia")
    except (FileNotFoundError, subprocess.CalledProcessError):
        log.error("❌ Install curl: pkg install curl")
        sys.exit(1)

    # Cek C binary
    binary = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rpow-native-miner")
    if os.path.exists(binary):
        log.info(f"✅ C miner tersedia — ~1.5M H/s per worker")
    else:
        log.warning("⚠️ C miner tidak ada — pakai Python (~340k H/s)")
        log.warning("   Compile: clang -O3 -o rpow-native-miner rpow-native-miner.c -lpthread")

    log.info(f"  Threads: {MINING_THREADS}")
    log.info(f"  Memeriksa {len(SESSIONS)} session...")

    for i, session in enumerate(SESSIONS):
        me = api_call("GET", "/me", session)
        if "error" not in me:
            balance = fmt(me.get("balance_base_units","0"))
            log.info(f"  Akun #{i+1}: ✅ {me['email']} | {balance} RPOW")
        else:
            log.warning(f"  Akun #{i+1}: ❌ Expired")

    binary_status = "⚡ C native (~1.5M H/s/worker)" if os.path.exists(binary) else "🐍 Python (~340k H/s)"

    tg_notify_all(
        f"🚀 <b>RPOW2 Bot v3.0 Started!</b>\n\n"
        f"⛏ {len(SESSIONS)} akun dimuat\n"
        f"🔧 Engine: {binary_status}\n"
        f"🧵 Threads: {MINING_THREADS}\n"
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"Jika session expired:\n"
        f"<code>bash ~/rpow2-miner/get_cookie.sh TOKEN</code>",
        main_keyboard()
    )

    threading.Thread(target=telegram_polling, daemon=True).start()
    threading.Thread(target=auto_report, daemon=True).start()

    for i in range(len(SESSIONS)):
        threading.Thread(target=mine_worker, args=(i,), daemon=True).start()
        time.sleep(0.5)

    log.info(f"\n🚀 Semua thread aktif. Ctrl+C untuk stop.\n")

    while True:
        time.sleep(60)
        with stats_lock:
            total = global_stats["total_mined"]
        log.info(f"💓 Heartbeat | Total: {total}")

if __name__ == "__main__":
    main()