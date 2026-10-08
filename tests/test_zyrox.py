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
    dec_payload, decryptor_smali, derive_mult, enc_payload, from_units,
    gen_keys, guard_smali, harden_manifest, inject_guard_call, ks,
    roundtrip, roundtrip2, sanity_check_zip, to_units, transform,
    units_escape, unescape, write_zip, xcrypt_units,
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
    # simple mode: global seed/mult
    k = gen_keys(rnd, True)
    assert 0x101 <= k["seed"] <= 0x7FFE
    assert 0x81 <= k["mult"] <= 0x7FFF
    assert k["desc"] == f"L{k['pkg']}/{k['cls']};"
    assert k["meth"].startswith("a")
    assert roundtrip("check me", k["seed"], k["mult"]) == "check me"
    # per-string mode: sirf mask hidden (XOR-split)
    k2 = gen_keys(rnd, False)
    assert 0x101 <= k2["mask"] <= 0xFFFE
    assert (k2["ma"] ^ k2["mb"]) == k2["mask"], "XOR-split mismatch (mask)"
    assert "seed" not in k2 and "mult" not in k2, "per-string mode me global keys nahi hone chahiye"
    assert roundtrip2("check me", 0x1234, k2["mask"]) == "check me"


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
    # per-string mode: mask smali me direct nahi dikhna chahiye (XOR-split hai)
    assert f"0x{k['mask']:04x}" not in smali, "mask smali me direct dikh raha hai!"
    # per-string marker: mult = seed * 0x25 + 0x1b
    assert "0x0025" in smali and "0x001b" in smali
    # simple mode me keys direct hote hain
    ks_ = gen_keys(random.Random(7), True)
    simple = decryptor_smali(ks_["desc"], ks_["meth"], ks_)
    assert f"0x{ks_['seed']:04x}" in simple and f"0x{ks_['mult']:04x}" in simple


def test_per_string_crypto_roundtrip():
    rnd = random.Random(4242)
    for _ in range(30):
        mask = rnd.randint(0x101, 0xFFFE)
        seed = rnd.randint(0x1, 0xFFFE)
        for text in EDGE_CASES:
            if not text:
                continue
            got = roundtrip2(text, seed, mask)
            assert to_units(got) == to_units(text), (seed, mask, repr(text))


def test_per_string_unique_ciphertext():
    """Same plaintext + alag seeds = alag ciphertext (static analysis mushkil)."""
    units = to_units("same secret string")
    c1 = enc_payload(units, 0x1234, 0xABCD)
    c2 = enc_payload(units, 0x5678, 0xABCD)
    assert c1 != c2
    # dono wapas decrypt hote hain
    assert dec_payload(c1, 0xABCD) == units
    assert dec_payload(c2, 0xABCD) == units


def test_derive_mult_deterministic():
    assert derive_mult(0x1) == (0x25 + 0x1B) & 0xFFFF
    assert derive_mult(0x1234) == (0x1234 * 0x25 + 0x1B) & 0xFFFF
    for v in (0x1, 0xFFFE, 0xABCD):
        assert 0 <= derive_mult(v) <= 0xFFFF


def test_transform_min_len_skips_short_strings():
    rnd = random.Random(11)
    k = gen_keys(rnd, False)
    stats = {"strings": 0, "units": 0, "classes": 0}
    src = ('    const-string v0, "hi"\n'
           '    const-string v1, "long enough string"\n')
    out = transform(src, k, stats, False, 4, rnd)
    # "hi" skip hui (plaintext rahi), lambi wali encrypt hui
    assert 'const-string v0, "hi"' in out
    assert "long enough string" not in out
    assert stats["strings"] == 1
    assert out.count("invoke-static") == 1


def test_guard_smali_shape():
    sm = guard_smali("abc", "Def", "gxyz", True, True)
    assert sm.startswith(".class public final Labc/Def;")
    assert ".method public static gxyz(Landroid/content/Context;)V" in sm
    assert "isDebuggerConnected" in sm
    assert "/system/xbin/su" in sm and "test-keys" in sm
    assert ":try_start_0" in sm and ":catch_0" in sm
    # sirf debug mode me root check nahi
    sm2 = guard_smali("abc", "Def", "gxyz", True, False)
    assert "isDebuggerConnected" in sm2 and "/system/xbin/su" not in sm2


def _fake_dec(tmp, manifest, app_smali=None, app_dotted=None):
    dec = Path(tmp) / "dec"
    (dec / "smali").mkdir(parents=True)
    (dec / "AndroidManifest.xml").write_text(manifest)
    if app_smali and app_dotted:
        p = dec / "smali" / (app_dotted.replace(".", "/") + ".smali")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(app_smali)
    return dec


def test_inject_guard_existing_app():
    import tempfile
    manifest = ('<manifest package="com.ex.app">\n'
                '<application android:name="com.ex.app.MyApp" android:label="x">\n'
                '</application></manifest>')
    app = (".class public Lcom/ex/app/MyApp;\n"
           ".super Landroid/app/Application;\n\n\n"
           ".method public onCreate()V\n"
           "    .locals 2\n\n"
           "    invoke-super {p0}, Landroid/app/Application;->onCreate()V\n\n"
           "    return-void\n"
           ".end method\n")
    with tempfile.TemporaryDirectory() as td:
        dec = _fake_dec(td, manifest, app, "com.ex.app.MyApp")
        info = inject_guard_call(dec, [dec / "smali"], "Lq/w;", "gx", random.Random(5))
        assert "inject hua" in info
        new = (dec / "smali" / "com/ex/app/MyApp.smali").read_text()
        assert "Lq/w;->gx(Landroid/content/Context;)V" in new
        # .locals ke BAAD inject hua (register allocation safe)
        assert new.index(".locals 2") < new.index("Lq/w;->gx")


def test_inject_guard_no_application():
    import tempfile
    manifest = ('<manifest package="com.ex.app">\n'
                '<application android:label="x">\n'
                '</application></manifest>')
    with tempfile.TemporaryDirectory() as td:
        dec = _fake_dec(td, manifest)
        info = inject_guard_call(dec, [dec / "smali"], "Lq/w;", "gx", random.Random(5))
        assert "naya Application banaya" in info
        mf = (dec / "AndroidManifest.xml").read_text()
        assert 'android:name="' in mf
        # naya Application class bani
        created = list((dec / "smali").rglob("Z*.smali"))
        assert len(created) == 1
        assert "Landroid/app/Application;" in created[0].read_text()


def test_harden_manifest():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        dec = Path(td)
        (dec / "AndroidManifest.xml").write_text(
            '<application android:debuggable="true" android:label="x">')
        assert harden_manifest(dec) is True
        assert 'android:debuggable="false"' in (dec / "AndroidManifest.xml").read_text()
        assert harden_manifest(dec) is False   # dobara kuch nahi badlega


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
