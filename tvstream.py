"""Mi Box pelo adb (Assistir na TV) e o envio de vídeo por HTTP com Range, usado pela biblioteca.

"Assistir na TV" pede para o app onVLC da Mi Box (pasta android\\) tocar um vídeo da biblioteca num ponto.
O app toca com o próprio motor, passando pelo cache dele, e manda a posição para a biblioteca do PC.
"""
import logging
import os
import re
import shutil
import socket
import subprocess
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

BOX_IP = "192.168.1.31"
BOX_MAC = "AA-BB-CC-00-00-01"
TV_IP, TV_MAC = "192.168.1.13", "AA-BB-CC-00-00-03"  # LG C1; porta 3000 só responde com ela ligada
APP_PKG = "com.lucas.tvcache"
APP_ACTIVITY = APP_PKG + "/.LibraryActivity"
NO_WINDOW = 0x08000000
CHUNK = 1024 * 1024
log = logging.getLogger("onvlc.tv")


# ---------- envio de vídeo ----------
def send_file(h, path, body=True):
    """Responde um pedido HTTP com o arquivo, aceitando Range (o cache da TV pede pedaços)."""
    if not path or not os.path.isfile(path):
        h.send_error(404)
        return
    size = os.path.getsize(path)
    start, end = 0, size - 1
    m = re.match(r"bytes=(\d*)-(\d*)", h.headers.get("Range", ""))
    if m and (m.group(1) or m.group(2)):
        if m.group(1):
            start = int(m.group(1))
            end = min(size - 1, int(m.group(2))) if m.group(2) else size - 1
        else:  # bytes=-N: os últimos N bytes
            start = max(0, size - int(m.group(2)))
        if start >= size or end < start:
            h.send_response(416)
            h.send_header("Content-Range", f"bytes */{size}")
            h.end_headers()
            return
        h.send_response(206)
        h.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    else:
        h.send_response(200)
    h.send_header("Content-Type", "video/x-matroska" if path.lower().endswith(".mkv") else "video/mp4")
    h.send_header("Accept-Ranges", "bytes")
    h.send_header("Content-Length", str(end - start + 1))
    h.end_headers()
    if not body:
        return
    with open(path, "rb") as f:
        f.seek(start)
        left = end - start + 1
        try:
            while left > 0:
                data = f.read(min(CHUNK, left))
                if not data:
                    break
                h.wfile.write(data)
                left -= len(data)
        except (ConnectionError, OSError):
            pass  # o player fechou a conexão para pular para outro ponto: normal


class _Server(ThreadingHTTPServer):
    # no Windows, "reaproveitar endereço" deixa dois programas abrirem a mesma porta e os pedidos caírem no
    # errado; o modo exclusivo impede isso
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


# ---------- Mi Box ----------
def _q(s):
    """Aspas simples para o shell do Android."""
    return "'" + str(s).replace("'", "'\\''") + "'"


class MiBox:
    def __init__(self, ip=BOX_IP):
        self.ip = ip
        self._adb = None

    @property
    def adb(self):
        # procurado só quando a TV for usada: não atrasa a abertura do player
        if self._adb is None:
            self._adb = shutil.which("adb") or self._winget_adb() or ""
        return self._adb

    @staticmethod
    def _winget_adb():
        pkgs = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Packages"
        hits = list(pkgs.glob("Google.PlatformTools*/platform-tools/adb.exe"))
        return str(hits[0]) if hits else None

    @property
    def serial(self):
        return f"{self.ip}:5555"

    def run(self, *args, timeout=15):
        if not self.adb:
            raise RuntimeError("adb não encontrado")
        r = subprocess.run([self.adb, *args], capture_output=True, text=True, timeout=timeout,
                           creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
        if r.returncode != 0 or r.stderr.strip():
            log.warning("adb %s → %s %s", " ".join(args)[:120], r.returncode, r.stderr.strip()[:200])
        return r.stdout.strip()

    def shell(self, cmd, timeout=15):
        return self.run("-s", self.serial, "shell", cmd, timeout=timeout)

    def online(self):
        # um comando de verdade: logo depois de a Mi Box dormir, o adb ainda diz "device" com a conexão morta
        if not self._port_open():
            return False
        try:
            return self.run("-s", self.serial, "shell", "echo ok", timeout=5) == "ok"
        except Exception:
            return False

    def _port_open(self, ip=None, port=5555):
        try:
            with socket.create_connection((ip or self.ip, port), timeout=1):
                return True
        except OSError:
            return False

    def wake_on_lan(self, mac_txt=BOX_MAC, ip=None):
        mac = bytes(int(x, 16) for x in mac_txt.split("-"))
        pkt = b"\xff" * 6 + mac * 16
        ip = ip or self.ip
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            for dst in ("255.255.255.255", ip.rsplit(".", 1)[0] + ".255", ip):
                s.sendto(pkt, (dst, 9))

    def tv_on(self):
        return self._port_open(TV_IP, 3000)

    def ensure_tv(self, progress=lambda m: None):
        """Liga a TV se estiver desligada: pelo HDMI-CEC da Mi Box e, se não der, pela rede."""
        if self.tv_on():
            return True
        progress("Ligando a TV")
        steps = (lambda: None,  # o KEYCODE_WAKEUP que acabou de acordar a Mi Box já costuma ligar a TV
                 lambda: self.shell("cmd hdmi_control onetouchplay"),  # pede de novo, pelo HDMI
                 lambda: self.wake_on_lan(TV_MAC, TV_IP))  # precisa de "Ligar via Wi-Fi" ativo na LG
        for step in steps:
            step()
            for _ in range(10):
                if self.tv_on():
                    return True
                time.sleep(1)
        return False

    def connect(self, progress=lambda m: None):
        """Liga a conexão do adb, acordando a Mi Box pela rede se ela estiver dormindo."""
        if self.online():
            return True
        if not self._port_open():
            progress("Acordando a Mi Box")
            for i in range(30):
                if i % 4 == 0:
                    self.wake_on_lan()  # o pacote às vezes se perde no Wi-Fi: manda de novo enquanto espera
                time.sleep(1)
                if self._port_open():
                    break
            else:
                return False
        for _ in range(3):
            # quando a Mi Box dorme, a conexão antiga fica "offline" no adb e o connect diz "já conectado";
            # derrubar antes faz ele abrir uma conexão nova
            self.run("disconnect", self.serial, timeout=5)
            self.run("connect", self.serial, timeout=10)
            for _ in range(3):
                if self.online():
                    return True
                time.sleep(1)
        return False

    def wake_screen(self):
        # acorda a tela; pelo HDMI-CEC a TV liga junto (e a tela inicial do Google TV vem para frente)
        self.shell("input keyevent KEYCODE_WAKEUP")

    def open_library(self, video_id, pos_ms):
        """Pede para o app onVLC tocar um vídeo da biblioteca a partir de pos_ms."""
        out = self.shell(f"am start -n {APP_ACTIVITY} --es play {_q(video_id)} --el pos {int(pos_ms)}")
        log.info("abrindo na TV em %ds: %s", pos_ms // 1000, out.replace("\n", " ")[:200])
        return out

    def stop_library(self):
        """Para o filme que o app onVLC está tocando (ele manda a posição para a biblioteca)."""
        self.shell(f"am start -n {APP_ACTIVITY} --ez stop true")

    def app_in_front(self, timeout=8):
        """Espera o app onVLC aparecer na tela (ao acordar, a tela inicial do Google TV pode passar por cima)."""
        for _ in range(int(timeout)):
            if APP_PKG in self.shell("dumpsys window | grep mCurrentFocus", timeout=5):
                return True
            time.sleep(1)
        return False
