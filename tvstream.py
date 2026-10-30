"""Assistir na TV: o PC serve o vídeo original por HTTP e o VLC da Mi Box toca.

Entre os dois fica o app TV Cache (pasta android\\), que guarda uma janela de blocos em volta do
ponto que está tocando: 5 min à frente, 1 min atrás e até 2 GB do que já baixou. Sem o app
instalado, o VLC da TV lê direto do PC (funciona, só que com a reserva pequena do próprio VLC).
"""
import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8765
BOX_IP = "192.168.1.31"
BOX_MAC = "AA-BB-CC-00-00-01"
TV_IP, TV_MAC = "192.168.1.13", "AA-BB-CC-00-00-03"  # LG C1; porta 3000 só responde com ela ligada
CACHE_PKG = "com.lucas.tvcache"
VLC_PKG = "org.videolan.vlc"
STATUS_PORT = 8091  # adb forward tcp:8091 → TV Cache (127.0.0.1:8090 na Mi Box)
NO_WINDOW = 0x08000000
CHUNK = 1024 * 1024
log = logging.getLogger("onvlc.tv")


# ---------- servidor de vídeo ----------
def send_file(h, path, body=True):
    """Responde um pedido HTTP com o arquivo, aceitando Range (o VLC e o TV Cache pedem pedaços)."""
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
        if start >= size:
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
            pass  # o VLC fechou a conexão para pular para outro ponto: normal


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _file(self):
        m = re.match(r"^/v/([A-Za-z0-9_-]+)/", self.path)
        return self.server.files.get(m.group(1)) if m else None

    def do_HEAD(self):
        self._serve(body=False)

    def do_GET(self):
        self._serve(body=True)

    def _serve(self, body):
        if self.path == "/ping":  # o próprio onVLC confere se é ele que responde nesta porta
            data = self.server.nonce.encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if body:
                self.wfile.write(data)
            return
        send_file(self, self._file(), body)


class _Server(ThreadingHTTPServer):
    # no Windows, "reaproveitar endereço" deixa dois programas abrirem a mesma porta e os pedidos caírem no
    # errado; o modo exclusivo impede isso
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class StreamServer:
    def __init__(self):
        self.httpd = None
        self.port = PORT

    def start(self, lan_ip):
        if self.httpd:
            return
        # cada janela do onVLC pega a primeira porta em que ela mesma responde pelo IP da rede
        # (a regra do firewall vale para o programa todo, qualquer porta)
        for port in range(PORT, PORT + 10):
            try:
                httpd = _Server(("0.0.0.0", port), _Handler)
            except OSError:
                continue
            httpd.files = {}
            httpd.nonce = secrets.token_hex(8)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                with urllib.request.urlopen(f"http://{lan_ip}:{port}/ping", timeout=2) as r:
                    ours = r.read().decode() == httpd.nonce
            except Exception:
                ours = False
            if ours:
                self.httpd, self.port = httpd, port
                log.info("servidor de vídeo na porta %d", port)
                return
            log.warning("porta %d responde por outro programa; tentando a próxima", port)
            httpd.shutdown()
            httpd.server_close()
        raise RuntimeError(f"Nenhuma porta livre entre {PORT} e {PORT + 9}")

    def share(self, path, box_ip):
        """Libera um arquivo e devolve o endereço dele, como a Mi Box enxerga o PC."""
        lan_ip = local_ip(box_ip)
        self.start(lan_ip)
        token = secrets.token_urlsafe(12)
        # endereços anteriores do mesmo vídeo continuam valendo (o TV Cache pode ainda estar usando um deles);
        # de outro vídeo, não
        files = {t: p for t, p in self.httpd.files.items() if p == str(path)}
        files[token] = str(path)
        self.httpd.files = files
        # o tamanho no caminho deixa o TV Cache reconhecer o mesmo filme numa próxima vez (o token muda)
        return (f"http://{lan_ip}:{self.port}/v/{token}/{os.path.getsize(path)}/"
                + urllib.parse.quote(Path(path).name))


def local_ip(peer):
    """IP deste PC na rede da Mi Box."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((peer, 9))
        return s.getsockname()[0]
    finally:
        s.close()


# ---------- Mi Box ----------
def _q(s):
    """Aspas simples para o shell do Android."""
    return "'" + str(s).replace("'", "'\\''") + "'"


class MiBox:
    def __init__(self, ip=BOX_IP):
        self.ip = ip
        self.adb = shutil.which("adb") or self._winget_adb()

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
        # acorda a tela; pelo HDMI-CEC a TV liga junto
        self.shell("input keyevent KEYCODE_WAKEUP")

    def has_cache_app(self):
        return "package:" in self.shell(f"pm path {CACHE_PKG}")

    def play(self, url, title, pos_ms, bps):
        """Abre no VLC da TV, passando pelo TV Cache quando ele está instalado."""
        if self.has_cache_app():
            cmd = (f"am start -n {CACHE_PKG}/.PlayActivity -d {_q(url)} --es title {_q(title)} "
                   f"--el position {int(pos_ms)} --el bps {int(bps)}")
        else:
            cmd = (f"am start -n {VLC_PKG}/org.videolan.vlc.gui.video.VideoPlayerActivity -a android.intent.action.VIEW "
                   f"-d {_q(url)} -t video/* --es title {_q(title)} --el position {int(pos_ms)}")
        out = self.shell(cmd)
        log.info("abrindo na TV em %ds: %s", pos_ms // 1000, out.replace("\n", " ")[:200])
        return out

    def vlc_in_front(self, timeout=8):
        """Espera o VLC aparecer na tela da TV (ao acordar, a tela inicial do Android pode passar por cima)."""
        for _ in range(int(timeout)):
            if VLC_PKG in self.shell("dumpsys window | grep mCurrentFocus", timeout=5):
                return True
            time.sleep(1)
        return False

    def status(self):
        """Janela do TV Cache: posição, à frente, atrás, guardado, velocidade."""
        if not getattr(self, "_forwarded", False):
            self.run("-s", self.serial, "forward", f"tcp:{STATUS_PORT}", "tcp:8090", timeout=5)
            self._forwarded = True
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{STATUS_PORT}/status", timeout=2) as r:
                return json.load(r)
        except Exception:
            self._forwarded = False  # a conexão do adb caiu: refaz o túnel na próxima
            raise

    def tv_position(self):
        """Onde o VLC da TV está, em segundos (pela sessão de mídia do Android), e se está tocando."""
        out = self.shell("cat /proc/uptime; dumpsys media_session", timeout=5)
        # o Android guarda também sessões antigas do VLC (paradas); vale a que está tocando ou pausada
        found = []
        for part in out.split("package=")[1:]:
            if not part.startswith(VLC_PKG):
                continue
            m = re.search(r"state=PlaybackState \{state=(\w+)\(\d+\), position=(-?\d+),.*?speed=([\d.]+), updated=(\d+)", part)
            if m:
                found.append(m)
        active = [m for m in found if m.group(1) in ("PLAYING", "PAUSED", "BUFFERING")]
        m = (active or found or [None])[0]
        if not m:
            return None, None
        state, pos = m.group(1), max(0, int(m.group(2))) / 1000
        if state == "PLAYING":
            # o Android só grava a posição quando o estado muda; soma o tempo desde então
            uptime = float(out.split()[0])
            pos += max(0.0, uptime - int(m.group(4)) / 1000) * float(m.group(3))
        return pos, state

    def stop(self):
        self.shell("input keyevent KEYCODE_MEDIA_STOP")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{STATUS_PORT}/stop", timeout=2).read()
        except Exception:
            pass
