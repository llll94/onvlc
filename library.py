"""Lado do player do PC da biblioteca da TV.

A biblioteca em si é o onvlc-biblioteca.exe (pasta server\\, em Go): um processo separado, sem janela, com ícone
perto do relógio, que lê a pasta "Vídeos\\onVLC" e serve a TV e o player na porta 8780. O onVLC abre esse processo
quando inicia; fechar a janela do player não fecha a biblioteca.
"""
import json
import logging
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
import os

LIB_DIR = Path(os.environ.get("ONVLC_LIBRARY") or Path.home() / "Videos" / "onVLC")
PORT = 8780
# o segredo que a biblioteca cria na primeira execução; todo pedido a ela (menos /api/hello) leva esse valor no
# cabeçalho X-onVLC-Key. A TV recebe o mesmo valor pelo assistente.
SECRET_F = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "onVLC" / "library" / "secret"
HOST = f"http://127.0.0.1:{PORT}"


PAUSE_F = SECRET_F.with_name("desligada")  # a pessoa desligou a biblioteca (ver server\pause.go)


def _boot_time():
    import ctypes
    import time
    ctypes.windll.kernel32.GetTickCount64.restype = ctypes.c_uint64
    return int(time.time() - ctypes.windll.kernel32.GetTickCount64() / 1000)


def enabled():
    """A biblioteca está ligada? Desligada = arquivo "desligada" com "sempre", ou "boot:<hora>" deste mesmo boot."""
    try:
        s = PAUSE_F.read_text(encoding="utf-8").strip()
    except OSError:
        return True
    if s == "sempre":
        return False
    if s.startswith("boot:") and s[5:].isdigit() and abs(int(s[5:]) - _boot_time()) < 120:
        return False
    return True


def set_enabled(on):
    """Liga (apaga o arquivo e abre a biblioteca) ou desliga até a pessoa ligar de novo (e fecha a que está aberta)."""
    if on:
        PAUSE_F.unlink(missing_ok=True)
        start_detached()
        return
    PAUSE_F.parent.mkdir(parents=True, exist_ok=True)
    PAUSE_F.write_text("sempre" + chr(10), encoding="utf-8")
    taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
    subprocess.run([str(taskkill), "/IM", "onvlc-biblioteca.exe", "/F"], capture_output=True, creationflags=0x08000000)


def key():
    try:
        return SECRET_F.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def request(path, data=None, timeout=5):
    """Pedido à biblioteca, já com a chave. `data` (dict) vira POST em JSON. Devolve a resposta aberta."""
    headers = {"X-onVLC-Key": key()}
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    return urllib.request.urlopen(urllib.request.Request(HOST + path, data=body, headers=headers), timeout=timeout)


def get_json(path, timeout=5):
    with request(path, timeout=timeout) as r:
        return json.load(r)
ROOT = Path(__file__).resolve().parent
log = logging.getLogger("onvlc.lib")


def is_sample(p):
    """Amostra que vem junto no download (pasta Sample ou "sample" no nome). Mesma regra da biblioteca."""
    p = Path(p)
    return p.parent.name.lower() == "sample" or re.search(r"(^|[\W_])sample([\W_]|$)", p.stem, re.I) is not None


def running():
    """Já existe uma biblioteca respondendo neste PC?"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/hello", timeout=1) as r:
            return json.load(r).get("app") == "onvlc-library"
    except Exception:
        return False


def exe():
    """O onvlc-biblioteca.exe: ao lado do onVLC.exe (pacote) ou em server\\ (desenvolvimento)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).with_name("onvlc-biblioteca.exe")
    return ROOT / "server" / "onvlc-biblioteca.exe"


def start_detached():
    """Abre a biblioteca como processo separado (sobrevive ao fechar o onVLC). Desligada pela pessoa: não abre."""
    if running() or not enabled():
        return
    path = exe()
    if not path.exists():
        log.warning("biblioteca não encontrada em %s", path)
        return
    # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
    flags = 0x00000008 | 0x00000200 | 0x01000000
    try:
        subprocess.Popen([str(path)], creationflags=flags, close_fds=True, cwd=str(path.parent))
    except OSError:
        subprocess.Popen([str(path)], creationflags=0x00000008 | 0x00000200, close_fds=True, cwd=str(path.parent))
