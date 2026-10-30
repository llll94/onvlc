"""Gera ui/icon.ico: quadrado escuro arredondado com um play laranja."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent.parent / "ui" / "icon.ico"
S = 1024  # desenha grande e reduz, para as bordas ficarem lisas


def build():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # fundo: degradê vertical escuro dentro de um quadrado arredondado
    bg = Image.new("RGBA", (S, S))
    top, bottom = (42, 44, 51), (14, 15, 19)
    for y in range(S):
        t = y / (S - 1)
        bg.paste(tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,), (0, y, S, y + 1))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((40, 40, S - 40, S - 40), radius=220, fill=255)
    img.paste(bg, (0, 0), mask)

    # brilho laranja suave atrás do play
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse((250, 250, S - 250, S - 250), fill=(255, 138, 31, 90))
    glow = glow.filter(ImageFilter.GaussianBlur(90))
    glow_masked = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    glow_masked.paste(glow, (0, 0), mask)
    img = Image.alpha_composite(img, glow_masked)

    # play laranja com cantos arredondados (triângulo + traço grosso arredondado)
    play = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(play)
    pts = [(395, 300), (395, 724), (745, 512)]
    d.polygon(pts, fill=(255, 138, 31, 255))
    d.line(pts + [pts[0]], fill=(255, 138, 31, 255), width=70, joint="curve")
    for x, y in pts:
        d.ellipse((x - 35, y - 35, x + 35, y + 35), fill=(255, 138, 31, 255))
    img = Image.alpha_composite(img, play)

    sizes = [256, 128, 64, 48, 32, 24, 16]
    img.resize((256, 256), Image.LANCZOS).save(OUT, sizes=[(n, n) for n in sizes])
    img.resize((512, 512), Image.LANCZOS).save(OUT.with_suffix(".png"))
    print("ícone salvo em", OUT)


if __name__ == "__main__":
    build()
