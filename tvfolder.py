"""Pasta onVLC: os vídeos que a TV mostra (Vídeos\\onVLC), vistos do player do PC.

A biblioteca (library.py) lê a pasta e a TV toca direto do PC. Aqui fica o lado do player:
- a lista da pasta com o nome que a TV mostra e o estado de cada vídeo (na TV, lendo, com problema);
- "Mandar para a TV": põe o vídeo na pasta. No mesmo disco vira um link (instantâneo, sem ocupar espaço
  a mais e sem tirar o original de onde está); em outro disco, é copiado. Legendas com o mesmo nome vão junto;
- apagar manda para a Lixeira (se era um link, o original continua onde estava).

Até 2026-10-30 esta era a "Pasta da TV" (Vídeos\\TV), que o Syncthing copiava para dentro da Mi Box.
"""
import ctypes
import glob
import json
import os
import shutil
import threading
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".webm", ".m4v", ".wmv", ".flv", ".ts", ".m2ts", ".mpg", ".mpeg"}
SUB_EXTS = {".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx"}
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: ffmpeg sem janela preta


def lib_dir():
    import library  # aqui dentro: library.py também importa este módulo
    return library.LIB_DIR


def gb(n):
    return n / 1024 ** 3


def size_txt(n):
    return f"{gb(n):.1f} GB".replace(".", ",") if n >= 1024 ** 3 else f"{n / 1024 ** 2:.0f} MB"


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


# ---------- pasta onVLC ----------
class LibraryFolder:
    """Lista e envios da pasta onVLC. `on_change` recebe o estado novo (de qualquer thread)."""

    def __init__(self, on_change):
        self.on_change = on_change
        self.jobs = []  # cópias em andamento: {"name", "src", "p"}
        self.lock = threading.Lock()
        self._refreshing = False

    def files(self):
        import library
        out = []
        for root, _dirs, names in os.walk(lib_dir()):
            for n in names:
                p = Path(root) / n
                if p.suffix.lower() in VIDEO_EXTS and not library.is_sample(p) and not n.endswith(".part"):
                    out.append(p)
        return sorted(out, key=lambda p: p.stat().st_mtime, reverse=True)

    def refresh(self):
        # o que a biblioteca já sabe de cada arquivo (nome do filme, qualidade, problema)
        known, running, summary = {}, False, ""
        try:
            with urllib.request.urlopen("http://127.0.0.1:8780/api/library", timeout=2) as r:
                lib = json.load(r)
            running = True
            for m in lib["movies"]:
                known[m["file"].lower()] = {"title": f'{m["title"]} ({m["year"]})' if m.get("year") else m["title"],
                                            "quality": m.get("quality", ""), "broken": m.get("broken")}
            for s in lib["shows"]:
                for se in s["seasons"]:
                    for e in se["episodes"]:
                        known[e["file"].lower()] = {"title": f'{s["title"]} · T{e["season"]}:E{e["episode"]}',
                                                    "quality": e.get("quality", ""), "broken": e.get("broken")}
            n_m, n_s = len(lib["movies"]), len(lib["shows"])
            summary = f"{n_m} filme{'s' if n_m != 1 else ''}" + (f", {n_s} série{'s' if n_s != 1 else ''}" if n_s else "")
        except Exception:
            pass
        rows = []
        with self.lock:
            for j in self.jobs:
                rows.append({"name": j["name"], "title": j["name"], "size": j["sizeTxt"], "state": "copy", "p": j["p"], "path": ""})
        total = 0
        for p in self.files():
            try:
                size = p.stat().st_size
            except OSError:
                continue
            total += size
            k = known.get(p.name.lower())
            state = "pc" if not running else "bad" if k and k["broken"] else "ok" if k else "read"
            rows.append({"name": p.name, "title": (k or {}).get("title") or p.stem, "size": size_txt(size),
                         "quality": (k or {}).get("quality", ""), "state": state, "p": 0, "path": str(p)})
        self.on_change({"path": str(lib_dir()), "running": running, "summary": summary, "files": rows,
                        "totalTxt": size_txt(total)})

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
    def add(self, src):
        """Põe o vídeo (e as legendas de mesmo nome) na pasta. Devolve o aviso para mostrar."""
        src = Path(src)
        base = lib_dir()
        base.mkdir(parents=True, exist_ok=True)
        try:
            if base.resolve() in src.resolve().parents:
                return "Esse vídeo já está na pasta onVLC"
        except OSError:
            pass
        dest = base / src.name
        if dest.exists():
            return "Já tem um vídeo com esse nome na pasta onVLC" if dest.stat().st_size != src.stat().st_size \
                else "Esse vídeo já está na pasta onVLC"
        subs = [f for f in src.parent.glob(glob.escape(src.stem) + "*") if f.suffix.lower() in SUB_EXTS]
        try:
            os.link(src, dest)  # mesmo disco: link instantâneo, sem ocupar espaço a mais
            for f in subs:
                if not (base / f.name).exists():
                    os.link(f, base / f.name)
            self.refresh_async()
            return f"{src.stem} está na TV" + (f" (com {len(subs)} legenda{'s' if len(subs) > 1 else ''})" if subs else "")
        except OSError:
            pass  # outro disco: copia em segundo plano
        with self.lock:
            if any(j["src"] == src for j in self.jobs):
                return "Esse vídeo já está sendo copiado"
            job = {"name": src.name, "src": src, "p": 0, "sizeTxt": size_txt(src.stat().st_size)}
            self.jobs.append(job)
        threading.Thread(target=self._copy, args=(job, dest, subs), daemon=True).start()
        self.refresh_async()
        return f"Copiando {src.stem} para a pasta onVLC"

    def _copy(self, job, dest, subs):
        src = job["src"]
        tmp = dest.with_name(dest.name + ".part")  # a biblioteca ignora ".part": nunca lê um arquivo pela metade
        msg = f"{src.stem} está na TV"
        try:
            total = src.stat().st_size or 1
            done, last = 0, 0
            with open(src, "rb") as fi, open(tmp, "wb") as fo:
                while True:
                    chunk = fi.read(8 << 20)
                    if not chunk:
                        break
                    fo.write(chunk)
                    done += len(chunk)
                    job["p"] = round(done * 100 / total)
                    if time.time() - last > 0.5:
                        last = time.time()
                        self.refresh()
            os.replace(tmp, dest)
            for f in subs:
                if not (dest.parent / f.name).exists():
                    shutil.copy2(f, dest.parent / f.name)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            msg = f"Não consegui copiar {src.name}: {e}"
        with self.lock:
            self.jobs.remove(job)
        self.refresh()
        self.on_change({"toast": msg})

    def delete(self, path):
        p = Path(path)
        try:
            if lib_dir().resolve() not in p.resolve().parents:
                return False
        except OSError:
            return False
        ok = p.is_file() and to_recycle_bin(p)
        self.refresh_async()
        return ok
