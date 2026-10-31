"""Gera o ícone do onVLC ("cone no ar"): círculo laranja, cone do VLC em branco e duas ondas saindo da ponta.

O cone transmitindo é o PC mandando o filme para a TV. Escolhido em 2026-10-30.
Saídas: ui/icon.ico e ui/icon.png (PC), android/res/drawable-xhdpi/icon.png e banner.png (tela inicial da Mi Box)
e tvui/icon.svg (marca na barra da TV). Uso: python tools\\make_icon.py
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ORANGE, WHITE = (255, 138, 31, 255), (255, 255, 255, 255)
S = 2048  # desenha grande e reduz, para as bordas ficarem lisas
K = S / 512  # o desenho é pensado numa grade de 512 (a mesma do SVG)

# geometria na grade de 512
CX, TIP, BASE, HALF = 256, 214, 372, 62
WAVES = [(66, 222, 318), (118, 226, 314)]  # raio, ângulo inicial e final (graus, sentido horário a partir das 3 h)
WAVE_W = 30


def k(*v):
    return [x * K for x in v]


def draw_icon():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, S - 1, S - 1), fill=ORANGE)

    # ondas: arco grosso com pontas redondas
    w = WAVE_W * K
    for r, a0, a1 in WAVES:
        cx, cy, rr = CX * K, TIP * K, r * K
        d.arc((cx - rr, cy - rr, cx + rr, cy + rr), a0, a1, fill=WHITE, width=round(w))
        for a in (a0, a1):
            # o arco do PIL é desenhado para dentro do raio: a ponta fica no meio da espessura
            m = rr - w / 2
            x, y = cx + m * math.cos(math.radians(a)), cy + m * math.sin(math.radians(a))
            d.ellipse((x - w / 2, y - w / 2, x + w / 2, y + w / 2), fill=WHITE)

    # cone: corpo com a ponta arredondada
    tip, base, half, cx = TIP * K, BASE * K, HALF * K, CX * K
    body = [(cx, tip - 4 * K), (cx + half, base), (cx - half, base)]
    d.polygon(body, fill=WHITE)
    d.line(body + [body[0]], fill=WHITE, width=round(10 * K), joint="curve")
    d.ellipse((cx - 9 * K, tip - 12 * K, cx + 9 * K, tip + 6 * K), fill=WHITE)

    # duas faixas laranja
    h = base - tip
    for t0, t1 in ((0.36, 0.5), (0.66, 0.8)):
        y0, y1 = tip + h * t0, tip + h * t1
        d.polygon([(cx - half * t0, y0), (cx + half * t0, y0), (cx + half * t1, y1), (cx - half * t1, y1)], fill=ORANGE)

    # base do cone
    d.rounded_rectangle((cx - half - 20 * K, base - 4 * K, cx + half + 20 * K, base + 22 * K), radius=13 * K, fill=WHITE)
    return img


def svg():
    def arc(r, a0, a1):
        p = lambda a: (CX + r * math.cos(math.radians(a)), TIP + r * math.sin(math.radians(a)))
        (x0, y0), (x1, y1) = p(a0), p(a1)
        return f'M{x0:.1f} {y0:.1f}A{r} {r} 0 0 1 {x1:.1f} {y1:.1f}'
    h = BASE - TIP
    bands = "".join(
        f'<path d="M{CX - HALF * a:.1f} {TIP + h * a:.1f}H{CX + HALF * a:.1f}L{CX + HALF * b:.1f} {TIP + h * b:.1f}H{CX - HALF * b:.1f}Z" fill="#ff8a1f"/>'
        for a, b in ((0.36, 0.5), (0.66, 0.8)))
    waves = "".join(f'<path d="{arc(r, a0, a1)}" fill="none" stroke="#fff" stroke-width="{WAVE_W}" stroke-linecap="round"/>'
                    for r, a0, a1 in WAVES)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512"><circle cx="256" cy="256" r="256" fill="#ff8a1f"/>{waves}'
            f'<path d="M{CX - 10} {TIP + 10}Q{CX} {TIP - 8} {CX + 10} {TIP + 10}L{CX + HALF} {BASE}H{CX - HALF}Z" fill="#fff" '
            f'stroke="#fff" stroke-width="10" stroke-linejoin="round"/>{bands}'
            f'<rect x="{CX - HALF - 20}" y="{BASE - 4}" width="{2 * HALF + 40}" height="26" rx="13" fill="#fff"/></svg>\n')


def banner(icon):
    """Banner da tela inicial da Android TV: 320×180 dp (640×360 em xhdpi)."""
    W, H = 640, 360
    img = Image.new("RGB", (W, H), (20, 20, 22))
    d = ImageDraw.Draw(img)
    for x in range(W):  # brilho laranja suave à esquerda
        a = max(0.0, 1 - x / (W * 0.8))
        d.line([(x, 0), (x, H)], fill=(int(20 + 40 * a), int(20 + 18 * a), int(22 + 4 * a)))
    ic = icon.resize((168, 168), Image.LANCZOS)
    img.paste(ic, (62, (H - 168) // 2), ic)
    font = ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", 108)
    d.text((256, H // 2), "onVLC", font=font, fill="white", anchor="lm")
    return img


def build():
    icon = draw_icon()
    ui = ROOT / "ui"
    icon.resize((256, 256), Image.LANCZOS).save(ui / "icon.ico", sizes=[(n, n) for n in (256, 128, 64, 48, 32, 24, 16)])
    icon.resize((512, 512), Image.LANCZOS).save(ui / "icon.png")
    res = ROOT / "android" / "res" / "drawable-xhdpi"
    res.mkdir(parents=True, exist_ok=True)
    icon.resize((192, 192), Image.LANCZOS).save(res / "icon.png")
    banner(icon).save(res / "banner.png")
    (ROOT / "tvui" / "icon.svg").write_text(svg(), encoding="utf-8")
    print("ícones salvos: ui/icon.ico, ui/icon.png, android/res/drawable-xhdpi/{icon,banner}.png, tvui/icon.svg")


if __name__ == "__main__":
    build()
