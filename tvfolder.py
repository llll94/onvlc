"""Pasta da TV: vídeos em C:\\Users\\lucas\\Videos\\TV que o Syncthing copia para dentro da Mi Box.

No PC a pasta é "Somente enviar"; na Mi Box, "Somente receber". Apagar daqui apaga da TV.
Vídeos mandados pelo player podem ser convertidos para HEVC (encoder da placa AMD) antes de
entrar na pasta. Tudo é preparado numa pasta de trabalho e só depois movido para cá, para o
Syncthing nunca copiar um arquivo pela metade.
"""
import ctypes
import glob
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path

TV_DIR = Path.home() / "Videos" / "TV"
WORK_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "onVLC" / "tv-tmp"
FOLDER_ID = "tv-videos"
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".webm", ".m4v", ".wmv", ".flv", ".ts", ".m2ts", ".mpg", ".mpeg"}
HEAVY_BPS = 6_000_000  # acima disso, vale converter
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: ffmpeg sem janela preta


def gb(n):
    return n / 1024 ** 3


def size_txt(n):
    return f"{gb(n):.1f} GB".replace(".", ",") if n >= 1024 ** 3 else f"{n / 1024 ** 2:.0f} MB"


# ---------- Syncthing ----------
class Syncthing:
    def __init__(self):
        self.base = "http://127.0.0.1:8384"
        self.key = ""
        cfg = Path(os.environ.get("LOCALAPPDATA", "")) / "Syncthing" / "config.xml"
        try:
            text = cfg.read_text(encoding="utf-8")
            self.key = re.search(r"<apikey>(.*?)</apikey>", text).group(1)
            addr = re.search(r"<gui[^>]*>.*?<address>(.*?)</address>", text, re.S)
            if addr:
                self.base = "http://" + addr.group(1).replace("0.0.0.0", "127.0.0.1")
        except (OSError, AttributeError):
            pass

    def call(self, path, method="GET", body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"X-API-Key": self.key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=4) as r:
            raw = r.read()
        return json.loads(raw) if raw else None

    def ensure_folder(self):
        """Cria a pasta tv-videos no Syncthing do PC, se ainda não existir."""
        try:
            self.call(f"/rest/config/folders/{FOLDER_ID}")
            return
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
        folder = self.call("/rest/config/defaults/folder")
        folder.update(id=FOLDER_ID, label="TV", path=str(TV_DIR), type="sendonly", devices=[])
        self.call(f"/rest/config/folders/{FOLDER_ID}", "PUT", folder)

    def status(self):
        """Quem recebe a pasta, se está conectado, e quais arquivos a TV ainda não tem."""
        my_id = self.call("/rest/system/status")["myID"]
        folder = self.call(f"/rest/config/folders/{FOLDER_ID}")
        others = [d["deviceID"] for d in folder.get("devices", []) if d["deviceID"] != my_id]
        if not others:
            return {"device": "", "connected": False, "need": set(), "completion": 0}
        dev = others[0]
        name = self.call(f"/rest/config/devices/{dev}").get("name") or dev[:7]
        conn = self.call("/rest/system/connections")["connections"].get(dev, {})
        comp = self.call(f"/rest/db/completion?folder={FOLDER_ID}&device={dev}")
        need = set()
        page = self.call(f"/rest/db/remoteneed?folder={FOLDER_ID}&device={dev}&perpage=1000") or {}
        for v in page.values():
            if isinstance(v, list):
                need.update(f.get("name", "") for f in v if isinstance(f, dict))
        return {"device": name, "connected": bool(conn.get("connected")), "need": need,
                "completion": round(comp.get("completion", 0))}


# ---------- lixeira ----------
class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]


def to_recycle_bin(path):
    """Manda para a Lixeira em vez de apagar de vez: o vídeo pode ser a única cópia."""
    # FO_DELETE = 3; FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    op = _SHFILEOPSTRUCTW(wFunc=3, pFrom=str(path) + "\0", fFlags=0x40 | 0x10 | 0x4 | 0x400)
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0


# ---------- ffmpeg ----------
def find_tool(name):
    found = shutil.which(name)
    if found:
        return found
    pkgs = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages")
    hits = glob.glob(os.path.join(pkgs, "Gyan.FFmpeg*", "*", "bin", name + ".exe"))
    return hits[0] if hits else None


def probe(path):
    exe = find_tool("ffprobe")
    if not exe:
        return None
    out = subprocess.run([exe, "-v", "error", "-show_entries",
                          "format=duration,bit_rate:stream=codec_type,codec_name,height,pix_fmt",
                          "-of", "json", str(path)], capture_output=True, creationflags=NO_WINDOW)
    try:
        data = json.loads(out.stdout or b"{}")
    except ValueError:
        return None
    fmt = data.get("format", {})
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    dur = float(fmt.get("duration") or 0)
    bps = float(fmt.get("bit_rate") or 0) or (os.path.getsize(path) * 8 / dur if dur else 0)
    return {"dur": dur, "bps": bps, "codec": video.get("codec_name", ""),
            "height": int(video.get("height") or 0), "pix": video.get("pix_fmt", "")}


def should_convert(info):
    if not info or not info["codec"]:
        return False
    if "10" in info["pix"]:  # 10 bits / HDR: converter para 8 bits estragaria as cores
        return False
    if info["codec"] == "hevc":
        return info["bps"] > HEAVY_BPS * 1.7
    return info["bps"] > HEAVY_BPS


def target_bps(height):
    return 2_500_000 if height <= 720 else 5_000_000 if height <= 1080 else 14_000_000


class TvFolder:
    """Lista, status e fila de envio da Pasta da TV. `on_change` recebe o estado novo (de qualquer thread)."""

    def __init__(self, on_change):
        self.on_change = on_change
        self.st = Syncthing()
        self.jobs = []  # {"name", "src", "state": "queue" | "opt" | "prep", "p"}
        self.lock = threading.Lock()
        self.queue = []
        self.worker = None
        self.optimize = True
        self.autoclean = True
        self.limit_gb = 10
        self.last = {}
        self.setup_error = ""
        self._refreshing = False

    # ---------- configuração ----------
    def setup_async(self):
        def work():
            try:
                TV_DIR.mkdir(parents=True, exist_ok=True)
                self.st.ensure_folder()
                self.setup_error = ""
            except Exception as e:
                self.setup_error = "Syncthing não respondeu: " + str(e)
            self.refresh()
        threading.Thread(target=work, daemon=True).start()

    # ---------- estado ----------
    def files(self):
        try:
            items = [p for p in TV_DIR.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS]
        except OSError:
            return []
        return sorted(items, key=lambda p: p.stat().st_mtime, reverse=True)

    def used(self):
        return sum(p.stat().st_size for p in self.files())

    def refresh(self):
        try:
            sync = self.st.status()
            err = self.setup_error
        except Exception:
            sync = {"device": "", "connected": False, "need": set(), "completion": 0}
            err = "Syncthing não está rodando"
        rows = []
        with self.lock:
            for j in self.jobs:
                rows.append({"name": j["name"], "size": j.get("sizeTxt", ""), "state": j["state"], "p": j.get("p", 0)})
        for p in self.files():
            name = p.name
            if not sync["device"]:
                state = "pc"
            elif name in sync["need"]:
                state = "copy" if sync["connected"] else "wait"
            else:
                state = "ok"
            rows.append({"name": name, "size": size_txt(p.stat().st_size), "state": state, "p": 0})
        used = self.used()
        self.last = {"error": err, "device": sync["device"], "connected": sync["connected"],
                     "completion": sync["completion"], "files": rows, "path": str(TV_DIR),
                     "used": round(gb(used), 2), "usedTxt": size_txt(used), "limit": self.limit_gb, "optimize": self.optimize,
                     "autoclean": self.autoclean, "ffmpeg": bool(find_tool("ffmpeg"))}
        self.on_change(self.last)

    def refresh_async(self):
        if self._refreshing:
            return
        self._refreshing = True

        def work():
            try:
                self.refresh()
            finally:
                self._refreshing = False
        threading.Thread(target=work, daemon=True).start()

    # ---------- ações ----------
    def delete(self, name):
        p = TV_DIR / name
        ok = p.is_file() and to_recycle_bin(p)
        self.refresh_async()
        return ok

    def add(self, src, convert=None):
        """Põe na fila. convert=None decide pelo tamanho (se a opção de otimizar estiver ligada)."""
        src = Path(src)
        with self.lock:
            if any(j["src"] == src for j in self.jobs):
                return False
            job = {"name": src.name, "src": src, "state": "queue", "p": 0, "sizeTxt": size_txt(src.stat().st_size),
                   "convert": convert}
            self.jobs.append(job)
        self.queue.append(job)
        if not self.worker or not self.worker.is_alive():
            self.worker = threading.Thread(target=self._run, daemon=True)
            self.worker.start()
        self.refresh_async()
        return True

    def _run(self):
        while self.queue:
            job = self.queue.pop(0)
            try:
                msg = self._process(job)
            except Exception as e:
                msg = f"Não consegui mandar {job['name']}: {e}"
            with self.lock:
                self.jobs.remove(job)
            self.refresh()
            self.on_change(dict(self.last, toast=msg))

    def _process(self, job):
        src = job["src"]
        WORK_DIR.mkdir(parents=True, exist_ok=True)
        info = probe(src)
        convert = job["convert"]
        if convert is None:
            convert = self.optimize and should_convert(info)
        out = None
        if convert and find_tool("ffmpeg"):
            job["state"] = "opt"
            out = self._convert(job, info)
            if out and out.stat().st_size > src.stat().st_size * 0.85:  # quase não diminuiu: manda o original
                out.unlink(missing_ok=True)
                out = None
                job.pop("shrunk", None)
        if out is None:
            job["state"] = "prep"
            out = WORK_DIR / src.name
            self._copy(job, src, out)
        size = out.stat().st_size
        if gb(size) > self.limit_gb:
            out.unlink(missing_ok=True)
            return f"{src.name} tem {size_txt(size)} e não cabe no limite de {self.limit_gb} GB da TV"
        freed = self._make_room(size)
        dest = TV_DIR / out.name
        n = 2
        while dest.exists():
            dest = TV_DIR / f"{out.stem} ({n}){out.suffix}"
            n += 1
        os.replace(out, dest)
        msg = f"{dest.name} está indo para a TV"
        if job.get("shrunk"):
            msg = f"{dest.name} otimizado ({job['shrunk']}) e indo para a TV"
        if freed:
            msg += f" · apaguei {len(freed)} antigo{'s' if len(freed) > 1 else ''} para caber"
        return msg

    def _make_room(self, incoming):
        files = self.files()
        used = sum(p.stat().st_size for p in files)
        freed = []
        if not self.autoclean:
            return freed
        limit = self.limit_gb * 1024 ** 3
        for p in reversed(files):  # mais antigos primeiro
            if used + incoming <= limit:
                break
            size = p.stat().st_size
            if to_recycle_bin(p):
                used -= size
                freed.append(p.name)
        return freed

    def _copy(self, job, src, out):
        total = src.stat().st_size or 1
        done = 0
        last = 0
        with open(src, "rb") as fi, open(out, "wb") as fo:
            while True:
                chunk = fi.read(8 * 1024 * 1024)
                if not chunk:
                    break
                fo.write(chunk)
                done += len(chunk)
                job["p"] = round(done * 100 / total)
                if time.time() - last > 0.5:
                    last = time.time()
                    self.refresh()
        shutil.copystat(src, out)
        os.utime(out)  # data de agora: é por ela que se escolhe o mais antigo para apagar

    def _convert(self, job, info):
        src = job["src"]
        out = WORK_DIR / (src.stem + ".mkv")
        bps = target_bps(info["height"])
        hw = ["-c:v", "hevc_amf", "-rc", "vbr_peak", "-b:v", str(bps), "-maxrate", str(int(bps * 1.6)),
              "-pix_fmt", "nv12"]
        sw = ["-c:v", "libx265", "-preset", "fast", "-crf", "24", "-pix_fmt", "yuv420p"]
        # tenta: placa de vídeo com legendas → placa sem legendas → processador sem legendas
        for enc, subs in ((hw, True), (hw, False), (sw, False)):
            maps = ["-map", "0:v:0", "-map", "0:a?"] + (["-map", "0:s?", "-map", "0:t?"] if subs else [])
            cmd = [find_tool("ffmpeg"), "-hide_banner", "-nostats", "-y", "-i", str(src), *maps,
                   "-c", "copy", *enc, "-progress", "pipe:1", str(out)]
            if self._ffmpeg(job, cmd, info["dur"]):
                job["shrunk"] = f"{size_txt(src.stat().st_size)} → {size_txt(out.stat().st_size)}"
                return out
            out.unlink(missing_ok=True)
        return None

    def _ffmpeg(self, job, cmd, dur):
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                                creationflags=NO_WINDOW)
        last = 0
        for line in proc.stdout:
            if line.startswith("out_time_us=") and dur:
                try:
                    job["p"] = min(99, round(int(line.split("=")[1]) / 1e6 * 100 / dur))
                except ValueError:
                    continue
                if time.time() - last > 0.5:
                    last = time.time()
                    self.refresh()
        return proc.wait() == 0
