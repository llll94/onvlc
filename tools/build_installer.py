"""Gera dist\\instalar-onVLC.exe (Inno Setup, grátis) a partir de dist\\onVLC.

Primeiro gera o app da TV e o pacote do PC sem mexer nos atalhos e na tarefa deste PC, depois compila o instalador.

Uso: python tools\\build_installer.py          (tudo)
     python tools\\build_installer.py --so-iss  (só o instalador, com o dist\\onVLC que já existe)
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def iscc():
    for p in (Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Inno Setup 6" / "ISCC.exe",
              Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe")):
        if p.is_file():
            return p
    found = shutil.which("ISCC")
    if not found:
        sys.exit("Inno Setup não encontrado: winget install JRSoftware.InnoSetup --scope user")
    return Path(found)


def main():
    if "--so-iss" not in sys.argv:
        subprocess.run([sys.executable, str(ROOT / "tools" / "build_tvcache.py"), "--no-install"], check=True)
        subprocess.run([sys.executable, str(ROOT / "tools" / "build.py"), "--sem-registrar"], check=True)
    src = ROOT / "dist" / "onVLC"
    if not (src / "onVLC.exe").is_file():
        sys.exit("falta dist\\onVLC (rode tools\\build.py)")
    commits = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    version = f"1.0.{commits or 0}"
    subprocess.run([str(iscc()), f"/DAppVersion={version}", f"/DSrc={src}", f"/DRoot={ROOT}", "/Q",
                    str(ROOT / "tools" / "onVLC.iss")], check=True)
    out = ROOT / "dist" / "instalar-onVLC.exe"
    print(f"pronto: {out} ({out.stat().st_size / 2**20:.0f} MB, versão {version})")


if __name__ == "__main__":
    main()
