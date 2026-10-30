"""Miniaturas da linha do tempo.

Ao abrir um vídeo, o ffmpeg tira um quadro pequeno de cada trecho em segundo plano
(só quadros-chave, então cada um leva ~0,4 s mesmo num 4K) e guarda em disco.
A ordem vai do grosso para o fino: primeiro quadros espalhados pelo filme inteiro,
depois os do meio, então passar o mouse já mostra algo perto em poucos segundos.
"""
import hashlib
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import tvfolder

THUMB_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "onVLC" / "thumbs"
COUNT = 200  # quadros por vídeo (um a cada ~40 s num filme de 2 h)
WIDTH = 240
KEEP = 60  # vídeos guardados; os mais antigos saem
WORKERS = 2
BELOW_NORMAL = 0x4000  # prioridade baixa: não disputa com o vídeo tocando
# HDR (PQ/HLG) sem conversão sai lavado; isto leva para cores normais
HDR_VF = (f"zscale=w={WIDTH}:h=-2:t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
          "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


def _order(n):
    """Índices do grosso para o fino: 0, n/2, n/4, 3n/4, ..."""
    out, seen, step = [], set(), 1
    while step * 2 < n:
        step *= 2
    while step >= 1:
        for i in range(0, n, step):
            if i not in seen:
                seen.add(i)
                out.append(i)
        step //= 2
    return out


def _is_hdr(ffprobe, path):
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=color_transfer", "-of", "json", str(path)],
                             capture_output=True, creationflags=tvfolder.NO_WINDOW, timeout=20)
        streams = json.loads(out.stdout or b"{}").get("streams", [])
        return bool(streams) and streams[0].get("color_transfer") in ("smpte2084", "arib-std-b67")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return False


class Thumbs:
    def __init__(self):
        self.gen = 0
        self.dir = None
        self.dur = 0.0
        self.n = 0
        self.have = set()
        self.lock = threading.Lock()

    def start(self, path, dur):
        self.gen += 1
        self.dur, self.dir, self.have = dur, None, set()
        ffmpeg, ffprobe = tvfolder.find_tool("ffmpeg"), tvfolder.find_tool("ffprobe")
        if not ffmpeg or not ffprobe or dur <= 0:
            return
        st = os.stat(path)
        key = hashlib.md5(f"{path.lower()}|{st.st_size}|{int(st.st_mtime)}".encode()).hexdigest()
        self.dir = THUMB_DIR / key
        self.dir.mkdir(parents=True, exist_ok=True)
        os.utime(self.dir)  # marca como usado agora, para a limpeza
        self.n = max(1, min(COUNT, int(dur / 5)))
        self.have = {int(p.stem) for p in self.dir.glob("*.jpg") if p.stem.isdigit()}
        todo = [i for i in _order(self.n) if i not in self.have]
        if todo:
            threading.Thread(target=self._run, args=(self.gen, path, ffmpeg, ffprobe, todo), daemon=True).start()

    def _run(self, gen, path, ffmpeg, ffprobe, todo):
        self._prune()
        vf = HDR_VF if _is_hdr(ffprobe, path) else f"scale={WIDTH}:-2"
        folder, step = self.dir, self.dur / self.n
        it = iter(todo)

        def worker():
            while gen == self.gen:
                with self.lock:
                    i = next(it, None)
                if i is None:
                    return
                out = folder / f"{i}.jpg"
                tmp = folder / f"{i}.tmp.jpg"
                try:
                    subprocess.run([ffmpeg, "-hide_banner", "-v", "error", "-y", "-skip_frame", "nokey",
                                    "-ss", f"{(i + 0.5) * step:.1f}", "-i", str(path), "-map", "0:v:0",
                                    "-frames:v", "1", "-vf", vf, "-q:v", "4", str(tmp)],
                                   capture_output=True, timeout=30,
                                   creationflags=tvfolder.NO_WINDOW | BELOW_NORMAL)
                    if tmp.exists() and tmp.stat().st_size > 0:
                        os.replace(tmp, out)
                        if gen == self.gen:
                            self.have.add(i)
                except (OSError, subprocess.TimeoutExpired):
                    pass

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(WORKERS)]
        for t in threads:
            t.start()

    def at(self, sec):
        """Arquivo da miniatura mais perto de `sec` que já existe, ou ''."""
        if not self.have or self.dur <= 0:
            return ""
        want = max(0, min(self.n - 1, int(sec / self.dur * self.n)))
        have = self.have
        for d in range(self.n):
            for i in (want - d, want + d):
                if i in have:
                    return str(self.dir / f"{i}.jpg")
        return ""

    @staticmethod
    def _prune():
        try:
            dirs = sorted((d for d in THUMB_DIR.iterdir() if d.is_dir()), key=lambda d: d.stat().st_mtime, reverse=True)
        except OSError:
            return
        for d in dirs[KEEP:]:
            shutil.rmtree(d, ignore_errors=True)
