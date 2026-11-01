"""Gera uma versão e cria a release no GitHub (rascunho), com o instalador, o app da TV e o SHA256SUMS.txt.

Uso: python tools\\release.py              → gera tudo e cria a release como RASCUNHO (só você vê)
     python tools\\release.py --publicar   → idem, já publicada
     python tools\\release.py --testar     → gera tudo e o SHA256SUMS, sem falar com o GitHub

Antes de criar, confere: nada pendente no git, nenhuma chave no repositório, o APK assinado com a chave nova.
O acesso ao GitHub usa o login que o git já guarda no Gerenciador de Credenciais (git credential fill); nada de token
em arquivo. A versão é 1.0.<número de commits> (a mesma do instalador).
"""
import hashlib
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "llll94/onvlc"
APK_CERT_SHA256 = "5a4de354aed556f11f200ce518e66b87fb4f86918ef2c47cc0ec6d48d074e0e4"  # CN=onVLC (chave de 01/11/2026)


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def preflight():
    if git("status", "--porcelain"):
        sys.exit("há mudanças sem commit: faça o commit antes de lançar")
    tracked = git("ls-files")
    bad = [f for f in tracked.splitlines() if f.lower().endswith((".keystore", ".jks", ".pem", ".p12", ".pfx"))]
    if bad:
        sys.exit(f"chave ou certificado no repositório: {bad}")


def apk_cert(apk):
    sdk = Path(os.environ.get("ANDROID_HOME") or Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk")
    bt = sorted((sdk / "build-tools").iterdir())[-1]
    jdk = next(Path(r"C:\Program Files\Microsoft").glob("jdk-17*"), None)
    env = dict(os.environ, JAVA_HOME=str(jdk)) if jdk else os.environ
    out = subprocess.run([str(bt / "apksigner.bat"), "verify", "--print-certs", str(apk)], capture_output=True,
                         text=True, env=env).stdout
    for line in out.splitlines():
        if "SHA-256 digest" in line:
            return line.split(":")[-1].strip()
    return ""


def github_token():
    """O login do GitHub que o git já usa (Gerenciador de Credenciais)."""
    r = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n", capture_output=True,
                       text=True, cwd=ROOT)
    for line in r.stdout.splitlines():
        if line.startswith("password="):
            return line[len("password="):]
    sys.exit("sem login do GitHub no git (faça um git push uma vez para o Windows guardar)")


def api(method, url, token, data=None, ctype="application/json"):
    body = json.dumps(data).encode() if isinstance(data, dict) else data
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "Content-Type": ctype,
        "X-GitHub-Api-Version": "2022-11-28"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)


def main():
    test = "--testar" in sys.argv
    if not test:
        preflight()
    subprocess.run([sys.executable, str(ROOT / "tools" / "build_installer.py")], check=True)
    version = "1.0." + git("rev-list", "--count", "HEAD")
    dist = ROOT / "dist"
    assets = {f"instalar-onVLC-{version}.exe": dist / "instalar-onVLC.exe",
              f"onVLC-tv-{version}.apk": dist / "tvcache.apk"}
    cert = apk_cert(dist / "tvcache.apk")
    if cert != APK_CERT_SHA256:
        sys.exit(f"o APK não está assinado com a chave do onVLC (encontrado: {cert or 'nada'})")
    sums = "".join(f"{sha256(p)}  {name}\n" for name, p in assets.items())
    (dist / "SHA256SUMS.txt").write_text(sums, encoding="utf-8")
    print(sums)
    if test:
        print("teste: nada foi enviado ao GitHub")
        return
    token = github_token()
    notes = (f"onVLC {version}\n\n"
             "**Instalar:** baixe `instalar-onVLC-" + version + ".exe` e abra. O Windows pode mostrar \"O Windows protegeu "
             "seu computador\" (o instalador não tem assinatura paga): clique em **Mais informações → Executar assim mesmo**.\n\n"
             "**Conferir o download (opcional):** no PowerShell, `Get-FileHash .\\instalar-onVLC-" + version + ".exe` deve dar o "
             "mesmo valor do `SHA256SUMS.txt`.\n\n"
             f"O app da TV é instalado pelo próprio assistente do PC. Certificado do APK (SHA-256): `{APK_CERT_SHA256}`.\n")
    tag = "v" + version
    rel = api("POST", f"https://api.github.com/repos/{REPO}/releases", token, {
        "tag_name": tag, "target_commitish": git("rev-parse", "HEAD"), "name": f"onVLC {version}", "body": notes,
        "draft": "--publicar" not in sys.argv, "prerelease": False})
    upload = rel["upload_url"].split("{")[0]
    for name, p in list(assets.items()) + [("SHA256SUMS.txt", dist / "SHA256SUMS.txt")]:
        api("POST", f"{upload}?name={name}", token, p.read_bytes(), "application/octet-stream")
        print("enviado:", name)
    print("release:", rel["html_url"], "(rascunho)" if rel.get("draft") else "")


if __name__ == "__main__":
    main()
