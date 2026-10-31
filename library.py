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
    """Abre a biblioteca como processo separado (sobrevive ao fechar o onVLC)."""
    if running():
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
