#!/data/data/com.termux/files/usr/bin/bash
# ══════════════════════════════════════════
#  RPOW2 Cookie Helper
#  Usage: bash get_cookie.sh TOKEN_ATAU_URL [ACC_INDEX]
#  ACC_INDEX: 0 = akun pertama (default)
# ══════════════════════════════════════════

TOKEN="$1"
ACC_INDEX="${2:-0}"
CONFIG="$HOME/rpow2-miner/config.json"

if [ -z "$TOKEN" ]; then
    echo ""
    echo "❌ Token tidak boleh kosong!"
    echo ""
    echo "Usage:"
    echo "  bash get_cookie.sh URL_MAGIC_LINK"
    echo "  bash get_cookie.sh TOKEN_SAJA"
    echo "  bash get_cookie.sh URL 1   (akun ke-2)"
    echo ""
    echo "Contoh:"
    echo "  bash get_cookie.sh 'https://api.rpow2.com/auth/verify?token=ABC123'"
    echo "  bash get_cookie.sh ABC123"
    echo ""
    exit 1
fi

# Kalau paste full URL, ekstrak token
if [[ "$TOKEN" == *"token="* ]]; then
    TOKEN=$(echo "$TOKEN" | grep -oP "token=\K[^\s&]+")
    echo "✅ Token diekstrak: ${TOKEN:0:20}..."
fi

# Hapus whitespace
TOKEN=$(echo "$TOKEN" | tr -d '[:space:]')

echo ""
echo "══════════════════════════════════"
echo "  RPOW2 Cookie Helper"
echo "══════════════════════════════════"
echo "Token : ${TOKEN:0:20}..."
echo "Akun  : #$((ACC_INDEX + 1))"
echo ""
echo "⏳ Menghubungi server..."

RESPONSE=$(curl -sk \
    --max-redirs 0 \
    -D - \
    -o /dev/null \
    -H "Origin: https://rpow2.com" \
    -H "Referer: https://rpow2.com/" \
    -H "Accept: application/json, text/html, */*" \
    -H "User-Agent: Mozilla/5.0 (Linux; Android 14; SM-A155F) AppleWebKit/537.36" \
    "https://api.rpow2.com/auth/verify?token=${TOKEN}")

COOKIE_VAL=$(echo "$RESPONSE" | grep -i "set-cookie" | grep -oP "rpow_session=\K[^;]+")

if [ -z "$COOKIE_VAL" ]; then
    echo "❌ Gagal dapat cookie!"
    echo ""
    echo "Response server:"
    echo "$RESPONSE" | head -3
    echo ""
    if echo "$RESPONSE" | grep -q "400"; then
        echo "⚠️  Token expired atau sudah dipakai."
        echo "   Minta magic link baru lalu langsung jalankan script ini."
    fi
    exit 1
fi

FULL_COOKIE="rpow_session=${COOKIE_VAL}"
echo "✅ Cookie berhasil didapat! (${#COOKIE_VAL} karakter)"
echo ""
echo "⏳ Verifikasi..."

ME=$(curl -sk \
    -H "Cookie: ${FULL_COOKIE}" \
    -H "Accept: application/json" \
    "https://api.rpow2.com/me")

EMAIL=$(echo "$ME" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('email','?'))" 2>/dev/null)
BALANCE=$(echo "$ME" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('balance_base_units','0'))" 2>/dev/null)

if [ "$EMAIL" = "?" ] || [ -z "$EMAIL" ]; then
    echo "❌ Cookie tidak valid!"
    echo "Response: $ME"
    exit 1
fi

echo "✅ Login berhasil!"
echo "   Email  : $EMAIL"
echo "   Balance: $BALANCE"
echo ""
echo "⏳ Menyimpan ke config.json..."

python3 << PYEOF
import json, sys

config_file = "$CONFIG"
acc_index   = $ACC_INDEX
new_cookie  = "$FULL_COOKIE"

try:
    with open(config_file, "r") as f:
        cfg = json.load(f)
    sessions = cfg.get("sessions", [])
    if acc_index >= len(sessions):
        sessions.append(new_cookie)
        print(f"  ➕ Akun baru ditambahkan (#{acc_index + 1})")
    else:
        sessions[acc_index] = new_cookie
        print(f"  🔄 Akun #{acc_index + 1} diupdate")
    cfg["sessions"] = sessions
    with open(config_file, "w") as f:
        json.dump(cfg, f, indent=2)
    print("  ✅ config.json berhasil disimpan!")
except FileNotFoundError:
    print(f"  ❌ config.json tidak ditemukan!")
    sys.exit(1)
except Exception as e:
    print(f"  ❌ Error: {e}")
    sys.exit(1)
PYEOF

echo ""
echo "══════════════════════════════════"
echo "  ✅ SELESAI!"
echo "══════════════════════════════════"
echo ""
echo "Cookie untuk $EMAIL tersimpan."
echo "Bot otomatis detect dalam ~10 detik."
echo ""