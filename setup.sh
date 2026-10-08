#!/tmp/prefix/bin/bash
# ============================================================================
#  ZYROX ENCRYPTION — setup.sh
#  Termux (Android) ke liye: python, Java, apktool.jar, apksigner
#  Chalayein:   bash setup.sh
# ============================================================================
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOL="$HERE/tool"
mkdir -p "$TOOL"

# ---- Termux pe shebang auto-fix (agar files PC pe bani hain)
if [ -n "${PREFIX:-}" ] && [ -d "$PREFIX/bin" ]; then
    for s in "$HERE"/*.sh; do
        sed -i "1s|^#!.*|#!$PREFIX/bin/bash|" "$s" 2>/dev/null || true
    done
fi

c()  { printf '\033[1;33m%s\033[0m\n' "$*"; }
ok() { printf '\033[1;32m[OK] %s\033[0m\n' "$*"; }
er() { printf '\033[1;31m[X] %s\033[0m\n' "$*"; }
warn(){ printf '\033[1;35m[!] %s\033[0m\n' "$*"; }

echo "=========================================================="
echo "   ███████╗██╗   ██╗██████╗  ██████╗ ██╗  ██╗"
echo "   ╚══███╔╝╚██╗ ██╔╝██╔══██╗██╔═══██╗╚██╗██╔╝"
echo "     ███╔╝  ╚████╔╝ ██████╔╝██║   ██║ ╚███╔╝"
echo "    ███╔╝    ╚██╔╝  ██╔══██╗██║   ██║ ██╔██╗"
echo "   ███████╗   ██║   ██║  ██║╚██████╔╝██╔╝ ██╗"
echo "   ╚══════╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝  ENCRYPTION"
echo "   SETUP"
echo "=========================================================="
echo

IS_TERMUX=0
if command -v pkg >/dev/null 2>&1 && [ -n "${PREFIX:-}" ]; then IS_TERMUX=1; fi

# ------------------------------------------------------------- 1. packages
if [ "$IS_TERMUX" = "1" ]; then
    c "[*] Termux packages: python, openjdk-17, curl, tar, unzip"
    pkg update -y >/dev/null 2>&1 || warn "pkg update fail (internet check karein)"
    for p in python openjdk-17 curl tar unzip; do
        if pkg list-installed 2>/dev/null | grep -q "^$p/"; then
            ok "$p pehle se installed"
        else
            c "    install: $p"
            pkg install -y "$p" || warn "$p install fail — manually:  pkg install -y $p"
        fi
    done
    pkg install -y apksigner >/dev/null 2>&1 && ok "system apksigner mila" || true
elif command -v apt-get >/dev/null 2>&1; then
    warn "Termux nahi hai (Linux lagta hai) — python3 + java pehle se hote hain"
elif command -v brew >/dev/null 2>&1; then
    warn "macOS:  brew install openjdk python3"
fi

# ------------------------------------------------------------- 2. Java
JAVA=""
for cand in "${PREFIX:-}/bin/java" "$(command -v java 2>/dev/null)"; do
    [ -n "$cand" ] && [ -x "$cand" ] && JAVA="$cand" && break
done
if [ -z "$JAVA" ] && command -v python3 >/dev/null 2>&1; then
    # dev-machine fallback (pip se portable JDK) — Termux pe ye skip
    python3 -c "import jdk4py" 2>/dev/null || \
        python3 -m pip install --quiet --break-system-packages jdk4py >/dev/null 2>&1 || true
    JH="$(python3 -c 'import jdk4py;print(jdk4py.JAVA_HOME)' 2>/dev/null || true)"
    [ -n "$JH" ] && [ -x "$JH/bin/java" ] && JAVA="$JH/bin/java"
fi
if [ -n "$JAVA" ]; then
    ok "Java: $("$JAVA" -version 2>&1 | head -1)"
else
    er "Java nahi mila — chalayein:  pkg install -y openjdk-17"
    exit 1
fi
echo

# ------------------------------------------------------------- 3. jars
if [ -f "$TOOL/apktool.jar" ]; then
    ok "apktool.jar pehle se hai"
else
    c "[*] apktool + apksigner download (npm registry tarball — npm ki zaroorat nahi)"
    TMP="$(mktemp -d)"; TGZ="$TMP/at.tgz"
    URL="https://registry.npmjs.org/@postar/apktool-node/-/apktool-node-0.3.4.tgz"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --retry 2 --max-time 300 -o "$TGZ" "$URL" || warn "download fail (Part 1)"
    elif command -v wget >/dev/null 2>&1; then
        wget -q -O "$TGZ" "$URL" || warn "download fail (Part 1)"
    fi
    if [ -s "$TGZ" ]; then
        tar xzf "$TGZ" -C "$TMP" 2>/dev/null || true
        [ -f "$TMP/package/lib/apktool.jar" ] && cp -f "$TMP/package/lib/apktool.jar" "$TOOL/"
        [ -f "$TMP/package/lib/apksigner.jar" ] && cp -f "$TMP/package/lib/apksigner.jar" "$TOOL/"
    fi
    rm -rf "$TMP"

    if [ ! -f "$TOOL/apktool.jar" ]; then
        warn "npm se nahi mila — GitHub (iBotPeaches/Apktool) se try kar raha hoon…"
        for v in 2.11.1 2.10.0 2.9.3; do
            U="https://github.com/iBotPeaches/Apktool/releases/download/v$v/apktool_$v.jar"
            if command -v curl >/dev/null 2>&1; then
                curl -fsSL --retry 1 --max-time 300 -o "$TOOL/apktool.jar" "$U" && break
            fi
        done
    fi
fi

if [ -f "$TOOL/apktool.jar" ]; then
    ok "apktool   : $("$JAVA" -jar "$TOOL/apktool.jar" --version 2>&1 | tail -1)"
else
    er "apktool.jar nahi mila — manually daalein:  $TOOL/apktool.jar"
    exit 1
fi

if command -v apksigner >/dev/null 2>&1; then
    ok "signer    : $(apksigner --version 2>&1 | head -1)  (system)"
elif [ -f "$TOOL/apksigner.jar" ]; then
    ok "signer    : apksigner.jar $("$JAVA" -jar "$TOOL/apksigner.jar" --version 2>&1 | head -1)"
else
    warn "signer nahi mila — 'pkg install -y apksigner' chalayein"
fi

echo
ok "SETUP POORA ✅"
echo
echo "  Ab chalayein:"
echo "     bash install.sh          # 'zyrox' command install karo (recommended)"
echo "     zyrox /sdcard/myapp.apk  # APK encrypt karo"
echo
echo "  Ya seedha:"
echo "     python zyrox.py /sdcard/myapp.apk --to-storage"
echo
