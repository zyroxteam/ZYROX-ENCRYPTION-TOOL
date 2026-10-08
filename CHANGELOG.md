# Changelog

Sab notable changes yahan likhe jaate hain. Format: [Keep a Changelog](https://keepachangelog.com/),
versioning: [Semantic Versioning](https://semver.org/).

## [1.1.0] — 2026-10-08

### Added
- **`zyrox --self-test`** — ek command me install verify: Java + apktool + apksigner + crypto round-trip
  (33 cases) + zip writer/alignment. APK ki zaroorat nahi.
- **Test suite** (`tests/test_zyrox.py`) — 15 tests, koi external dependency nahi:
  crypto round-trip (ASCII/Hindi/Chinese/Arabic/emoji/NUL/500-char), UTF-16 unit semantics,
  keystream 16-bit behaviour, smali escape/unescape, key generation + XOR-split correctness,
  decryptor smali shape (hidden keys leak nahi karte), zip writer integrity/alignment/data-preservation,
  zip64 path (70k entries).
- **`CHANGELOG.md`** — ye file.
- CI me test suite + self-test + `--version`/`--help` smoke test add kiye.

### Changed
- Tests me comparison **UTF-16 unit level** pe (Java semantics) — emoji/astral chars Python me 1 char
  hote hain par Java me 2; unit-level hi correct tareeka hai.

## [1.0.0] — 2026-10-08

### Added
- **String encryption**: har `const-string` encrypt → runtime pe decryptor call (pure smali, koi library nahi).
- **Hidden keys**: har build pe random package/class/method naam + `seed`/`mult` XOR-split form me
  (smali me direct constants nahi dikhte). `--simple-keys` se purana direct mode.
- **Debug info strip**: `.line`, `.local`, `.param`, `.prologue`, `.epilogue`, `.restart local`.
- **Resources untouched**: sirf dex replace hota hai — `res/`, `assets/`, `AndroidManifest.xml`,
  `resources.arsc`, `lib/` byte-for-byte original.
- **Zipalign-style repack**: `resources.arsc` 4-byte + `.so` 4096-byte alignment, ZIP64 support.
- **Signing**: v1 + v2 + v3 (Google apksigner). Termux ka system `apksigner` ya bundled `apksigner.jar` — auto-detect.
- **`zyrox-verify.py`**: proof report — signature verify, zip/alignment, decrypt-test
  (protected → decrypt → original se compare), plaintext leak check.
- **Termux one-command setup**: `setup.sh` (python + openjdk + apktool + apksigner) + `install.sh`
  (`zyrox` command PATH me) + `bin/zyrox` launcher + `termux-wake-lock` + `/sdcard/Download/zyrox` copy.
- **Input APK ka certificate** dikhta hai (verify karo ki apni app hai).
- **README assets**: `logo.png`/`logo.svg`, animated `typing.svg` (typing effect + blinking cursor),
  `banner.svg` (scrolling marquee) — `assets/gen_svg.py` se generate hote hain.

### Verified (test results)
- 80/80 strings encrypt + decrypt exact match (real APK).
- Multi-dex (`classes.dex` + `classes2.dex`): 82/82 strings.
- `.so` 4096-aligned, `resources.arsc` 4-aligned.
- `apksigner verify`: v1 + v2 + v3 = true.
- Edge cases: quotes, backslash, newline/tab, Hindi/Chinese/Arabic, emoji (surrogate pair), NUL, 400-char.
- Custom keystore (`--keys`) ke saath signature DN match.

### Known limitations
- Sirf single `.apk` (`.aab` / split APKs / XAPK support nahi).
- Class/method renaming nahi karta (R8/ProGuard ka kaam) — isliye protection "string-level" hai,
  control-flow obfuscation nahi.
- Asli Android device pe runtime test maintainer ne nahi kiya (emulator/device nahi tha) —
  users ko publish se pehle test karna chahiye.
