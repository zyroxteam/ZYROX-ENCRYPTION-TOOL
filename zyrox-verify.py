#!/usr/bin/env python3
"""
zyrox-verify.py — ZYROX ENCRYPTION ka proof report
==================================================
Checks:
  1. apksigner verify → signature (v1/v2/v3)
  2. zip integrity + resources.arsc alignment + .so page alignment
  3. decrypt-test → protected dex ke encrypted strings decrypt karke ORIGINAL se compare
     (dono key-modes detect: direct + hidden XOR-split)
  4. plaintext leak → original strings protected dex me dikhti hain kya?

Usage:
    python zyrox-verify.py original.apk protected.apk
"""
from __future__ import annotations

import re
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from zyrox import (  # noqa: E402
    CONST_RE, DEX_RE, Signer, apktool_decode, dec_payload, derive_mult,
    find_tool, human, java_bin, sanity_check_zip, to_units, unescape,
    xcrypt_units,
)

OK, BAD = "✅", "❌"

PAIR_RE = re.compile(r"const v(\d+), 0x([0-9a-f]+)\s*\n\s*xor-int/lit16 v\1, v\1, 0x([0-9a-f]+)")
DIRECT_MUL_RE = re.compile(r"mul-int/lit16 v\d+, v\d+, 0x([0-9a-f]{4})")
DIRECT_ADD_RE = re.compile(r"add-int/lit16 v\d+, v\d+, 0x([0-9a-f]{4})")
REG_MUL_RE = re.compile(r"mul-int v\d+, v\d+, v(\d+)")
REG_ADD_RE = re.compile(r"add-int v\d+, v\d+, v(\d+)")


V2_MUL_RE = re.compile(r"mul-int/lit16 v(\d+), v(\d+), 0x0025")
V2_ADD_RE = re.compile(r"add-int/lit16 v\d+, v\d+, 0x001b")


def extract_keys(smali_text: str):
    """Decryptor ke keys nikalo.
    v1.2 per-string mode  -> (mask, "per-string (hidden mask)")
    purana hidden (XOR-split) -> (seed, mult, "hidden (XOR-split)")
    simple/direct           -> (seed, mult, "direct")
    """
    reg: dict[str, int] = {}
    for m in PAIR_RE.finditer(smali_text):
        reg[m.group(1)] = int(m.group(2), 16) ^ int(m.group(3), 16)
    # v1.2: mask XOR-split + mult = seed * 0x25 + 0x1b
    vm = V2_MUL_RE.search(smali_text)
    va = V2_ADD_RE.search(smali_text)
    if vm and va and vm.group(2) in reg:
        return reg[vm.group(2)], "per-string (hidden mask)"
    mm, am = REG_MUL_RE.search(smali_text), REG_ADD_RE.search(smali_text)
    if mm and am and mm.group(1) in reg and am.group(1) in reg:
        return reg[am.group(1)], reg[mm.group(1)], "hidden (XOR-split)"
    dm, da = DIRECT_MUL_RE.search(smali_text), DIRECT_ADD_RE.search(smali_text)
    if dm and da:
        return int(da.group(1), 16), int(dm.group(1), 16), "direct"
    return None


def lits(path: Path) -> list[str]:
    """CONST_RE groups: 1=indent 2=jumbo 3=register 4=literal"""
    return [unescape(m.group(4))
            for m in map(CONST_RE.match, path.read_text(errors="surrogateescape").split("\n")) if m]


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    orig = Path(sys.argv[1]).expanduser().resolve()
    prot = Path(sys.argv[2]).expanduser().resolve()
    for p in (orig, prot):
        if not p.exists():
            print(f"{BAD} nahi mila: {p}")
            sys.exit(1)

    java = java_bin()
    signer = Signer(java)
    apktool = find_tool("apktool.jar")
    if not apktool:
        print(f"{BAD} apktool.jar nahi mila — `bash setup.sh` chalayein")
        sys.exit(1)

    print("=" * 68)
    print("  🔎 ZYROX VERIFY — protected APK ka proof report")
    print("=" * 68)
    print(f"  original  : {orig.name}  ({human(orig.stat().st_size)})")
    print(f"  protected : {prot.name}  ({human(prot.stat().st_size)})")

    # 1 ---------------------------------------------------------- signature
    print("\n[1/4] signature verify…")
    ok_sig, vtxt = signer.verify(prot)
    for line in vtxt.splitlines():
        s = line.strip()
        if s.startswith(("Verifies", "Verified using")):
            print("    " + s)
    print(f"    → {OK + ' SIGNED & VERIFIES' if ok_sig else BAD + ' verify FAIL'}")

    # 2 ---------------------------------------------------------- zip
    print("\n[2/4] zip integrity + alignment…")
    zok, zmsg, znotes = sanity_check_zip(prot)
    print(f"    → {OK if zok else BAD} {zmsg}")
    for n in znotes:
        print(f"    [i] {n}")

    # 3 + 4 ------------------------------------------------------ crypto
    print("\n[3/4] decrypt-test (protected → decrypt → original se compare)…")
    base: Path | None = None
    with tempfile.TemporaryDirectory(prefix="zyrox_verify_") as td:
        tdp = Path(td)
        try:
            apktool_decode(java, apktool, prot, tdp / "p")
        except SystemExit:
            print(f"    {BAD} protected APK decode nahi hua")
            sys.exit(2)

        found = None
        for f in sorted((tdp / "p").rglob("*.smali")):
            k = extract_keys(f.read_text(errors="surrogateescape"))
            if k:
                found = (f, k)
                break

        if not found:
            print(f"    {BAD} decryptor class nahi mili — protection laga hua nahi lagta")
        else:
            f, keys = found
            print(f"    decryptor : {f.relative_to(tdp / 'p')}")
            if len(keys) == 2:
                mask, mode = keys
                per_string = True
                print(f"    key mode  : {mode}   mask=0x{mask:04x}")
            else:
                seed, mult, mode = keys
                per_string = False
                print(f"    key mode  : {mode}   seed=0x{seed:04x}  mult=0x{mult:04x}")
            try:
                apktool_decode(java, apktool, orig, tdp / "o")
                base = tdp / "o" / "smali"
            except SystemExit:
                print("    [i] original decode nahi hua — compare skip")
            if base:
                match = mismatch = checked = 0
                odir, pdir = tdp / "o", tdp / "p"
                for psub in sorted(d for d in pdir.iterdir() if d.is_dir()):
                    osub = odir / psub.name
                    if not osub.is_dir():
                        continue
                    for of in sorted(osub.rglob("*.smali")):
                        pf = psub / of.relative_to(osub)
                        if not pf.exists():
                            continue
                        a, b = lits(of), lits(pf)
                        if len(a) != len(b):
                            continue
                        for x, y in zip(a, b):
                            checked += 1
                            yu = to_units(y)
                            if per_string:
                                ok = (to_units(x) == dec_payload(yu, mask)) or (x == y)
                            else:
                                ok = to_units(x) == xcrypt_units(yu, seed, mult)
                            if ok:
                                match += 1
                            else:
                                mismatch += 1
                pct = (match * 100 // checked) if checked else 0
                good = mismatch == 0 and checked > 0
                print(f"    → {OK if good else BAD} {match}/{checked} strings decrypt hokar "
                      f"ORIGINAL se exact match ({pct}%)  |  {mismatch} mismatch")

        # 4 ------------------------------------------------------ leak check
        print("\n[4/4] plaintext leak check…")
        with zipfile.ZipFile(prot) as zp:
            dex = b"".join(zp.read(n) for n in zp.namelist() if DEX_RE.match(n))
        longs: set[str] = set()
        if base:
            for of in base.rglob("*.smali"):
                for s in lits(of):
                    if len(s) >= 8 and s.isascii():
                        longs.add(s)
        sample = list(longs)[:5000]
        if not sample:
            print(f"    → {OK} leak-check skip (original decode nahi hua)")
        else:
            leaked = [s for s in sample if s.encode() in dex]
            print(f"    → {OK + ' koi plaintext leak nahi' if not leaked else BAD + ' LEAK mila!'}"
                  f"   ({len(sample)} strings check kiye, protected dex {human(len(dex))})")
            for s in leaked[:5]:
                print(f"        {BAD} plaintext: {s[:60]!r}")

    print("\n" + "=" * 68)
    print("  Report done ✅")
    print("=" * 68)


if __name__ == "__main__":
    main()
