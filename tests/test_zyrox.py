#!/usr/bin/env python3
"""
tests/test_zyrox.py — ZYROX ENCRYPTION ke tests
==============================================
Chalane ke 2 tareeke:
    python tests/test_zyrox.py          # seedha (koi dependency nahi)
    pytest tests/                        # pytest se bhi chalte hain

Ye tests **Java/apktool ki zaroorat nahi** rakhte — sirf pure-python logic test karte hain:
  * crypto round-trip (ASCII, unicode, emoji, control chars)
  * UTF-16 unit conversion (Java semantics)
  * keystream formula (int overflow round-trip)
  * zip writer (alignment + integrity + zip64 path)
  * decryptor smali generation (syntax sanity)
  * key hiding (XOR-split) consistency
"""
from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import zyrox  # noqa: E402
from zyrox import (  # noqa: E402
    decryptor_smali, from_units, gen_keys, ks, roundtrip, sanity_check_zip,
    to_units, units_escape, unescape, write_zip, xcrypt_units,
)

import random  # noqa: E402

EDGE_CASES = [
    "",
    "a",
    "Hello secret world",
    "sk_live_9f8a7b6c5d4e3f2a1b0c",
    "https://api.example.com/v1?token=abc&x=%20#frag",
    "हिन्दी टेस्ट",
    "中文 العربية русский",
    "🙏🕉️ emoji with surrogate pairs 🚀",
    'quote " backslash \\ tab\t newline\n return\r',
    "\x00null-start\x1f",
    " ",
    "ends with space ",
    "a" * 500,
]

KEY_PAIRS = [(0x101, 0x81), (0x7FFE, 0x7FFF), (0x3B54, 0x3BDE), (0x0001, 0x0002)]


# --------------------------------------------------------------- crypto tests
def test_crypto_roundtrip_all_edge_cases():
    for seed, mult in KEY_PAIRS:
        for text in EDGE_CASES:
            got = roundtrip(text, seed, mult)
            # Java-semantics: UTF-16 unit level pe exact match (emoji = surrogate pair)
            assert to_units(got) == to_units(text), (seed, mult, repr(text))
            # jo strings already valid UTF-16 hain unke liye str-level bhi match
            if all(not (0xD800 <= ord(c) <= 0xDFFF) for c in text):
                assert got == text, (seed, mult, repr(text))


def test_crypto_is_not_identity():
    """Encrypt hone ke baad text badalna chahiye (warna protection hi nahi)."""
    for seed, mult in KEY_PAIRS[:2]:
        for text in [t for t in EDGE_CASES if len(t) > 3]:
            enc = from_units(xcrypt_units(to_units(text), seed, mult))
            assert enc != text, (seed, mult, repr(text))


def test_utf16_units_match_java_semantics():
    """Emoji Java me 2 chars hote hain — units count exactly wahi hona chahiye."""
    assert len(to_units("a")) == 1
    assert len(to_units("🙏")) == 2          # surrogate pair
    assert len(to_units("हिन्दी")) == 6
    assert to_units("") == []
    # round-trip intact rehna chahiye (lone surrogates bhi)
    for s in EDGE_CASES:
        assert from_units(to_units(s)) == s, repr(s)


def test_keystream_is_16bit():
    """Java int-to-char sirf low 16 bits leta hai."""
    for i in range(0, 400):
        v = ks(i, 0x7FFE, 0x7FFF)
        assert 0 <= v <= 0xFFFF, (i, v)
    # consecutive keystream values mult ke mod 2^16 se badhte hain
    assert ks(1, 0x0101, 0x0103) == (0x0101 + 0x0103) & 0xFFFF


def test_xcrypt_involution_within_16bit():
    """do baar XOR = original (jab tak keys 16-bit me hain)."""
    rnd = random.Random(1234)
    for _ in range(50):
        seed = rnd.randint(0, 0xFFFF)
        mult = rnd.randint(0, 0xFFFF)
        text = "".join(chr(rnd.randint(0x20, 0xFFFF)) for _ in range(rnd.randint(1, 60)))
        # unit-level equality (random strings me surrogate chars aa sakte hain)
        assert to_units(roundtrip(text, seed, mult)) == to_units(text)


# ------------------------------------------------------------ smali escaping
def test_smali_escape_unescape_roundtrip():
    """
    NOTE: comparison **UTF-16 unit level** pe hoti hai (Java ke saath identical
    semantics). Emoji/astral chars Python me 1 char hote hain par Java me 2 —
    isliye unit-level hi sahi tareeka hai (asli tool bhi yahi karta hai).
    """
    for text in EDGE_CASES:
        esc = units_escape(to_units(text))
        assert to_units(unescape(esc)) == to_units(text), repr(text)
        # escaped form sirf printable ASCII ho (decompiler me non-ASCII blob dikhe)
        assert all(0x20 <= ord(c) <= 0x7E for c in esc), repr(esc)
        # aur original ke saare non-ASCII chars escape ho gaye hon
        assert all(c not in esc for c in text if ord(c) > 0x7E), repr(text)


def test_units_escape_handles_quotes_and_backslash():
    units = to_units('a"b\\c')
    esc = units_escape(units)
    assert '\\"' in esc and "\\\\" in esc
    assert unescape(esc) == 'a"b\\c'


# ------------------------------------------------------------------ key gen
def test_gen_keys_roundtrip_and_split_correctness():
    rnd = random.Random(99)
    for simple in (False, True):
        k = gen_keys(rnd, simple)
        assert 0x101 <= k["seed"] <= 0x7FFE
        assert 0x81 <= k["mult"] <= 0x7FFF
        assert (k["ma"] ^ k["mb"]) == k["mult"], "XOR-split mismatch (mult)"
        assert (k["sa"] ^ k["sb"]) == k["seed"], "XOR-split mismatch (seed)"
        assert k["desc"] == f"L{k['pkg']}/{k['cls']};"
        assert k["meth"].startswith("a")
        assert roundtrip("check me", k["seed"], k["mult"]) == "check me"


def test_gen_keys_unique_names_across_builds():
    names = {gen_keys(random.Random(os.urandom(16)), False)["desc"] for _ in range(25)}
    assert len(names) == 25, "random naam repeat ho rahe hain"


def test_decryptor_smali_shape():
    k = gen_keys(random.Random(7), False)
    smali = decryptor_smali(k["desc"], k["meth"], k)
    assert smali.startswith(f".class public final {k['desc']}")
    assert f".method public static {k['meth']}(Ljava/lang/String;)Ljava/lang/String;" in smali
    assert ".end method" in smali
    assert "invoke-virtual" in smali and "int-to-char" in smali and "new-instance" in smali
    # labels balanced
    assert smali.count(":cond_0") == 2 and smali.count(":cond_1") == 2
    # hidden mode me direct keys nahi hone chahiye
    assert f"0x{k['seed']:04x}" not in smali, "seed smali me direct dikh raha hai!"
    assert f"0x{k['mult']:04x}" not in smali, "mult smali me direct dikh raha hai!"
    # simple mode me keys direct hote hain
    simple = decryptor_smali(k["desc"], k["meth"], {**k, "simple": True})
    assert f"0x{k['seed']:04x}" in simple and f"0x{k['mult']:04x}" in simple


# ------------------------------------------------------------------ zip tests
def _entries():
    return [
        {"name": "AndroidManifest.xml", "data": b"\x03\x00manifest" * 40, "method": 8, "isdir": False},
        {"name": "resources.arsc", "data": bytes(range(256)) * 30, "method": 0, "isdir": False},
        {"name": "lib/arm64-v8a/libx.so", "data": bytes(range(256)) * 200, "method": 0, "isdir": False},
        {"name": "lib/armeabi-v7a/libx.so", "data": bytes(range(256)) * 50, "method": 0, "isdir": False},
        {"name": "classes.dex", "data": b"dex\n035\x00" + b"\xAB" * 3000, "method": 8, "isdir": False},
        {"name": "res/drawable/icon.png", "data": os.urandom(1500), "method": 0, "isdir": False},
    ]


def test_zip_writer_integrity_and_alignment():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t.apk"
        write_zip(p, _entries())
        assert zipfile.ZipFile(p).testzip() is None, "zip corrupt"
        ok, msg, _ = sanity_check_zip(p)
        assert ok, msg


def test_zip_writer_preserves_data_exactly():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t.apk"
        ents = _entries()
        write_zip(p, ents)
        with zipfile.ZipFile(p) as z:
            for e in ents:
                assert z.read(e["name"]) == e["data"], e["name"]


def test_zip_writer_offsets_are_aligned():
    import struct
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t.apk"
        write_zip(p, _entries())
        with zipfile.ZipFile(p) as z, open(p, "rb") as fh:
            for zi in z.infolist():
                if zi.compress_type != 0:
                    continue
                fh.seek(zi.header_offset + 26)
                nlen, elen = struct.unpack("<HH", fh.read(4))
                off = zi.header_offset + 30 + nlen + elen
                need = 4096 if zi.filename.endswith(".so") else 4
                assert off % need == 0, (zi.filename, off, need)


def test_zip64_path_many_entries():
    """>65k entries wale zip64 branch ka sanity (bade APK me hota hai)."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "big.apk"
        ents = [{"name": f"res/x{i}.bin", "data": b"x" * 4, "method": 0, "isdir": False}
                for i in range(70000)]
        write_zip(p, ents)
        with zipfile.ZipFile(p) as z:
            assert len(z.namelist()) == 70000


# ------------------------------------------------------------------ helpers
def test_human_readable():
    assert zyrox.human(512) == "512 B"
    assert zyrox.human(1536).startswith("1.5")
    assert zyrox.human(5 * 1024 * 1024).startswith("5.0")


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
            passed += 1
        except AssertionError as exc:
            print(f"  ❌ {name}\n       {exc}")
            failed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  💥 {name}  ({type(exc).__name__}: {exc})")
            failed += 1
    print(f"\n  {passed} passed, {failed} failed  ({len(tests)} total)")
    return 1 if failed else 0


if __name__ == "__main__":
    print("=" * 60)
    print("  ZYROX ENCRYPTION — test suite")
    print("=" * 60)
    sys.exit(_run_all())
