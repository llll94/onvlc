"""As TVs com Android ligadas a este PC: achar na rede, parear como controle remoto e guardar (tvs.json).

Cada TV salva tem:
  id, name, maker, model  — "Sala", "Xiaomi", "MiTV-AFMU0"
  ip                      — onde ela estava da última vez (se mudar, `relocate` acha de novo)
  mac                     — do Wi-Fi/cabo, para acordar pela rede (Wake-on-LAN)
  bt                      — o MAC do Bluetooth que ela anuncia na rede; não muda, serve para reconhecer a TV
  display (opcional)      — a tela ligada nela por HDMI, quando dá para saber pela rede se está ligada:
                            {"name": "LG C1", "ip": ..., "mac": ..., "port": 3000}

O controle remoto é o mesmo protocolo do app "Google TV" do celular (porta 6466): pareia uma vez com um código que
a TV mostra; depois o PC aperta botões, abre links e sabe qual app está na frente.
"""
import asyncio
import json
import logging
import os
import re
import socket
import subprocess
import threading
import time
from pathlib import Path

DATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "onVLC"
FILE = DATA / "tvs.json"
CERT = DATA / "remote" / "cert.pem"
KEY = DATA / "remote" / "key.pem"
NO_WINDOW = 0x08000000
log = logging.getLogger("onvlc.tv")
_lock = threading.Lock()


# ---------- TVs salvas ----------
def _read():
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
        if isinstance(data.get("tvs"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"default": "", "tvs": []}


def _write(data):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, FILE)


def load():
    return _read()["tvs"]


def default():
    """A TV usada pelo "Assistir na TV" (a última pareada), ou None se nenhuma foi configurada."""
    data = _read()
    tvs = data["tvs"]
    return next((t for t in tvs if t.get("id") == data.get("default")), tvs[0] if tvs else None)


def save(tv, make_default=True):
    """Guarda (ou atualiza) uma TV. O id é o MAC do Bluetooth, que não muda; sem ele, o MAC da rede ou o IP."""
    tv = {k: v for k, v in tv.items() if k != "adb"}  # "adb" (depuração ligada) muda: não se guarda
    tv["id"] = tv.get("id") or tv.get("bt") or tv.get("mac") or tv.get("ip")
    with _lock:
        data = _read()
        data["tvs"] = [t for t in data["tvs"] if t.get("id") != tv["id"]] + [tv]
        if make_default or not data.get("default"):
            data["default"] = tv["id"]
        _write(data)
    return tv


def remove(tv_id):
    with _lock:
        data = _read()
        data["tvs"] = [t for t in data["tvs"] if t.get("id") != tv_id]
        if data.get("default") == tv_id:
            data["default"] = data["tvs"][0]["id"] if data["tvs"] else ""
        _write(data)


# ---------- achar na rede ----------
SERVICES = ("_androidtvremote2._tcp.local.", "_googlecast._tcp.local.", "_adb._tcp.local.")


def discover(seconds=4.0):
    """TVs com Android na rede, pelos anúncios que elas mesmas fazem (mDNS). Só entram as que aceitam o controle
    remoto (Android TV / Google TV); caixas de som e Chromecasts antigos ficam de fora.
    Devolve [{"ip", "name", "model", "bt", "mac", "adb"}]; "adb" diz se a depuração já está ligada."""
    from zeroconf import ServiceBrowser, ServiceListener, Zeroconf

    seen = []

    class Listener(ServiceListener):
        def add_service(self, zc, kind, name):
            seen.append((kind, name))  # resolvido depois: não pode travar a linha do zeroconf aqui

        update_service = add_service

        def remove_service(self, zc, kind, name):
            pass

    zc = Zeroconf()
    try:
        browsers = [ServiceBrowser(zc, kind, Listener()) for kind in SERVICES]
        time.sleep(seconds)
        found = {}
        for kind, name in dict.fromkeys(seen):
            info = zc.get_service_info(kind, name, 1500)
            ips = [a for a in (info.parsed_addresses() if info else []) if a.count(".") == 3]
            if not ips:
                continue
            props = {k.decode(errors="replace"): (v or b"").decode(errors="replace") for k, v in info.properties.items()}
            d = found.setdefault(ips[0], {"ip": ips[0], "name": "", "model": "", "bt": "", "remote": False, "adb": False})
            if kind.startswith("_androidtvremote2"):
                d["remote"] = True
                d["bt"] = props.get("bt", "").upper()
                d["name"] = d["name"] or name.split("._")[0]
            elif kind.startswith("_googlecast"):
                d["name"] = props.get("fn") or d["name"]  # o nome que a pessoa deu à TV
                d["model"] = props.get("md", "")
            else:
                d["adb"] = True
        for b in browsers:
            b.cancel()
    finally:
        zc.close()
    tvs = [d for d in found.values() if d.pop("remote")]
    for d in tvs:
        d["mac"] = arp_mac(d["ip"])
    return sorted(tvs, key=lambda d: d["name"].lower())


def arp_mac(ip):
    """MAC da rede de um IP (para o Wake-on-LAN), pela tabela ARP do Windows."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(b"", (ip, 9))  # garante que o IP está na tabela
        time.sleep(0.3)
        out = subprocess.run(["arp", "-a", ip], capture_output=True, text=True, timeout=5,
                             creationflags=NO_WINDOW, errors="replace").stdout
        m = re.search(r"(?:[0-9a-f]{2}-){5}[0-9a-f]{2}", out, re.I)
        return m.group(0).upper() if m else ""
    except Exception:
        return ""


def relocate(tv, seconds=4.0):
    """Se o IP da TV mudou (o roteador deu outro), acha de novo pelo MAC do Bluetooth ou pelo nome e salva."""
    for d in discover(seconds):
        same = (tv.get("bt") and d["bt"] == tv["bt"].upper()) or (not tv.get("bt") and d["name"] == tv.get("name"))
        if same:
            if d["ip"] != tv.get("ip"):
                log.info("TV %s mudou de IP: %s → %s", tv.get("name"), tv.get("ip"), d["ip"])
                tv.update(ip=d["ip"], mac=d["mac"] or tv.get("mac", ""))
                save(tv, make_default=False)
            return d["ip"]
    return None


# ---------- controle remoto ----------
class NeedsPairing(Exception):
    """A TV ainda não conhece este PC: precisa do código."""


class Remote:
    """Controle remoto de uma TV. Tudo roda numa linha própria (asyncio); os métodos esperam a resposta."""

    _loop = None

    def __init__(self, ip):
        self.ip = ip
        self._r = None

    @classmethod
    def _run(cls, coro, timeout=20):
        if cls._loop is None:
            cls._loop = asyncio.new_event_loop()
            threading.Thread(target=cls._loop.run_forever, daemon=True, name="tv-remote").start()
        return asyncio.run_coroutine_threadsafe(coro, cls._loop).result(timeout)

    async def _remote(self):
        if self._r is None:
            from androidtvremote2 import AndroidTVRemote
            CERT.parent.mkdir(parents=True, exist_ok=True)
            self._r = AndroidTVRemote(f"onVLC no {socket.gethostname()}", str(CERT), str(KEY), self.ip)
            await self._r.async_generate_cert_if_missing()
        return self._r

    def connect(self):
        """Conecta. Levanta NeedsPairing se a TV ainda não conhece este PC."""
        from androidtvremote2 import InvalidAuth

        async def go():
            r = await self._remote()
            try:
                await r.async_connect()
            except InvalidAuth:
                raise NeedsPairing() from None
        self._run(go())

    def start_pairing(self):
        """A TV mostra um código de 6 letras e números."""
        async def go():
            await (await self._remote()).async_start_pairing()
        self._run(go())

    def finish_pairing(self, code):
        """Confere o código digitado e já deixa conectado. Código errado levanta InvalidAuth."""
        async def go():
            r = await self._remote()
            await r.async_finish_pairing(code.strip().upper())
            await r.async_connect()
        self._run(go(), timeout=30)

    def info(self):
        """{"maker", "model", "app", "on"}: fabricante, modelo, app na frente e se a tela está ligada."""
        r = self._r
        di = (r.device_info or {}) if r else {}
        return {"maker": di.get("manufacturer", ""), "model": di.get("model", ""),
                "app": r.current_app if r else "", "on": bool(r and r.is_on)}

    def key(self, name):
        """Aperta um botão: "HOME", "BACK", "DPAD_CENTER", "DPAD_DOWN", "SETTINGS", "POWER"..."""
        async def go():
            (await self._remote()).send_key_command(name)
        self._run(go())

    def open_link(self, url):
        """Abre um link na TV: "market://details?id=..." (Play Store) ou um link de app."""
        async def go():
            (await self._remote()).send_launch_app_command(url)
        self._run(go())

    def close(self):
        if self._r is not None:
            r, self._r = self._r, None
            Remote._loop.call_soon_threadsafe(r.disconnect)
