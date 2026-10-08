#!/usr/bin/env python3
"""
gen_svg.py — ZYROX ENCRYPTION ke README assets banata hai:
  * typing.svg  — terminal-style animated "running text" (typing + blinking cursor)
  * banner.svg  — scrolling marquee banner
  * logo.svg    — vector logo (shield + Z + padlock)

Chalayein:   python3 assets/gen_svg.py
(SVG SMIL animations GitHub README me chalti hain — <img src="..."> se.)
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent

MONO = "ui-monospace, 'JetBrains Mono', 'DejaVu Sans Mono', Menlo, Consolas, monospace"
AMBER = "#ff7a00"
GOLD = "#ffb300"
BG = "#0d1117"
PANEL = "#161b22"
BORDER = "#30363d"
DIM = "#8b949e"
TXT = "#e6edf3"

PHRASES = [
    ("→ decoding dex … classes=1,247", "#7ee787"),
    ("→ encrypting strings … 12,404 done ✅", GOLD),
    ("→ hiding keys + stripping debug info …", "#a5d6ff"),
    ("→ signing v1+v2+v3 … verified ✅", "#7ee787"),
    ("✔ saved: /sdcard/Download/zyrox/myapp-protected.apk", AMBER),
]

CHIPS = ["MIT", "Termux CLI", "apktool + apksigner", "v1+v2+v3 signing"]


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def typing_svg() -> str:
    W, H = 900, 264
    x0, fs = 44, 16
    cw = fs * 0.6                    # monospace char width
    slot, n = 2.4, len(PHRASES)
    total = slot * n
    parts = []

    for i, (txt, color) in enumerate(PHRASES):
        tw = round(len(txt) * cw, 1)
        ts, te = i * slot / total, (i + 1) * slot / total
        kt = f"0;{ts:.4f};{ts + 0.5 / total:.4f};{te:.4f};1"
        cid = f"clip{i}"
        parts.append(f'''
  <clipPath id="{cid}"><rect x="{x0}" y="{150 - 13}" height="22" width="0">
    <animate attributeName="width" dur="{total}s" repeatCount="indefinite"
             keyTimes="{kt}" values="0;0;{tw};{tw};{tw}"/>
  </rect></clipPath>
  <g opacity="0">
    <animate attributeName="opacity" dur="{total}s" repeatCount="indefinite"
             keyTimes="0;{ts:.4f};{ts + 0.01 / total:.4f};{te - 0.01 / total:.4f};{te:.4f};1"
             values="0;0;1;1;0;0"/>
    <text x="{x0}" y="150" clip-path="url(#{cid})" fill="{color}" font-size="{fs}"
          textLength="{tw}" lengthAdjust="spacingAndGlyphs">{esc(txt)}</text>
    <rect x="{x0}" y="137" width="9" height="19" fill="{AMBER}">
      <animate attributeName="x" dur="{total}s" repeatCount="indefinite"
               keyTimes="{kt}" values="{x0};{x0};{x0 + tw};{x0 + tw};{x0 + tw}"/>
      <animate attributeName="opacity" values="1;0;1" dur="1s" repeatCount="indefinite"/>
    </rect>
  </g>''')

    # static shell prompt line
    prompt = (f'''  <text x="{x0}" y="110" font-size="{fs}" fill="#7ee787">$</text>
  <text x="{x0 + 2 * cw}" y="110" font-size="{fs}" fill="{TXT}"
        textLength="{round(28 * cw, 1)}" lengthAdjust="spacingAndGlyphs">bash zyrox.sh /sdcard/myapp.apk</text>''')

    # footer metrics (dim, static)
    footer = (f'''  <text x="{x0}" y="196" font-size="12.5" fill="{DIM}">strings_encrypted=12404
  debug_stripped=true   keys=hidden (xor-split)   sign=v1+v2+v3 ✔   leak=none</text>''')

    # chips row
    chips, cx = [], x0
    for label in CHIPS:
        w = round(len(label) * 6.9 + 22, 1)
        chips.append(f'''  <g>
    <rect x="{cx}" y="212" width="{w}" height="26" rx="13" fill="#1f6feb22" stroke="#1f6feb66"/>
    <text x="{cx + w / 2}" y="229.5" font-size="12" fill="#a5d6ff" text-anchor="middle">{esc(label)}</text>
  </g>''')
        cx += w + 10

    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img">
<title>ZYROX ENCRYPTION — termux demo</title>
<defs>
  <linearGradient id="win" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#0d1117"/><stop offset="1" stop-color="#0b0f14"/>
  </linearGradient>
  <linearGradient id="accent" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="{AMBER}"/><stop offset="1" stop-color="{GOLD}"/>
  </linearGradient>
  <filter id="glow" x="-30%" y="-30%" width="160%" height="160%">
    <feGaussianBlur stdDeviation="4" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
</defs>

<rect width="{W}" height="{H}" rx="16" fill="url(#win)" stroke="{BORDER}"/>
<path d="M16,30 h868 a14,14 0 0 1 14,14 v20 h-896 v-20 a14,14 0 0 1 14,-14 z" fill="{PANEL}"/>
<line x1="16" y1="64" x2="884" y2="64" stroke="{BORDER}"/>
<circle cx="44" cy="47" r="7" fill="#ff5f56"/><circle cx="66" cy="47" r="7" fill="#ffbd2e"/>
<circle cx="88" cy="47" r="7" fill="#27c93f"/>
<text x="450" y="52" font-size="13" fill="{DIM}" text-anchor="middle">zyrox@termux: ~/zyrox-encryption</text>

<g font-family="{MONO}">
{prompt}
{footer}
{''.join(chips)}
</g>
<g font-family="{MONO}">{''.join(parts)}</g>
<rect x="16" y="16" width="868" height="232" rx="14" fill="none" stroke="url(#accent)" stroke-opacity="0.35" filter="url(#glow)"/>
</svg>
'''


def banner_svg() -> str:
    W, H = 1280, 72
    text = "ZYROX ENCRYPTION   ✦   APK STRING ENCRYPTION   ✦   TERMUX CLI   ✦   v1+v2+v3 SIGNING   ✦   DEBUG-STRIP   ✦   ZERO PLAINTEXT LEAK   ✦   "
    fs, ls = 22, 4.5
    tw = round(len(text) * (fs * 0.6 + ls), 1)      # approx width of one copy
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img">
<title>ZYROX ENCRYPTION banner</title>
<defs>
  <linearGradient id="bg" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="#0b0f14"/><stop offset="0.5" stop-color="#161b22"/><stop offset="1" stop-color="#0b0f14"/>
  </linearGradient>
  <linearGradient id="txt" x1="0" y1="0" x2="1" y2="0">
    <stop offset="0" stop-color="{AMBER}"/><stop offset="0.5" stop-color="#ffd479"/><stop offset="1" stop-color="{AMBER}"/>
  </linearGradient>
  <clipPath id="strip"><rect width="{W}" height="{H}" rx="12"/></clipPath>
</defs>
<rect width="{W}" height="{H}" rx="12" fill="url(#bg)" stroke="{BORDER}"/>
<g clip-path="url(#strip)">
  <g font-family="{MONO}" font-size="{fs}" letter-spacing="{ls}" fill="url(#txt)">
    <animateTransform attributeName="transform" type="translate" from="0 0" to="{-tw} 0"
                      dur="26s" repeatCount="indefinite"/>
    <text x="24" y="46" textLength="{tw}" lengthAdjust="spacingAndGlyphs">{esc(text)}</text>
    <text x="{24 + tw}" y="46" textLength="{tw}" lengthAdjust="spacingAndGlyphs">{esc(text)}</text>
  </g>
</g>
</svg>
'''


def logo_svg() -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 220" width="200" height="220" role="img">
<title>ZYROX ENCRYPTION logo</title>
<defs>
  <linearGradient id="shield" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="{AMBER}"/><stop offset="1" stop-color="{GOLD}"/>
  </linearGradient>
  <linearGradient id="inner" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="#141a21"/><stop offset="1" stop-color="#0b0f14"/>
  </linearGradient>
  <filter id="soft" x="-40%" y="-40%" width="180%" height="180%">
    <feGaussianBlur stdDeviation="6" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
</defs>

<path d="M100 12 L172 42 V104 C172 152 140 186 100 206 C60 186 28 152 28 104 V42 Z"
      fill="url(#inner)" stroke="url(#shield)" stroke-width="5" filter="url(#soft)"/>
<path d="M100 26 L160 50 V104 C160 144 134 173 100 191 C66 173 40 144 40 104 V50 Z"
      fill="none" stroke="{AMBER}" stroke-opacity="0.35" stroke-width="1.5"/>

<!-- Z -->
<path d="M62 62 H138 V78 L96 112 H138 V130 H62 V114 L104 80 H62 Z" fill="url(#shield)"/>
<!-- circuit dots -->
<circle cx="150" cy="86" r="3.4" fill="{GOLD}"/><circle cx="150" cy="112" r="3.4" fill="{GOLD}"/>
<circle cx="50" cy="86" r="3.4" fill="{GOLD}"/><circle cx="50" cy="112" r="3.4" fill="{GOLD}"/>

<!-- padlock -->
<path d="M89 158 v-8 a11 11 0 0 1 22 0 v8" fill="none" stroke="{GOLD}" stroke-width="3.6" stroke-linecap="round"/>
<rect x="82" y="157" width="36" height="27" rx="6" fill="none" stroke="url(#shield)" stroke-width="3.6"/>
<circle cx="100" cy="169" r="3.6" fill="{GOLD}"/><path d="M100 171.5 v6" stroke="{GOLD}" stroke-width="3.4" stroke-linecap="round"/>
</svg>
'''


def main() -> None:
    for name, content in (("typing.svg", typing_svg()), ("banner.svg", banner_svg()),
                          ("logo.svg", logo_svg())):
        path = OUT / name
        path.write_text(content)
        print(f"  wrote {path.relative_to(OUT.parent)}  ({len(content)} bytes)")

    # XML well-formedness check
    import xml.etree.ElementTree as ET
    for name in ("typing.svg", "banner.svg", "logo.svg"):
        ET.parse(OUT / name)
    print("  ✅ teeno SVG valid XML hain")


if __name__ == "__main__":
    main()
