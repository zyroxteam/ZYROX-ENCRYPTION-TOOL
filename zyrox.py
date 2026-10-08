#!/usr/bin/env python3
"""
 ███████╗██╗   ██╗██████╗  ██████╗ ██╗  ██╗
 ╚══███╔╝╚██╗ ██╔╝██╔══██╗██╔═══██╗╚██╗██╔╝
   ███╔╝  ╚████╔╝ ██████╔╝██║   ██║ ╚███╔╝
  ███╔╝    ╚██╔╝  ██╔══██╗██║   ██║ ██╔██╗
 ███████╗   ██║   ██║  ██║╚██████╔╝██╔╝ ██╗
 ╚══════╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝   ENCRYPTION
 ================================================================
  ZYROX ENCRYPTION — APK string-encryption + debug-strip + signing
  Termux CLI (Python + Java) · https://github.com/zyroxteam/zyrox-encryption
 ================================================================

Kya karta hai:
  1. apktool se APK decode (smali + resources)
  2. HAR string (const-string) encrypt → runtime decryptor call
     HAR string ki APNI key: seed har string ke liye random,
     sirf ek global mask hidden hai (XOR-split) — static analysis mushkil
  3. debug info strip (.line / .local / .param .prologue) → decompile padhna mushkil
  4. apktool se rebuild → naya dex; ORIGINAL APK me sirf dex replace (resources safe)
  5. resources.arsc 4-byte align + .so page align (zipalign jaisa)
  6. sign v1+v2+v3 + verify (proof)
  + optional: --anti-debug / --anti-root guard (Application.onCreate me inject),
    --harden-manifest (debuggable=false), --min-len N

Usage:
  zyrox /sdcard/myapp.apk                    # aapka APK -> protected APK
  zyrox "/sdcard/Protect Apk/myapp.apk"       # SPACE wale path me quotes lagao!
  zyrox /sdcard/Protect\ Apk/                # ya folder do — .apk khud dhoondh lega
  zyrox /sdcard/myapp.apk --anti-debug --anti-root --harden-manifest
  zyrox /sdcard/myapp.apk --keys my.jks      # apni keystore se sign (update ke liye)
  zyrox /sdcard/myapp.apk --info             # sirf jaankari
  zyrox --self-test                          # install check (APK ki zaroorat nahi)
  zyrox --help
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

VERSION = "1.2.0"
REPO = "https://github.com/zyroxteam/zyrox-encryption"

HERE = Path(__file__).resolve().parent
HOME = Path(os.environ.get("ZYROX_HOME", str(HERE)))

DEX_RE = re.compile(r"^classes(\d*)\.dex$")
SIG_RE = re.compile(r"^META-INF/.*\.(SF|RSA|DSA|EC|MF)$", re.I)
SMALI_DIR_RE = re.compile(r"^smali(_classes\d+)?$")

CONST_RE = re.compile(r'^(\s*)const-string(/jumbo)?\s+([vp]\d+)\s*,\s*"((?:[^"\\]|\\.)*)"(\s*)$')
DEBUG_RE = re.compile(r"^\s*\.(line|local|end local|param|end param|prologue|epilogue|restart local)\b")

ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f",
           '"': '"', "'": "'", "\\": "\\", "0": "\0"}

BANNER = r"""
 ███████╗██╗   ██╗██████╗  ██████╗ ██╗  ██╗
 ╚══███╔╝╚██╗ ██╔╝██╔══██╗██╔═══██╗╚██╗██╔╝
   ███╔╝  ╚████╔╝ ██████╔╝██║   ██║ ╚███╔╝
  ███╔╝    ╚██╔╝  ██╔══██╗██║   ██║ ██╔██╗
 ███████╗   ██║   ██║  ██║╚██████╔╝██╔╝ ██╗
 ╚══════╝   ╚═╝   ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝  ENCRYPTION
"""


# ------------------------------------------------------------------ helpers
def log(m: str = "") -> None:
    print(m, flush=True)


def die(msg: str, code: int = 1):
    log(f"\n❌ {msg}")
    sys.exit(code)


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n} B"


def find_tool(name: str) -> Path | None:
    """tool/ folder (repo, ZYROX_TOOL_DIR ya ZYROX_HOME) me jar dhoondho."""
    cands = []
    if os.environ.get("ZYROX_TOOL_DIR"):
        cands.append(Path(os.environ["ZYROX_TOOL_DIR"]))
    cands += [HOME / "tool", HERE / "tool", Path("/data/data/com.termux/files/home/zyrox-encryption/tool")]
    for d in cands:
        if (d / name).exists():
            return d / name
    return None


def _jdk4py_java() -> str | None:
    """Dev-machine fallback (Termux pe iski zaroorat nahi)."""
    try:
        import jdk4py  # type: ignore
        p = Path(str(jdk4py.JAVA_HOME)) / "bin" / "java"
        return str(p) if p.exists() else None
    except Exception:
        return None


def java_bin() -> str:
    for cand in (os.environ.get("ZYROX_JAVA"), os.environ.get("JAVA_HOME")):
        if cand:
            p = Path(cand)
            if (p / "bin" / "java").exists():
                return str(p / "bin" / "java")
            if p.name == "java" and p.exists():
                return str(p)
    j = shutil.which("java")
    if j:
        return j
    j = _jdk4py_java()
    if j:
        return j
    die("Java nahi mila.\n"
        "   Termux:  pkg install -y openjdk-17\n"
        "   ya:      bash setup.sh   (sab install kar dega)")
    return ""


def java_keytool(java: str) -> str:
    kt = Path(java).parent / ("keytool.exe" if os.name == "nt" else "keytool")
    return str(kt) if kt.exists() else "keytool"


_JAVA_MAJOR: int | None = None


def java_major(java: str) -> int:
    global _JAVA_MAJOR
    if _JAVA_MAJOR is None:
        try:
            r = subprocess.run([java, "-version"], capture_output=True, text=True, timeout=30)
            m = re.search(r'version "(\d+)', (r.stderr or "") + (r.stdout or ""))
            _JAVA_MAJOR = int(m.group(1)) if m else 0
        except Exception:
            _JAVA_MAJOR = 0
    return _JAVA_MAJOR


def native_flag(java: str) -> list[str]:
    """Java 24+ pe native-access warning band karne ke liye."""
    return ["--enable-native-access=ALL-UNNAMED"] if java_major(java) >= 24 else []


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# --------------------------------------------------------------- signing glue
class Signer:
    """Termux ka `apksigner` command ya bundled apksigner.jar — dono support."""

    def __init__(self, java: str):
        self.java = java
        self.cli = shutil.which("apksigner")
        self.jar = find_tool("apksigner.jar")
        if not self.cli and not self.jar:
            die("Signer nahi mila.\n   chalayein:  bash setup.sh\n   ya:         pkg install -y apksigner")
        self.supports_v3 = self._detect_v3()

    def _base(self, sign: bool) -> list[str]:
        if self.jar:
            return [self.java, *native_flag(self.java), "-jar", str(self.jar)] + (["sign"] if sign else ["verify"])
        return [str(self.cli)] + (["sign"] if sign else ["verify"])

    def _detect_v3(self) -> bool:
        try:
            r = run(self._base(True) + ["--help"])
            return "--v3-signing-enabled" in ((r.stdout or "") + (r.stderr or ""))
        except Exception:
            return False

    def describe(self) -> str:
        if self.cli:
            try:
                v = (run([str(self.cli), "--version"]).stdout or "").strip().splitlines()
                return f"apksigner (system) {v[0] if v else ''}".strip()
            except Exception:
                return "apksigner (system)"
        return f"apksigner.jar ({Path(str(self.jar)).name})"

    def sign(self, apk: Path, out: Path, ks: Path, alias: str,
             ks_pass: str, key_pass: str, min_sdk: int) -> None:
        cmd = self._base(True) + [
            "--ks", str(ks), "--ks-key-alias", alias,
            "--ks-pass", f"pass:{ks_pass}", "--key-pass", f"pass:{key_pass}",
            "--min-sdk-version", str(min_sdk),
            "--v1-signing-enabled", "true", "--v2-signing-enabled", "true",
        ]
        if self.supports_v3:
            cmd += ["--v3-signing-enabled", "true"]
        cmd += ["--out", str(out), str(apk)]
        r = run(cmd)
        if r.returncode != 0:
            die(f"Sign fail:\n{(r.stdout or '')[-1500:]}\n{(r.stderr or '')[-1000:]}")

    def verify(self, apk: Path) -> tuple[bool, str]:
        r = run(self._base(False) + ["--verbose", "--print-certs", str(apk)])
        txt = (r.stdout or "") + (r.stderr or "")
        return ("Verifies" in txt and "DOES NOT VERIFY" not in txt), txt


# ============================================================= zip read/write
def read_zip(apk: Path) -> list[dict]:
    import zipfile
    with zipfile.ZipFile(apk) as z:
        return [{"name": zi.filename, "data": z.read(zi.filename),
                 "method": zi.compress_type, "isdir": zi.is_dir()} for zi in z.infolist()]


def _deflate(data: bytes) -> bytes:
    """Zip ko RAW deflate chahiye (zlib header/trailer ke bina)."""
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    return co.compress(data) + co.flush()


def _dos_ts() -> tuple[int, int]:
    t = time.localtime()
    return ((t.tm_hour << 11) | (t.tm_min << 5) | (t.tm_sec // 2),
            ((t.tm_year - 1980) << 9) | (t.tm_mon << 5) | t.tm_mday)


def write_zip(path: Path, entries: list[dict], align: int = 4) -> None:
    """Zip writer — STORED entries ko align karta hai (zipalign jaisa), .so -> 4096."""
    dtime, ddate = _dos_ts()
    central: list[bytes] = []
    off = 0
    zip64 = len(entries) > 0xFFFE
    fh = open(path, "wb")

    def w(b: bytes):
        nonlocal off
        fh.write(b)
        off += len(b)

    for e in entries:
        name_b = e["name"].encode("utf-8")
        raw = e["data"]
        crc = zlib.crc32(raw) & 0xFFFFFFFF
        if e["isdir"]:
            method, comp = 0, b""
        elif e["method"] == 0:
            method, comp = 0, raw
        else:
            method, comp = 8, _deflate(raw)

        extra = b""
        this_align = 4096 if e["name"].endswith(".so") else align
        if method == 0 and this_align and not e["isdir"]:
            need = (-(off + 30 + len(name_b))) % this_align
            if need:
                if need < 4:
                    need += this_align
                extra = struct.pack("<HH", 0xD935, need - 4) + b"\x00" * (need - 4)

        size, csize = len(raw), len(comp)
        use64 = size > 0xFFFFFFFE or csize > 0xFFFFFFFE
        if use64:
            zip64 = True
            z64 = struct.pack("<QQ", size, csize)
            extra = struct.pack("<HH", 0x0001, len(z64)) + z64 + extra
            size_f = csize_f = 0xFFFFFFFF
        else:
            size_f, csize_f = size, csize

        local = off
        w(struct.pack("<IHHHHHIIIHH", 0x04034B50, 45 if use64 else 20, 0x0800,
                      method, dtime, ddate, crc, csize_f, size_f, len(name_b), len(extra)))
        w(name_b)
        w(extra)
        w(comp)
        central.append(struct.pack(
            "<IHHHHHHIIIHHHHHII", 0x02014B50, 0x031E, 45 if use64 else 20, 0x0800,
            method, dtime, ddate, crc, csize_f, size_f, len(name_b), len(extra),
            0, 0, 0, 0, 0xFFFFFFFF if local > 0xFFFFFFFE else local) + name_b + extra)

    cd_off = off
    for c in central:
        w(c)
    cd_size = off - cd_off
    if zip64 or cd_off > 0xFFFFFFFF or cd_size > 0xFFFFFFFF or len(entries) > 0xFFFE:
        z64_off = off
        w(struct.pack("<IQHHIIQQQQ", 0x06064B50, 44, 45, 45, 0, 0,
                      len(entries), len(entries), cd_size, cd_off))
        w(struct.pack("<IIQI", 0x07064B50, 0, z64_off, 1))
        w(struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0))
    else:
        w(struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries), cd_size, cd_off, 0))
    fh.close()


def sanity_check_zip(path: Path) -> tuple[bool, str, list[str]]:
    import zipfile
    msgs: list[str] = []
    notes: list[str] = []
    ok = True
    try:
        with zipfile.ZipFile(path) as z:
            bad = z.testzip()
            if bad:
                ok = False
                msgs.append(f"corrupt entry: {bad}")
            for zi in z.infolist():
                if zi.filename in ("resources.arsc", "AndroidManifest.xml") and zi.compress_type != 0:
                    notes.append(f"{zi.filename} compressed hai (apktool ne aisa hi rakha hai)")
                if zi.compress_type == 0:
                    with open(path, "rb") as f:
                        f.seek(zi.header_offset + 26)
                        nlen, elen = struct.unpack("<HH", f.read(4))
                    data_off = zi.header_offset + 30 + nlen + elen
                    need = 4096 if zi.filename.endswith(".so") else 4
                    if data_off % need:
                        ok = False
                        msgs.append(f"{zi.filename} {need}-aligned nahi ({data_off})")
    except Exception as exc:
        ok = False
        msgs.append(f"zip read fail: {exc}")
    return ok, ("; ".join(msgs) if msgs else "zip ok · arsc aligned · .so page-aligned"), notes


# ================================================================ string crypto
def to_units(s: str) -> list[int]:
    """Python str -> Java UTF-16 code units (emoji = 2 units, exactly Java jaisa)."""
    b = s.encode("utf-16-be", "surrogatepass")
    return [(b[i] << 8) | b[i + 1] for i in range(0, len(b), 2)]


def from_units(units: list[int]) -> str:
    b = bytearray()
    for u in units:
        b += bytes(((u >> 8) & 0xFF, u & 0xFF))
    return bytes(b).decode("utf-16-be", "surrogatepass")


def ks(i: int, seed: int, mult: int) -> int:
    return (seed + i * mult) & 0xFFFF


def xcrypt_units(units: list[int], seed: int, mult: int) -> list[int]:
    return [(u ^ ks(i, seed, mult)) & 0xFFFF for i, u in enumerate(units)]


def roundtrip(s: str, seed: int, mult: int) -> str:
    return from_units(xcrypt_units(xcrypt_units(to_units(s), seed, mult), seed, mult))


# ------------------------------------------------- per-string crypto (v1.2)
def derive_mult(seed: int) -> int:
    """Har string ka mult uske seed se deterministic — decryptor khud derive karega."""
    return (seed * 0x25 + 0x1B) & 0xFFFF


def enc_payload(units: list[int], seed: int, mask: int) -> list[int]:
    """Payload = [seed ^ mask] + encrypted units. Har string ki apni seed."""
    mult = derive_mult(seed)
    enc = [(u ^ ks(i, seed, mult)) & 0xFFFF for i, u in enumerate(units)]
    return [((seed ^ mask) & 0xFFFF)] + enc


def dec_payload(payload: list[int], mask: int) -> list[int]:
    seed = (payload[0] ^ mask) & 0xFFFF
    mult = derive_mult(seed)
    return [(u ^ ks(i, seed, mult)) & 0xFFFF for i, u in enumerate(payload[1:])]


def roundtrip2(s: str, seed: int, mask: int) -> str:
    return from_units(dec_payload(enc_payload(to_units(s), seed, mask), mask))


def unescape(s: str) -> str:
    out, i, n = [], 0, len(s)
    while i < n:
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            nx = s[i + 1]
            if nx == "u" and i + 6 <= n:
                try:
                    out.append(chr(int(s[i + 2:i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            if nx in ESCAPES:
                out.append(ESCAPES[nx])
                i += 2
                continue
            out.append(nx)
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def units_escape(units: list[int]) -> str:
    out = []
    for u in units:
        c = chr(u)
        if c == '"':
            out.append('\\"')
        elif c == "\\":
            out.append("\\\\")
        elif 0x20 <= u <= 0x7E:
            out.append(c)
        else:
            out.append("\\u%04x" % u)
    return "".join(out)


def transform(text: str, keys: dict, stats: dict, strip: bool,
              min_len: int, rnd: random.Random) -> str:
    out = []
    for line in text.split("\n"):
        cr = "\r" if line.endswith("\r") else ""
        L = line[:-1] if cr else line
        if strip and DEBUG_RE.match(L):
            continue
        m = CONST_RE.match(L)
        if m:
            ind, jumbo, reg, lit, tail = m.groups()
            jumbo = jumbo or ""
            plain = unescape(lit)
            if len(plain) < min_len:
                out.append(L + cr)   # chhoti/khaali string — encrypt nahi
                continue
            units = to_units(plain)
            if keys["simple"]:
                enc = xcrypt_units(units, keys["seed"], keys["mult"])
            else:
                seed = rnd.randint(0x1, 0xFFFE)   # har string ki apni seed
                enc = enc_payload(units, seed, keys["mask"])
            stats["strings"] += 1
            stats["units"] += len(enc)
            out.append(f'{ind}const-string{jumbo} {reg}, "{units_escape(enc)}"{tail}')
            out.append(f"{ind}invoke-static {{{reg}}}, {keys['desc']}->"
                       f"{keys['meth']}(Ljava/lang/String;)Ljava/lang/String;")
            out.append(f"{ind}move-result-object {reg}")
        else:
            out.append(L + cr)
    return "\n".join(out)


def gen_keys(rnd: random.Random, simple: bool) -> dict:
    pkg = "".join(rnd.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rnd.randint(4, 7)))
    cls = "".join(rnd.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
                  for _ in range(rnd.randint(5, 8)))
    meth = "a" + "".join(rnd.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rnd.randint(4, 8)))
    keys: dict = {"pkg": pkg, "cls": cls, "meth": meth,
                  "desc": f"L{pkg}/{cls};", "simple": simple}
    if simple:
        # debug mode: ek global seed/mult, seedhe smali me (aasan debugging)
        keys["seed"] = rnd.randint(0x101, 0x7FFE)
        keys["mult"] = rnd.randint(0x81, 0x7FFF)
    else:
        # production: har string ki apni seed; sirf global mask hidden (XOR-split)
        mask = rnd.randint(0x101, 0xFFFE)
        mb = rnd.randint(0x1000, 0x3FFF)
        keys["mask"] = mask
        keys["ma"] = mask ^ mb
        keys["mb"] = mb                    # ma ^ mb == mask
    return keys


def decryptor_smali(desc: str, meth: str, keys: dict) -> str:
    """Runtime decryptor — pure smali, koi external library nahi."""
    if keys["simple"]:
        return f""".class public final {desc}
.super Ljava/lang/Object;


.method public static {meth}(Ljava/lang/String;)Ljava/lang/String;
    .locals 6

    invoke-virtual {{p0}}, Ljava/lang/String;->length()I

    move-result v0

    new-array v1, v0, [C

    const/4 v2, 0x0

    :cond_0
    if-ge v2, v0, :cond_1

    invoke-virtual {{p0, v2}}, Ljava/lang/String;->charAt(I)C

    move-result v3

    mul-int/lit16 v4, v2, 0x{keys["mult"]:04x}

    add-int/lit16 v4, v4, 0x{keys["seed"]:04x}

    xor-int/2addr v3, v4

    int-to-char v3, v3

    aput-char v3, v1, v2

    add-int/lit8 v2, v2, 0x1

    goto :cond_0

    :cond_1
    new-instance v3, Ljava/lang/String;

    invoke-direct {{v3, v1}}, Ljava/lang/String;-><init>([C)V

    return-object v3
.end method
"""
    # per-string mode: payload = [seed ^ mask] + encrypted units
    # seed = charAt(0) ^ mask; mult = seed * 0x25 + 0x1b (derive_mult jaisa)
    return f""".class public final {desc}
.super Ljava/lang/Object;


.method public static {meth}(Ljava/lang/String;)Ljava/lang/String;
    .locals 8

    const v5, 0x{keys["ma"]:04x}

    xor-int/lit16 v5, v5, 0x{keys["mb"]:04x}

    const/4 v2, 0x0

    invoke-virtual {{p0, v2}}, Ljava/lang/String;->charAt(I)C

    move-result v3

    xor-int/2addr v3, v5

    int-to-char v3, v3

    move v5, v3

    mul-int/lit16 v6, v5, 0x0025

    add-int/lit16 v6, v6, 0x001b

    invoke-virtual {{p0}}, Ljava/lang/String;->length()I

    move-result v0

    add-int/lit8 v0, v0, -0x1

    new-array v1, v0, [C

    const/4 v2, 0x0

    :cond_0
    if-ge v2, v0, :cond_1

    add-int/lit8 v7, v2, 0x1

    invoke-virtual {{p0, v7}}, Ljava/lang/String;->charAt(I)C

    move-result v3

    mul-int v4, v2, v6

    add-int v4, v4, v5

    xor-int/2addr v3, v4

    int-to-char v3, v3

    aput-char v3, v1, v2

    add-int/lit8 v2, v2, 0x1

    goto :cond_0

    :cond_1
    new-instance v3, Ljava/lang/String;

    invoke-direct {{v3, v1}}, Ljava/lang/String;-><init>([C)V

    return-object v3
.end method
"""

# ============================================================ runtime guards
APP_NAME_RE = re.compile(r'<application\b[^>]*?android:name="([^"]+)"', re.S)
PKG_RE = re.compile(r'<manifest\b[^>]*?package="([^"]+)"', re.S)
ONCREATE_RE = re.compile(r'^(\.method[^\n]*\bonCreate\(\)V[^\n]*\n)', re.M)
LOCALS_RE = re.compile(r'^[ \t]*\.(locals|registers)[^\n]*\n', re.M)


def guard_smali(pkg: str, cls: str, meth: str, anti_debug: bool, anti_root: bool) -> str:
    """Application.onCreate se call hone wala guard — debugger/root pe exit(-1).
    Poora check try/catch me hai taaki guard kabhi legit user ko crash na kare."""
    desc = f"L{pkg}/{cls};"
    checks = []
    if anti_debug:
        checks.append("""    invoke-static {}, Landroid/os/Debug;->isDebuggerConnected()Z

    move-result v0

    if-eqz v0, :cond_bad""")
    if anti_root:
        checks.append("""    new-instance v0, Ljava/io/File;

    const-string v1, "/system/xbin/su"

    invoke-direct {v0, v1}, Ljava/io/File;-><init>(Ljava/lang/String;)V

    invoke-virtual {v0}, Ljava/io/File;->exists()Z

    move-result v0

    if-nez v0, :cond_bad

    new-instance v0, Ljava/io/File;

    const-string v1, "/system/bin/su"

    invoke-direct {v0, v1}, Ljava/io/File;-><init>(Ljava/lang/String;)V

    invoke-virtual {v0}, Ljava/io/File;->exists()Z

    move-result v0

    if-nez v0, :cond_bad

    sget-object v0, Landroid/os/Build;->TAGS:Ljava/lang/String;

    if-eqz v0, :cond_ok

    const-string v1, "test-keys"

    invoke-virtual {v0, v1}, Ljava/lang/String;->contains(Ljava/lang/CharSequence;)Z

    move-result v0

    if-eqz v0, :cond_bad

    :cond_ok""")
    body = "\n\n".join(checks)
    return f""".class public final {desc}
.super Ljava/lang/Object;


.method public static {meth}(Landroid/content/Context;)V
    .locals 3

    :try_start_0
{body}

    :try_end_0
    .catch Ljava/lang/Exception; {{:try_start_0 .. :try_end_0}} :catch_0

    return-void

    :cond_bad
    invoke-static {{}}, Ljava/lang/Runtime;->getRuntime()Ljava/lang/Runtime;

    move-result-object v0

    const/4 v1, -0x1

    invoke-virtual {{v0, v1}}, Ljava/lang/Runtime;->exit(I)V

    return-void

    :catch_0
    return-void
.end method
"""


def _find_smali(smali_dirs: list[Path], dotted: str) -> Path | None:
    rel = dotted.replace(".", "/") + ".smali"
    for sd in smali_dirs:
        c = sd / rel
        if c.exists():
            return c
    return None


def inject_guard_call(dec: Path, smali_dirs: list[Path], guard_desc: str,
                      guard_meth: str, rnd: random.Random) -> str:
    """Guard call Application.onCreate me inject karo. Returns info string."""
    mf = dec / "AndroidManifest.xml"
    txt = mf.read_text(errors="ignore")
    dotted = None
    m = APP_NAME_RE.search(txt)
    if m:
        name = m.group(1)
        if name.startswith("."):
            pm = PKG_RE.search(txt)
            name = (pm.group(1) if pm else "") + name
        dotted = name
    target = _find_smali(smali_dirs, dotted) if dotted else None
    call = f"    invoke-static {{p0}}, {guard_desc}->{guard_meth}(Landroid/content/Context;)V"
    if target is None:
        # app ka apna Application nahi — naya subclass banao + manifest me lagao
        pkg = "".join(rnd.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rnd.randint(4, 7)))
        cls = "Z" + "".join(rnd.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
                            for _ in range(rnd.randint(5, 8)))
        desc = f"L{pkg}/{cls};"
        dotted_new = f"{pkg}.{cls}"
        smali = ("".join([
            f".class public {desc}\n",
            ".super Landroid/app/Application;\n",
            "\n\n",
            ".method public onCreate()V\n",
            "    .locals 1\n",
            "\n",
            "    invoke-super {p0}, Landroid/app/Application;->onCreate()V\n",
            "\n",
            f"    {call}\n",
            "\n",
            "    return-void\n",
            ".end method\n",
        ]))
        d = smali_dirs[0] / pkg
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{cls}.smali").write_text(smali)
        new_txt, n = re.subn(r'<application\b', f'<application android:name="{dotted_new}"',
                             txt, count=1)
        if n:
            mf.write_text(new_txt)
            return f"naya Application banaya: {dotted_new}"
        return "manifest edit fail (application tag nahi mila)"
    stxt = target.read_text(errors="surrogateescape")
    m2 = ONCREATE_RE.search(stxt)
    if m2:
        rest = stxt[m2.end():]
        m3 = LOCALS_RE.search(rest)
        if m3:
            pos = m2.end() + m3.end()
            stxt = stxt[:pos] + "\n" + call + "\n" + stxt[pos:]
        else:
            stxt = stxt[:m2.end()] + "    " + call + "\n" + stxt[m2.end():]
    else:
        new_m = ("\n.method public onCreate()V\n    .locals 1\n\n"
                 "    invoke-super {p0}, Landroid/app/Application;->onCreate()V\n\n"
                 f"    {call}\n\n    return-void\n.end method\n")
        stxt = stxt.rstrip("\n") + "\n" + new_m
    target.write_text(stxt, errors="surrogateescape")
    return f"inject hua: {dotted}"


def harden_manifest(dec: Path) -> bool:
    """android:debuggable=true mile to false karo. Returns True agar badla."""
    mf = dec / "AndroidManifest.xml"
    txt = mf.read_text(errors="ignore")
    new_txt, n = re.subn(r'android:debuggable="true"', 'android:debuggable="false"', txt)
    if n:
        mf.write_text(new_txt)
        return True
    return False


# ==================================================================== apktool
def apktool_decode(java: str, apktool: Path, apk: Path, out: Path) -> None:
    r = run([java, "-Xmx1800m", "-jar", str(apktool), "d", "-f", "-o", str(out), str(apk)])
    if r.returncode != 0 or not (out / "apktool.yml").exists():
        die(f"apktool decode fail:\n{(r.stdout or '')[-2000:]}\n{(r.stderr or '')[-1200:]}")


def apktool_build(java: str, apktool: Path, folder: Path, out: Path) -> None:
    r = run([java, "-Xmx1800m", "-jar", str(apktool), "b", str(folder), "-o", str(out)])
    if r.returncode != 0 or not out.exists():
        die(f"apktool build fail:\n{(r.stdout or '')[-2000:]}\n{(r.stderr or '')[-1200:]}")


def min_sdk_of(folder: Path) -> int:
    mf = folder / "AndroidManifest.xml"
    if mf.exists():
        txt = mf.read_text(errors="ignore")
        m = (re.search(r'<uses-sdk[^>]*minSdkVersion="(\d+)"', txt)
             or re.search(r'minSdkVersion="(\d+)"', txt))
        return int(m.group(1)) if m else 1
    yml = folder / "apktool.yml"
    if yml.exists():
        for line in yml.read_text(errors="ignore").splitlines():
            m = re.search(r"minSdkVersion:\s*'?(\d+)'?", line)
            if m:
                return int(m.group(1))
    return 21


# ================================================================== self-test
def run_self_test(skip_tools_on_missing: bool = True) -> int:
    """`zyrox --self-test` — install sahi hai ya nahi, ye verify karta hai."""
    log(BANNER)
    log("  SELF-TEST — kya sab theek se laga hai?")
    log("=" * 66)
    fails = 0

    # 1 ---------- toolchain
    log("\n[1/3] toolchain check…")
    java = ""
    try:
        java = java_bin()
        log(f"    ✅ java      : {java}  (v{java_major(java)})")
    except SystemExit:
        log("    ⚠️  java      : NAHI MILA  →  pkg install -y openjdk-17")
        fails += 0 if skip_tools_on_missing else 1

    apktool = find_tool("apktool.jar")
    if apktool:
        out = ""
        if java:
            r = run([java, "-jar", str(apktool), "--version"])
            out = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
        log(f"    ✅ apktool   : {Path(apktool).name}  {out[0] if out else ''}")
    else:
        log("    ⚠️  apktool   : nahi mila  →  bash setup.sh")
        fails += 0 if skip_tools_on_missing else 1

    signer = None
    if java:
        try:
            signer = Signer(java)
            log(f"    ✅ signer    : {signer.describe()}"
                + ("" if signer.supports_v3 else "  [i] v3 support nahi (v1+v2 chalega)"))
        except SystemExit:
            signer = None
    if signer is None:
        log("    ⚠️  signer    : nahi mila  →  bash setup.sh  (ya: pkg install -y apksigner)")
        fails += 0 if skip_tools_on_missing else 1

    # 2 ---------- crypto
    log("\n[2/3] crypto round-trip (encrypt → decrypt == original)…")
    tests = ["a", "", "Hello secret world", "sk_live_9f8a",
             "https://api.example.com/x?token=1&a=b#c", "हिन्दी टेस्ट", "中文 العربية",
             "🙏🕉️ emoji", 'line\nbreak\ttab"q\\s', "\x00null\x1f", "a" * 500]
    pairs = [(0x101, 0x81), (0x7FFE, 0x7FFF), (0x3B54, 0x3BDE)]
    n = bad = 0
    for seed, mult in pairs:
        for t in tests:
            n += 1
            if roundtrip(t, seed, mult) != t:
                bad += 1
                log(f"    ❌ FAIL seed=0x{seed:04x} mult=0x{mult:04x} text={t[:30]!r}")
    if bad == 0:
        log(f"    ✅ {n}/{n} PASS  (ASCII, हिन्दी, 中文, العربية, emoji, NUL, 500-char)")
    else:
        log(f"    ❌ {bad}/{n} FAIL")
        fails += 1

    # 2b --------- per-string crypto (v1.2)
    log("\n[2b/3] per-string crypto (har string ki apni seed)…")
    rnd2 = random.Random(os.urandom(8))
    n2 = bad2 = 0
    for t in tests:
        mask = rnd2.randint(0x101, 0xFFFE)
        seed = rnd2.randint(0x1, 0xFFFE)
        n2 += 1
        if not t or roundtrip2(t, seed, mask) != t:
            if t:
                bad2 += 1
                log(f"    ❌ FAIL seed=0x{seed:04x} mask=0x{mask:04x} text={t[:30]!r}")
    # alag-alag seeds = alag-alag ciphertext (same plaintext)
    c1 = enc_payload(to_units("same secret"), 0x1234, 0xABCD)
    c2 = enc_payload(to_units("same secret"), 0x5678, 0xABCD)
    if c1 == c2:
        bad2 += 1
        log("    ❌ FAIL: same plaintext, different seeds — ciphertext same!")
    n2 += 1
    if bad2 == 0:
        log(f"    ✅ {n2}/{n2} PASS  (per-string seeds unique ciphertext dete hain)")
    else:
        log(f"    ❌ {bad2}/{n2} FAIL")
        fails += 1

    # 3 ---------- zip writer
    log("\n[3/3] zip writer + alignment…")
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        pth = Path(td) / "selftest.apk"
        ents = [
            {"name": "AndroidManifest.xml", "data": b"\x03\x00manifest" * 40,
             "method": 8, "isdir": False},
            {"name": "resources.arsc", "data": bytes(range(256)) * 30,
             "method": 0, "isdir": False},
            {"name": "lib/arm64-v8a/libx.so", "data": bytes(range(256)) * 200,
             "method": 0, "isdir": False},
            {"name": "classes.dex", "data": b"dex\n035\x00" + b"\xAB" * 3000,
             "method": 8, "isdir": False},
        ]
        write_zip(pth, ents)
        ok, msg, _ = sanity_check_zip(pth)
        log(f"    {'✅' if ok else '❌'} {msg}")
        if not ok:
            fails += 1

    log("\n" + "=" * 66)
    if fails == 0:
        log("   ✅ SELF-TEST PASS — tool ready hai!")
        log("      ab chalayein:   zyrox /sdcard/myapp.apk")
    else:
        log(f"   ❌ {fails} check fail — upar dekhein")
    log("=" * 66)
    return 0 if fails == 0 else 2

# ======================================================================= main
class ZyroxParser(argparse.ArgumentParser):
    def error(self, message):
        if "unrecognized arguments" in message:
            log(f"\n❌ {message}")
            log('\n💡 Hint: agar APK ke path me SPACE hai to quotes lagao:')
            log('   zyrox "/storage/emulated/0/Protect Apk/myapp.apk"')
            log("   (bina quotes ke shell path ko tod deta hai — 'Protect' aur 'Apk/' alag ho jaate hain)")
            sys.exit(2)
        super().error(message)


def main() -> None:
    ap = ZyroxParser(
        prog="zyrox",
        description="ZYROX ENCRYPTION — APK string-encryption + debug-strip + signing CLI",
        epilog="Example:  zyrox /sdcard/myapp.apk --keys my-release.jks")
    ap.add_argument("apk", nargs="?", help="APK ka path, jaise /sdcard/myapp.apk")
    ap.add_argument("--out", default=str(HOME / "out"), help="output folder (default ./out)")
    ap.add_argument("--no-strings", action="store_true", help="string encryption band karo")
    ap.add_argument("--keep-debug", action="store_true", help="debug info mat hatao")
    ap.add_argument("--simple-keys", action="store_true", help="keys direct (debug ke liye)")
    ap.add_argument("--no-sign", action="store_true", help="sign mat karo")
    ap.add_argument("--min-len", type=int, default=1,
                    help="is se chhoti strings encrypt nahi hongi (default 1 = sab, khaali chhod ke)")
    ap.add_argument("--anti-debug", action="store_true",
                    help="debugger attach ho to app turant band (Application.onCreate me guard)")
    ap.add_argument("--anti-root", action="store_true",
                    help="rooted device pe app turant band (su binary + test-keys check)")
    ap.add_argument("--harden-manifest", action="store_true",
                    help="android:debuggable=true mile to false kar do")
    ap.add_argument("--ks-file", "--keys", dest="ks_file", default=None,
                    help="apni keystore (.jks/.ks/.keystore) — purani app ke update ke liye")
    ap.add_argument("--ks-pass", default=None, help="keystore password")
    ap.add_argument("--key-pass", default=None, help="key password (same ho to chhod dein)")
    ap.add_argument("--alias", default=None, help="keystore alias")
    ap.add_argument("--to-storage", action="store_true", default=None,
                    help="output /sdcard/Download/zyrox me copy karo")
    ap.add_argument("--info", action="store_true", help="sirf jaankari dikhao (kuch modify nahi)")
    ap.add_argument("--self-test", action="store_true",
                    help="install check: java + apktool + signer + crypto + zip (APK ki zaroorat nahi)")
    ap.add_argument("--version", action="version", version=f"ZYROX ENCRYPTION v{VERSION}\n{REPO}")
    args = ap.parse_args()

    if args.self_test:
        sys.exit(run_self_test())

    if not args.apk:
        log(f"ZYROX ENCRYPTION v{VERSION}  ·  {REPO}")
        print(__doc__)
        die("APK ka path do. Example:\n   zyrox /sdcard/Download/myapp.apk")

    java = java_bin()
    apktool = find_tool("apktool.jar")
    if not apktool:
        die("apktool.jar nahi mila — chalayein:  bash setup.sh")

    apk = Path(os.path.expanduser(args.apk)).resolve()
    if apk.is_dir():
        found = sorted(apk.glob("*.apk")) or sorted(apk.rglob("*.apk"))
        if len(found) == 1:
            log(f"[i] folder diya tha — APK mil gaya: {found[0].name}")
            apk = found[0]
        elif not found:
            die(f"Folder me koi .apk nahi mila: {apk}")
        else:
            die("Folder me kai APK mile — kaunsi wali?\n" +
                "\n".join(f"   - {f.name}" for f in found[:10]))
    if not apk.exists():
        die(f"APK nahi mila: {apk}")
    if apk.suffix.lower() not in (".apk",):
        log(f"[i] dhyaan: '{apk.suffix}' file hai — ye tool single .apk pe chalta hai (AAB/split support nahi)")

    outdir = Path(os.path.expanduser(args.out)).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    protected = outdir / f"{apk.stem}-protected.apk"

    log(BANNER)
    log(f"  ZYROX ENCRYPTION v{VERSION}  ·  {REPO}")
    log("=" * 66)
    log(f"  input : {apk.name}  ({human(apk.stat().st_size)})")
    log(f"  java  : v{java_major(java)}")

    signer = Signer(java)
    log(f"  signer: {signer.describe()}" + ("" if signer.supports_v3 else "   [i] v3 support nahi (v1+v2 chalega)"))

    log("\n  [i] input APK ki current signature:")
    _, vtxt_in = signer.verify(apk)
    dn = [l.strip() for l in vtxt_in.splitlines() if "Signer #1 certificate DN:" in l]
    if dn:
        log("      " + dn[0])
    else:
        log("      (verify nahi hua — unsigned/toota hua APK ho sakta hai)")
    log("      ⚠️  sirf apni APK pe chalayein — dusre ki app pe ye illegal hai")

    if args.info:
        import zipfile
        with zipfile.ZipFile(apk) as z:
            names = z.namelist()
            dexes = [n for n in names if DEX_RE.match(n)]
            total_dex = sum(z.getinfo(n).file_size for n in dexes)
        log("\n  [i] jaankari:")
        log(f"      dex files : {', '.join(dexes) or 'koi nahi'}  ({human(total_dex)})")
        log(f"      entries   : {len(names)}")
        log("\n  --info mode: kuch modify nahi kiya.")
        return

    work = Path(tempfile.mkdtemp(prefix="zyrox_"))
    t0 = time.time()
    total = 5 if args.no_sign else 6
    stats = {"strings": 0, "units": 0, "classes": 0}

    try:
        # 1 ---------------------------------------------------- decode
        log(f"\n[1/{total}] apktool se decode (smali + resources)…")
        dec = work / "dec"
        apktool_decode(java, apktool, apk, dec)
        smali_dirs = sorted([d for d in dec.iterdir() if d.is_dir() and SMALI_DIR_RE.match(d.name)],
                            key=lambda d: (d.name != "smali", d.name))
        if not smali_dirs:
            die("Koi smali folder nahi mila — APK protected/split lag rahi hai.")
        min_sdk = min_sdk_of(dec)
        log(f"    smali: {', '.join(d.name for d in smali_dirs)}   minSdk={min_sdk}")

        # 2 ---------------------------------------------------- encrypt + strip
        rnd = random.Random(os.urandom(16))
        keys = None
        crypto_mode = "off"
        if args.no_strings:
            log(f"\n[2/{total}] string encryption SKIP")
        else:
            log(f"\n[2/{total}] strings encrypt + debug strip…")
            keys = gen_keys(rnd, args.simple_keys)
            crypto_mode = "simple (direct keys)" if args.simple_keys else "per-string keys"
            strip = not args.keep_debug
            for sd in smali_dirs:
                for f in sd.rglob("*.smali"):
                    txt = f.read_text(errors="surrogateescape")
                    new = transform(txt, keys, stats, strip, max(1, args.min_len), rnd)
                    if new != txt:
                        f.write_text(new, errors="surrogateescape")
                    stats["classes"] += 1
            ddir = dec / "smali" / keys["pkg"]
            ddir.mkdir(parents=True, exist_ok=True)
            (ddir / f"{keys['cls']}.smali").write_text(
                decryptor_smali(keys["desc"], keys["meth"], keys))
            log(f"    ✅ {stats['strings']} strings encrypt ({stats['units']} UTF-16 units), "
                f"{stats['classes']} classes   [min-len={max(1, args.min_len)}]")
            log(f"    decryptor: {keys['desc']}->{keys['meth']}()   [{crypto_mode}]")
            tests = ["a", "Hello secret world", "sk_live_9f8a", "https://x.io/a?b=1&c=%20#d",
                     "हिन्दी टेस्ट", "中文 العربية", "🙏🕉️ emoji", 'line\nbreak\ttab"q\\s',
                     "\x00null\x1f", "a" * 400]
            if args.simple_keys:
                bad = [t for t in tests if roundtrip(t, keys["seed"], keys["mult"]) != t]
            else:
                bad = [t for t in tests
                       if roundtrip2(t, rnd.randint(0x1, 0xFFFE), keys["mask"]) != t]
            if bad:
                die(f"crypto self-test FAIL: {bad[:2]!r}")
            log("    self-test: 10/10 PASS ✅")

        # 2b --------------------------------------------------- guards
        guard_info = None
        if args.anti_debug or args.anti_root:
            log(f"\n[2b/{total}] runtime guard inject (anti-debug/root)…")
            gpkg = "".join(rnd.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rnd.randint(4, 7)))
            gcls = "".join(rnd.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
                           for _ in range(rnd.randint(5, 8)))
            gmeth = "g" + "".join(rnd.choice("abcdefghijklmnopqrstuvwxyz")
                                  for _ in range(rnd.randint(4, 8)))
            gdir = dec / "smali" / gpkg
            gdir.mkdir(parents=True, exist_ok=True)
            (gdir / f"{gcls}.smali").write_text(
                guard_smali(gpkg, gcls, gmeth, args.anti_debug, args.anti_root))
            guard_info = inject_guard_call(dec, smali_dirs, f"L{gpkg}/{gcls};", gmeth, rnd)
            log(f"    ✅ guard {guard_info}")
            log(f"       (debug={'ON' if args.anti_debug else 'off'}, "
                f"root={'ON' if args.anti_root else 'off'})")

        # 2c --------------------------------------------------- manifest harden
        hardened = False
        if args.harden_manifest:
            log(f"\n[2c/{total}] manifest harden…")
            hardened = harden_manifest(dec)
            log("    ✅ android:debuggable=false" if hardened
                else "    [i] debuggable pehle se off tha")

        # 3 ---------------------------------------------------- rebuild
        log(f"\n[3/{total}] apktool se rebuild (naya dex)…")
        built = work / "built.apk"
        apktool_build(java, apktool, dec, built)
        import zipfile
        new_dex: dict[str, bytes] = {}
        with zipfile.ZipFile(built) as z:
            for n in z.namelist():
                if DEX_RE.match(n):
                    new_dex[n] = z.read(n)
        if not new_dex:
            die("Build me koi dex nahi bana")
        log("    " + ", ".join(f"{n}: {human(len(b))}" for n, b in sorted(new_dex.items())))

        # 4 ---------------------------------------------------- repack original
        log(f"\n[4/{total}] original APK me dex replace + align…")
        entries = read_zip(apk)
        names = {e["name"] for e in entries}
        out_entries, removed = [], 0
        for e in entries:
            if SIG_RE.match(e["name"]):
                removed += 1
                continue
            if DEX_RE.match(e["name"]) and e["name"] in new_dex:
                out_entries.append({**e, "data": new_dex[e["name"]]})
            else:
                out_entries.append(e)
        for n, b in new_dex.items():
            if n not in names:
                out_entries.append({"name": n, "data": b, "method": 8, "isdir": False})
        unsigned = work / "unsigned.apk"
        write_zip(unsigned, out_entries)
        zok, zmsg, znotes = sanity_check_zip(unsigned)
        log(f"    zip check: {'✅' if zok else '❌'} {zmsg}")
        for n in znotes:
            log(f"    [i] {n}")
        if not zok:
            die("Repack sanity check fail")
        log(f"    ✅ {len(out_entries)} entries"
            + (f", {removed} purani signature files hataayi" if removed else ""))

        # 5 / 6 ------------------------------------------------- sign + verify
        if args.no_sign:
            shutil.copy2(unsigned, protected)
            log(f"\n[5/{total}] sign SKIP — output unsigned: {protected}")
        else:
            log(f"\n[5/{total}] sign (v1+v2" + ("+v3" if signer.supports_v3 else "") + ")…")
            if args.ks_file:
                ks = Path(os.path.expanduser(args.ks_file)).resolve()
                if not ks.exists():
                    die(f"Keystore nahi mila: {ks}")
                alias = args.alias or "key0"
                kp = args.ks_pass or ""
                keyp = args.key_pass or kp
                if not kp:
                    die("--ks-file ke saath --ks-pass bhi dena zaroori hai")
            else:
                keys_dir = HOME / "keys"
                keys_dir.mkdir(parents=True, exist_ok=True)
                ks = keys_dir / f"{apk.stem}.jks"
                cred = keys_dir / f"{apk.stem}-password.txt"
                alias = "zyrox"
                if ks.exists() and cred.exists():
                    kp = cred.read_text().strip()
                    log(f"    pehle se maujood key use ho rahi hai: {ks.name}")
                else:
                    kp = "".join(random.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(16))
                    r = run([java_keytool(java), "-genkeypair", "-keystore", str(ks),
                             "-alias", alias, "-keyalg", "RSA", "-keysize", "2048",
                             "-validity", "10000", "-storetype", "PKCS12",
                             "-storepass", kp, "-keypass", kp,
                             "-dname", "CN=ZYROX ENCRYPTION, OU=Protection, O=Private, C=IN"])
                    if r.returncode != 0:
                        die(f"keytool fail:\n{(r.stderr or '')[-800:]}")
                    cred.write_text(kp + "\n")
                    log(f"    🔑 nayi key banayi : {ks}")
                    log(f"       password file   : {cred}   (sambhal ke rakhein!)")
                keyp = kp
            signer.sign(unsigned, protected, ks, alias, kp, keyp, min_sdk)

            idsig = Path(str(protected) + ".idsig")   # v4 side-file (optional)
            if idsig.exists():
                idsig.unlink()

            log(f"\n[6/{total}] verify (proof)…")
            ok, vtxt = signer.verify(protected)
            for line in vtxt.splitlines():
                s = line.strip()
                if s.startswith(("Verifies", "Verified using")):
                    log("    " + s)
            if not ok:
                die("Verify FAIL — output bharosemand nahi hai")

        # ---- phone storage me copy
        stored = None
        if args.to_storage:
            for base in (Path("/sdcard/Download"), Path("/storage/emulated/0/Download"),
                         Path.home() / "storage" / "downloads"):
                if base.exists() and os.access(base, os.W_OK):
                    dest = base / "zyrox"
                    dest.mkdir(exist_ok=True)
                    shutil.copy2(protected, dest / protected.name)
                    stored = dest / protected.name
                    break
            if stored:
                log(f"\n    📲 phone storage me copy: {stored}")
            else:
                log("\n    [i] storage copy nahi hua — Termux me `termux-setup-storage` chalayein")

        # ---- report
        rep = {
            "tool": f"ZYROX ENCRYPTION v{VERSION}", "repo": REPO,
            "input": str(apk), "input_bytes": apk.stat().st_size,
            "input_sha256": hashlib.sha256(apk.read_bytes()).hexdigest(),
            "output": str(protected), "output_bytes": protected.stat().st_size,
            "output_sha256": hashlib.sha256(protected.read_bytes()).hexdigest(),
            "strings_encrypted": stats["strings"], "utf16_units": stats["units"],
            "classes_processed": stats["classes"],
            "debug_stripped": (not args.keep_debug) and (not args.no_strings),
            "crypto_mode": crypto_mode,
            "min_len": max(1, args.min_len),
            "guard": guard_info,
            "manifest_hardened": hardened,
            "min_sdk": min_sdk, "signed": not args.no_sign,
            "seconds": round(time.time() - t0, 1),
        }
        (outdir / f"{apk.stem}-report.json").write_text(json.dumps(rep, indent=2))

        log("\n" + "=" * 66)
        log("   ✅  HO GAYA!")
        log("=" * 66)
        log(f"  output : {protected}")
        log(f"  size   : {human(rep['input_bytes'])} → {human(rep['output_bytes'])} "
            f"({(rep['output_bytes']-rep['input_bytes'])/rep['input_bytes']*100:+.1f}%)")
        if stats["strings"]:
            log(f"  crypto : {stats['strings']} strings encrypted ({stats['units']} units)")
        log(f"  sha256 : {rep['output_sha256'][:40]}…")
        log(f"  time   : {rep['seconds']}s")
        log(f"\n  NEXT → phone pe install karke test karein:")
        log(f"     file manager se tap karein, ya:  adb install -r \"{protected}\"")
        log("  Crash ho to:  adb logcat | grep -iE \"fatal|exception\"")
        log(f"\n  Proof report:   python zyrox-verify.py \"{apk}\" \"{protected}\"\n")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
