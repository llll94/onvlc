"""Player de vídeo com motor do VLC e interface própria.

A janela de baixo (VideoWindow) só mostra o vídeo, desenhado pelo VLC.
Por cima dela fica uma janela transparente em QML (ui/Overlay.qml) com a barra,
o menu e os quadros. As duas andam juntas.
"""
import ctypes
import difflib
import hashlib
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

VLC_DIR = r"C:\Program Files\VideoLAN\VLC"
os.environ.setdefault("PYTHON_VLC_MODULE_PATH", VLC_DIR)

import vlc  # noqa: E402
from PySide6.QtCore import QEvent, QObject, Property, QSettings, Qt, QTimer, QUrl, Signal, Slot  # noqa: E402
from PySide6.QtGui import QColor, QIcon, QPalette, QSurfaceFormat  # noqa: E402
from PySide6.QtQuick import QQuickView, QQuickWindow, QSGRendererInterface  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QWidget  # noqa: E402

import opensubs  # noqa: E402
import thumbs  # noqa: E402
import tvfolder  # noqa: E402
import tvstream  # noqa: E402

try:
    import keyring  # guarda chave e senha no Gerenciador de Credenciais do Windows
except ImportError:
    keyring = None

LOG_FILE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "onVLC" / "onvlc.log"
log = logging.getLogger("onvlc")

# no .exe os arquivos ficam em sys._MEIPASS; rodando o .py, ficam ao lado dele
HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".webm", ".m4v", ".wmv", ".flv", ".ts", ".m2ts", ".mpg", ".mpeg"}
SUB_EXTS = {".srt", ".ass", ".ssa", ".vtt", ".sub"}
SUB_DIRS = ["", "Subs", "Sub", "Legendas", "Legenda", "Subtitles"]
ASPECTS = [None, "16:9", "4:3", "21:9", "1:1"]
RATES = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
# capítulo sem nome de verdade: "00:12:30.000", "Chapter 05", "Capítulo 5"
GENERIC_CHAPTER = re.compile(r"^\s*((chapter|chap|cap[ií]tulo|cap)\.?\s*\d+|\d{1,2}(:\d{2}){1,2}([.,]\d+)?)\s*$", re.I)


def _dec(x):
    return x.decode("utf-8", "replace") if isinstance(x, bytes) else str(x)


def _norm(name):
    return re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()


class VideoWindow(QWidget):
    """Janela sem borda onde o VLC desenha o vídeo."""

    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_NativeWindow)
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor("black"))
        self.setPalette(pal)
        self.setAutoFillBackground(True)
        self.setMinimumSize(480, 270)
        self.resize(1280, 720)
        self.setWindowTitle("onVLC")

    def moveEvent(self, e):
        self.ctl.sync_overlay()

    def resizeEvent(self, e):
        self.ctl.sync_overlay()

    def changeEvent(self, e):
        if e.type() == QEvent.WindowStateChange:
            self.ctl.on_window_state()
        elif e.type() == QEvent.ActivationChange and self.isActiveWindow():
            QTimer.singleShot(0, self.ctl.overlay.requestActivate)
        super().changeEvent(e)

    def closeEvent(self, e):
        self.ctl.shutdown()
        e.accept()


class Player(QObject):
    stateChanged = Signal()
    toast = Signal(str)
    _webResult = Signal(object)  # resposta do OpenSubtitles, vinda de outra thread
    _tvState = Signal(object)  # estado da Pasta da TV, vindo de outra thread
    _watchState = Signal(object)  # estado do Assistir na TV, vindo de outra thread

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.settings = QSettings("PlayerLucas", "Player")
        self.os_configured = bool(self._secret("api_key"))
        self.vlc = vlc.Instance(["--quiet", "--no-video-title-show", "--sub-autodetect-fuzzy=3"])
        self.mp = self.vlc.media_player_new()
        self.mp.video_set_mouse_input(False)
        self.mp.video_set_key_input(False)

        self.path = None
        self.playlist = []
        self.index = -1
        self.vol = int(self.settings.value("vol", 80))
        self.vol_max = 100
        self.muted = False
        self.repeat = False
        self.resume = self.settings.value("resume", "true") == "true"
        self.aspect_i = 0
        self.on_top = False
        self.pending_resume = None
        self.ticks = 0
        self.os_client = None
        self.thumbs = thumbs.Thumbs()
        self.thumbs_for = None
        self._webResult.connect(self._on_web)
        self.st = {"tv": {}}
        self._reset_media_state()

        self.tv = tvfolder.TvFolder(self._tvState.emit)
        self.tv.optimize = self.settings.value("tv/optimize", "true") == "true"
        self.tv.autoclean = self.settings.value("tv/autoclean", "true") == "true"
        self.tv.limit_gb = int(self.settings.value("tv/limit", 10))
        self._tvState.connect(self._on_tv)
        self.tv.setup_async()

        self.stream = tvstream.StreamServer()
        self.box = tvstream.MiBox()
        self.watching = False
        self.st["watch"] = {}
        self._watchState.connect(self._on_watch)

        self.win = VideoWindow(self)
        self.overlay = QQuickView()
        fmt = QSurfaceFormat()
        fmt.setAlphaBufferSize(8)
        self.overlay.setFormat(fmt)
        self.overlay.setColor(QColor(0, 0, 0, 0))
        self.overlay.setFlags(Qt.Tool | Qt.FramelessWindowHint)
        self.overlay.setResizeMode(QQuickView.SizeRootObjectToView)
        self.overlay.rootContext().setContextProperty("player", self)
        self.overlay.setSource(QUrl.fromLocalFile(str(HERE / "ui" / "Overlay.qml")))
        if self.overlay.status() == QQuickView.Error:
            for err in self.overlay.errors():
                print(err.toString(), file=sys.stderr)
            sys.exit(1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(200)

    # ---------- janelas ----------
    def show(self):
        self.win.show()
        self.mp.set_hwnd(int(self.win.winId()))
        self.overlay.setTransientParent(self.win.windowHandle())
        self.sync_overlay()
        self.overlay.show()
        self.overlay.requestActivate()

    def sync_overlay(self):
        if not hasattr(self, "overlay"):
            return
        g = self.win.geometry()
        if self.win.isFullScreen():
            # uma janela OpenGL que cobre o monitor inteiro perde a transparência no Windows
            # e vira um retângulo preto por cima do vídeo; 1 pixel a menos evita isso
            g.setHeight(g.height() - 1)
        if self.overlay.geometry() != g:
            self.overlay.setGeometry(g)

    def on_window_state(self):
        if self.win.isMinimized():
            self.overlay.hide()
        else:
            self.sync_overlay()
            self.overlay.show()
            self.overlay.requestActivate()
        self.st["maximized"] = self.win.isMaximized()
        self.st["fullscreen"] = self.win.isFullScreen()
        self.stateChanged.emit()

    def shutdown(self):
        self._save_position()
        self.settings.setValue("vol", self.vol)
        self.timer.stop()
        self.mp.stop()
        self.overlay.close()
        self.app.quit()

    # ---------- estado para a interface ----------
    def _reset_media_state(self):
        self.st.update(hasMedia=False, playing=False, pos=0.0, dur=0.0, title="", chapters=[],
                       audio=[], audioId=-1, subs=[], subId=-1, subDelay=0.0, found=[], badge=False,
                       nextName="", prevName="", web=[], webStatus="idle", webMsg="")
        self._refresh_static()

    def _refresh_static(self):
        self.st.update(vol=self.vol, volMax=self.vol_max, muted=self.muted, repeat=self.repeat,
                       resume=self.resume, aspect=ASPECTS[self.aspect_i] or "Automática", onTop=self.on_top,
                       recent=self._recent_list(), rate=round(self.mp.get_rate() or 1.0, 2),
                       osUser=self.settings.value("osUser", ""), osConfigured=self.os_configured)

    def _get_state(self):
        return self.st

    state = Property("QVariantMap", _get_state, notify=stateChanged)

    def tick(self):
        mp = self.mp
        self.ticks += 1
        if self.path:
            if mp.get_state() == vlc.State.Ended:
                self._on_end()
                return
            self.st["playing"] = mp.is_playing() == 1
            self.st["pos"] = max(0, mp.get_time()) / 1000
            dur = mp.get_length() / 1000
            if dur > 0:
                self.st["dur"] = dur
                if self.thumbs_for != self.path:
                    self.thumbs_for = self.path
                    self.thumbs.start(self.path, dur)
                if self.pending_resume is not None:
                    at = self.pending_resume
                    self.pending_resume = None
                    if 30 < at < dur - 60:
                        mp.set_time(int(at * 1000))
                        self.toast.emit("Continuando de " + self._fmt(at))
            cur = mp.audio_get_volume()
            want = 0 if self.muted else self.vol
            if cur >= 0 and cur != want:
                mp.audio_set_volume(want)
            if self.ticks % 5 == 0:
                self._refresh_tracks()
            if self.ticks % 25 == 0:
                self._save_position()
        self._refresh_static()
        self.stateChanged.emit()

    def _refresh_tracks(self):
        mp = self.mp
        try:
            self.st["audio"] = [{"id": i, "name": _dec(n)} for i, n in (mp.audio_get_track_description() or []) if i >= 0]
            self.st["audioId"] = mp.audio_get_track()
            self.st["subs"] = [{"id": i, "name": _dec(n)} for i, n in (mp.video_get_spu_description() or [])]
            self.st["subId"] = mp.video_get_spu()
            self.st["subDelay"] = mp.video_get_spu_delay() / 1_000_000
        except Exception:
            pass
        if not self.st["chapters"] and self.st["dur"] > 0:
            try:
                chs = list(mp.get_full_chapter_descriptions(-1) or [])
                names = [_dec(c.name) if c.name else "" for c in chs]
                self.st["chapters"] = [{"name": "" if GENERIC_CHAPTER.match(n) else n,
                                        "start": c.time_offset / 1000} for n, c in zip(names, chs)]
            except Exception:
                pass
        self.st["badge"] = bool(self.st["found"]) and self.st["subId"] == -1

    @Slot(float, result=str)
    def thumbAt(self, sec):
        f = self.thumbs.at(sec)
        return QUrl.fromLocalFile(f).toString() if f else ""

    @staticmethod
    def _fmt(s):
        s = int(s)
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"

    # ---------- abrir ----------
    def open_path(self, path, playlist=None):
        path = str(Path(path))
        if not os.path.isfile(path):
            self.toast.emit("Arquivo não encontrado")
            return
        self._save_position()
        self.path = path
        if playlist is None:
            folder = Path(path).parent
            playlist = sorted((str(p) for p in folder.iterdir() if p.suffix.lower() in VIDEO_EXTS), key=str.lower)
        self.playlist = playlist
        self.index = playlist.index(path) if path in playlist else -1
        self._reset_media_state()
        self.mp.set_media(self.vlc.media_new_path(path))
        self.mp.play()
        self.mp.video_set_aspect_ratio(ASPECTS[self.aspect_i])
        self.st.update(hasMedia=True, title=Path(path).stem, found=self._find_subs(path))
        nxt = self.playlist[self.index + 1] if 0 <= self.index < len(self.playlist) - 1 else ""
        prv = self.playlist[self.index - 1] if self.index > 0 else ""
        self.st.update(nextName=Path(nxt).stem if nxt else "", prevName=Path(prv).stem if prv else "")
        self.pending_resume = float(self.settings.value("pos/" + self._key(path), 0)) if self.resume else None
        self._add_recent(path)
        self.stateChanged.emit()
        if self.st["found"]:
            n = len(self.st["found"])
            QTimer.singleShot(1500, lambda: self.toast.emit(
                f"Achei {n} legenda{'s' if n > 1 else ''} na pasta do vídeo"))

    def _on_end(self):
        self.settings.remove("pos/" + self._key(self.path))
        if self.repeat:
            self.open_path(self.path, self.playlist)
        elif 0 <= self.index < len(self.playlist) - 1:
            self.open_path(self.playlist[self.index + 1], self.playlist)
        else:
            self.mp.stop()
            self.st.update(playing=False, pos=self.st["dur"])
            self.stateChanged.emit()

    @staticmethod
    def _key(path):
        return hashlib.md5(path.lower().encode()).hexdigest()

    def _save_position(self):
        if self.path and self.st.get("dur", 0) > 0:
            self.settings.setValue("pos/" + self._key(self.path), self.st["pos"])

    def _recent_list(self):
        rec = self.settings.value("recent", []) or []
        if isinstance(rec, str):
            rec = [rec]
        return [{"name": Path(p).name, "path": p} for p in rec if p != self.path][:5]

    def _add_recent(self, path):
        rec = self.settings.value("recent", []) or []
        if isinstance(rec, str):
            rec = [rec]
        rec = [path] + [p for p in rec if p != path]
        self.settings.setValue("recent", rec[:8])

    # ---------- legendas na pasta ----------
    def _find_subs(self, path):
        video = Path(path)
        stem = _norm(video.stem)
        found = []
        for sub in SUB_DIRS:
            d = video.parent / sub if sub else video.parent
            if not d.is_dir():
                continue
            for f in d.iterdir():
                if f.suffix.lower() not in SUB_EXTS:
                    continue
                score = difflib.SequenceMatcher(None, stem, _norm(f.stem)).ratio()
                if stem and stem in _norm(f.stem):
                    score = max(score, 0.95)
                if score >= 0.45:
                    found.append({"name": f.name, "path": str(f), "where": (sub + "\\") if sub else "nesta pasta",
                                  "score": round(score * 100)})
        return sorted(found, key=lambda x: -x["score"])

    @Slot(str)
    def loadSub(self, path):
        ok = self.mp.add_slave(vlc.MediaSlaveType.subtitle, QUrl.fromLocalFile(path).toString(), True)
        if ok == 0:
            self.toast.emit("Legenda carregada: " + Path(path).name)
            QTimer.singleShot(600, self._refresh_tracks)
        else:
            self.toast.emit("Não consegui carregar essa legenda")

    def _dialog(self, fn):
        """Abre uma janela de escolher arquivo/pasta sem a camada de controles por cima dela."""
        self.overlay.hide()
        try:
            return fn(self.win)
        finally:
            self.sync_overlay()
            self.overlay.show()
            self.overlay.requestActivate()

    @Slot()
    def addSub(self):
        start = str(Path(self.path).parent) if self.path else ""
        f, _ = self._dialog(lambda w: QFileDialog.getOpenFileName(
            w, "Escolher legenda", start, "Legendas (*.srt *.ass *.ssa *.vtt *.sub)"))
        if f:
            self.loadSub(f)

    # ---------- legendas da internet ----------
    def _secret(self, name):
        if keyring:
            try:
                return keyring.get_password("onVLC", name) or ""
            except Exception:
                pass
        return self.settings.value("secret/" + name, "")

    def _set_secret(self, name, value):
        if keyring:
            try:
                keyring.set_password("onVLC", name, value)
                return
            except Exception:
                pass
        self.settings.setValue("secret/" + name, value)

    def _client(self):
        key = self._secret("api_key")
        if not key:
            return None
        user = self.settings.value("osUser", "")
        if not self.os_client or self.os_client.api_key != key or self.os_client.user != user:
            self.os_client = opensubs.Client(key, user, self._secret("password"))
        return self.os_client

    @Slot(str, str, str)
    def saveAccount(self, key, user, password):
        if key.strip():
            self._set_secret("api_key", key.strip())
        self.settings.setValue("osUser", user.strip())
        if password:
            self._set_secret("password", password)
        self.os_client = None
        self.os_configured = bool(self._secret("api_key"))
        self.webSearch()

    def _in_thread(self, path, job):
        def work():
            try:
                self._webResult.emit(dict(job(), path=path))
            except opensubs.OSError_ as e:
                self._webResult.emit({"kind": "error", "path": path, "msg": str(e)})
            except Exception as e:  # qualquer outra falha vira aviso na tela, não fecha o app
                self._webResult.emit({"kind": "error", "path": path, "msg": "Erro inesperado: " + str(e)})
        threading.Thread(target=work, daemon=True).start()

    @Slot()
    def webSearch(self):
        if not self.path:
            return
        client = self._client()
        if not client:
            self.st["webStatus"] = "setup"
            self.stateChanged.emit()
            return
        self.st.update(webStatus="searching", web=[], webMsg="")
        self.stateChanged.emit()
        path, stem = self.path, Path(self.path).stem
        self._in_thread(path, lambda: {"kind": "search", "items": client.search(path, stem)})

    @Slot(int, str)
    def webDownload(self, file_id, lang):
        client = self._client()
        if not client or not self.path:
            return
        self.st["webStatus"] = "downloading"
        self.stateChanged.emit()

        def job():
            data, remaining = client.download(file_id)
            return {"kind": "download", "data": data, "lang": lang, "remaining": remaining}
        self._in_thread(self.path, job)

    def _on_web(self, r):
        if r["path"] != self.path:  # o vídeo mudou enquanto buscava
            return
        if r["kind"] == "search":
            self.st.update(web=r["items"], webStatus="done", webMsg="" if r["items"] else "Nenhuma legenda encontrada")
        elif r["kind"] == "error":
            self.st.update(webStatus="error", webMsg=r["msg"])
        elif r["kind"] == "download":
            self.st["webStatus"] = "done"
            target = self._save_sub(r["data"], r["lang"])
            if target:
                self.loadSub(str(target))
                self.st["found"] = self._find_subs(self.path)
                if r["remaining"] is not None:
                    n = r["remaining"]
                    QTimer.singleShot(2600, lambda: self.toast.emit(f"Restam {n} downloads de legenda hoje"))
        self.stateChanged.emit()

    def _save_sub(self, data, lang):
        """Salva ao lado do vídeo; se a pasta não deixar, salva em %APPDATA%\\onVLC\\legendas."""
        video = Path(self.path)
        tag = lang or "sub"
        for folder in (video.parent, Path(os.environ.get("APPDATA", str(Path.home()))) / "onVLC" / "legendas"):
            try:
                folder.mkdir(parents=True, exist_ok=True)
                target, n = folder / f"{video.stem}.{tag}.srt", 2
                while target.exists():
                    target, n = folder / f"{video.stem}.{tag}.{n}.srt", n + 1
                target.write_bytes(data)
                return target
            except OSError:
                continue
        self.toast.emit("Não consegui salvar a legenda")
        return None

    # ---------- Pasta da TV ----------
    def _on_tv(self, data):
        msg = data.pop("toast", None)
        self.st["tv"] = data
        self.stateChanged.emit()
        if msg:
            self.toast.emit(msg)

    @Slot()
    def tvRefresh(self):
        self.tv.refresh_async()

    @Slot()
    def tvSendCurrent(self):
        if not self.path:
            return
        if Path(self.path).parent == tvfolder.TV_DIR:
            self.toast.emit("Esse vídeo já está na Pasta da TV")
        elif self.tv.add(self.path):
            self.toast.emit("Mandando para a Pasta da TV")

    @Slot()
    def tvAddFiles(self):
        start = str(Path(self.path).parent) if self.path else str(Path.home() / "Videos")
        exts = " ".join("*" + e for e in sorted(VIDEO_EXTS))
        files, _ = self._dialog(lambda w: QFileDialog.getOpenFileNames(
            w, "Mandar para a TV", start, f"Vídeos ({exts})"))
        for f in files:
            self.tv.add(f)

    @Slot(str)
    def tvDelete(self, name):
        if self.tv.delete(name):
            self.toast.emit(name + " saiu da TV (está na Lixeira do PC)")
        else:
            self.toast.emit("Não consegui apagar " + name)

    @Slot(str)
    def tvPlay(self, name):
        p = tvfolder.TV_DIR / name
        if p.is_file():
            self.open_path(str(p))

    @Slot()
    def tvToggleOptimize(self):
        self.tv.optimize = not self.tv.optimize
        self.settings.setValue("tv/optimize", "true" if self.tv.optimize else "false")
        self.tv.refresh_async()

    @Slot()
    def tvToggleAutoclean(self):
        self.tv.autoclean = not self.tv.autoclean
        self.settings.setValue("tv/autoclean", "true" if self.tv.autoclean else "false")
        self.tv.refresh_async()

    @Slot()
    def tvCycleLimit(self):
        steps = [5, 10, 15]  # a Mi Box tem ~22 GB livres
        self.tv.limit_gb = next((g for g in steps if g > self.tv.limit_gb), steps[0])
        self.settings.setValue("tv/limit", self.tv.limit_gb)
        self.tv.refresh_async()

    @Slot()
    def tvOpenFolder(self):
        tvfolder.TV_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(tvfolder.TV_DIR))

    # ---------- Assistir na TV ----------
    def _on_watch(self, data):
        msg = data.pop("toast", None)
        resume = data.pop("resume", None)
        self.st["watch"] = data
        if resume is not None and self.path:
            self.seek(resume)  # o PC continua de onde a TV parou
        self.stateChanged.emit()
        if msg:
            self.toast.emit(msg)

    def _watch_ended(self, msg, pos=None):
        """Sai do modo TV e deixa o aviso no balão, com as opções de continuar."""
        self.watching = False
        log.info("fim do Assistir na TV: %s", msg)
        self._watchState.emit({"stage": "ended", "msg": msg, "pos": pos})

    @Slot()
    def tvWatch(self):
        if not self.path or self.watching:
            return
        path, title = self.path, Path(self.path).stem
        start = self.st["pos"]
        if self.mp.is_playing():
            self.mp.pause()
        self.watching = True
        session = object()  # identifica esta tentativa; um clique novo invalida a anterior
        self.watch_session = session
        log.info("Assistir na TV: %s a partir de %ds", path, start)
        self._watchState.emit({"stage": "connect", "title": title})

        def alive():
            return self.watching and self.watch_session is session

        def work():
            try:
                info = tvfolder.probe(path) or {}
                if not self.box.connect(lambda m: self._watchState.emit({"stage": "wake", "title": title})):
                    raise RuntimeError("A Mi Box não respondeu. Ela está ligada na tomada?")
                self.box.wake_screen()
                if not self.box.ensure_tv(lambda m: self._watchState.emit({"stage": "tv", "title": title})):
                    log.warning("a TV não ligou; mandando mesmo assim")
                self._watchState.emit({"stage": "open", "title": title})
                cache = self.box.has_cache_app()
                for attempt in range(3):
                    # endereço novo a cada tentativa: num endereço que já viu, o VLC ignora a posição pedida
                    url = self.stream.share(path, self.box.ip)
                    self.box.play(url, title, start * 1000, info.get("bps") or 16e6)
                    if self.box.vlc_in_front():
                        break
                    log.warning("o VLC não apareceu na TV (tentativa %d); mandando de novo", attempt + 1)
                else:
                    raise RuntimeError("O VLC da TV não abriu o filme")
                base = {"stage": "playing", "title": title, "cache": cache, "dur": info.get("dur", 0), "pos": start}
                self._watchState.emit(dict(base, toast="Tocando na TV"))
                n, tv_off, box_off = 0, 0, 0
                while alive():
                    time.sleep(1)
                    n += 1
                    # TV ou Mi Box desligada? Primeiro, e rápido (1 s no máximo cada); 3 seguidas para não
                    # confundir com uma oscilação do Wi-Fi
                    box_up, tv_up = self.box._port_open(), self.box.tv_on()
                    box_off = 0 if box_up else box_off + 1
                    tv_off = 0 if tv_up else tv_off + 1
                    if tv_off >= 3 or box_off >= 3:
                        what = "A TV foi desligada" if not tv_up else "A Mi Box foi desligada"
                        self._watch_ended(f"{what} em {self._fmt(base['pos'])}", base["pos"])
                        return
                    if not box_up:
                        continue  # sem a Mi Box, o adb só ficaria esperando
                    data = dict(base)
                    if cache:
                        try:
                            st = self.box.status()
                            if n > 3 and st.get("src") and st["src"] != url:  # outra janela mandou outro vídeo
                                self._watch_ended("Outra janela do onVLC assumiu a TV", base["pos"])
                                return
                            data.update(st)
                        except Exception:
                            pass
                    if n % 2 == 0:
                        try:  # posição exata pela sessão de mídia do Android
                            tv_pos, tv_state = self.box.tv_position()
                            # parado (TV desligando, VLC fechando) a posição vem 0: guarda a última boa
                            if tv_pos is not None and tv_state in ("PLAYING", "PAUSED", "BUFFERING"):
                                base.update(pos=tv_pos, tvState=tv_state)
                        except Exception:
                            pass
                    data.update(pos=base["pos"], tvState=base.get("tvState", ""), dur=base["dur"])
                    if alive():
                        self._watchState.emit(data)
            except Exception as e:
                log.exception("Assistir na TV falhou")
                if self.watch_session is session:
                    self._watch_ended(str(e), start)
        threading.Thread(target=work, daemon=True).start()

    @Slot()
    def tvResumeHere(self):
        """Fecha o aviso do balão e continua no PC de onde a TV parou."""
        pos = (self.st.get("watch") or {}).get("pos")
        self._watchState.emit({"stage": "", "resume": pos})
        if self.path and pos is not None and self.mp.get_state() != vlc.State.Playing:
            self.mp.play()

    @Slot()
    def tvRetry(self):
        """Liga tudo de novo e continua na TV do mesmo ponto."""
        pos = (self.st.get("watch") or {}).get("pos")
        if pos is not None and self.path:
            self.seek(pos)
        self.watching = False
        self.tvWatch()

    @Slot()
    def tvWatchStop(self):
        if not self.watching:
            return
        self.watching = False
        last = self.st.get("watch", {}).get("pos", 0)
        self._watchState.emit({"stage": "stopping"})

        def work():
            pos = last
            try:
                tv_pos, _ = self.box.tv_position()
                if tv_pos:
                    pos = tv_pos
                self.box.stop()
            except Exception:
                pass
            self._watchState.emit({"stage": "", "resume": pos, "toast": "Parou na TV em " + self._fmt(pos)})
        threading.Thread(target=work, daemon=True).start()

    @Slot()
    def setDefaultApp(self):
        # abre Configurações → Aplicativos padrão já na página deste app (registrado pelo tools\build.py)
        os.startfile("ms-settings:defaultapps?registeredAppUser=onVLC")
        self.toast.emit('Clique em "Definir padrão" no topo da página')

    # ---------- comandos da interface ----------
    @Slot()
    def openFile(self):
        start = str(Path(self.path).parent) if self.path else str(Path.home() / "Videos")
        exts = " ".join("*" + e for e in sorted(VIDEO_EXTS))
        f, _ = self._dialog(lambda w: QFileDialog.getOpenFileName(
            w, "Abrir vídeo", start, f"Vídeos ({exts});;Todos os arquivos (*.*)"))
        if f:
            self.open_path(f)

    @Slot()
    def openFolder(self):
        d = self._dialog(lambda w: QFileDialog.getExistingDirectory(w, "Abrir pasta", str(Path.home() / "Videos")))
        if not d:
            return
        vids = sorted((str(p) for p in Path(d).iterdir() if p.suffix.lower() in VIDEO_EXTS), key=str.lower)
        if vids:
            self.open_path(vids[0], vids)
        else:
            self.toast.emit("Nenhum vídeo nessa pasta")

    @Slot(str)
    def openUrl(self, url):
        q = QUrl(url)
        self.open_path(q.toLocalFile() if q.isLocalFile() else url)

    @Slot(str)
    def openPath(self, path):
        self.open_path(path)

    @Slot()
    def togglePlay(self):
        if not self.path:
            return self.openFile()
        if self.mp.get_state() in (vlc.State.Ended, vlc.State.Stopped):
            self.open_path(self.path, self.playlist)
        else:
            self.mp.pause()

    @Slot(float)
    def seek(self, sec):
        if self.path:
            self.mp.set_time(int(max(0, sec) * 1000))
            self.st["pos"] = sec
            self.stateChanged.emit()

    @Slot(float)
    def skip(self, d):
        if self.path:
            self.seek(min(self.st["dur"] - 1, max(0, self.st["pos"] + d)))

    @Slot(int)
    def next(self, d):
        i = self.index + d
        if 0 <= i < len(self.playlist):
            self.open_path(self.playlist[i], self.playlist)

    @Slot(int)
    def setVolume(self, v):
        self.vol = max(0, min(self.vol_max, int(v)))
        self.muted = self.vol == 0
        self.tick()

    @Slot()
    def toggleMute(self):
        self.muted = not self.muted
        self.tick()

    @Slot()
    def toggleBoost(self):
        self.vol_max = 200 if self.vol_max == 100 else 100
        self.vol = min(self.vol, self.vol_max)
        self.toast.emit("Volume até " + str(self.vol_max) + "%")
        self.tick()

    @Slot(float)
    def setRate(self, r):
        self.mp.set_rate(r)
        self.tick()

    @Slot(int)
    def stepRate(self, d):
        cur = round(self.mp.get_rate() or 1.0, 2)
        i = min(range(len(RATES)), key=lambda k: abs(RATES[k] - cur))
        self.setRate(RATES[max(0, min(len(RATES) - 1, i + d))])

    @Slot(int)
    def setAudioTrack(self, i):
        self.mp.audio_set_track(i)
        self._refresh_tracks()
        self.stateChanged.emit()

    @Slot(int)
    def setSubTrack(self, i):
        self.mp.video_set_spu(i)
        self._refresh_tracks()
        self.stateChanged.emit()

    @Slot(float)
    def shiftSubDelay(self, d):
        cur = self.mp.video_get_spu_delay()
        self.mp.video_set_spu_delay(int(cur + d * 1_000_000))
        self._refresh_tracks()
        self.toast.emit("Atraso da legenda: " + f"{self.st['subDelay']:+.1f} s".replace(".", ","))
        self.stateChanged.emit()

    @Slot()
    def toggleRepeat(self):
        self.repeat = not self.repeat
        self.tick()

    @Slot()
    def toggleResume(self):
        self.resume = not self.resume
        self.settings.setValue("resume", "true" if self.resume else "false")
        self.tick()

    @Slot()
    def cycleAspect(self):
        self.aspect_i = (self.aspect_i + 1) % len(ASPECTS)
        self.mp.video_set_aspect_ratio(ASPECTS[self.aspect_i])
        self.tick()

    @Slot()
    def snapshot(self):
        if not self.path:
            return
        d = Path.home() / "Pictures" / "Player"
        d.mkdir(parents=True, exist_ok=True)
        ok = self.mp.video_take_snapshot(0, str(d) + os.sep, 0, 0)
        self.toast.emit("Imagem salva em Imagens\\Player" if ok == 0 else "Não consegui capturar a imagem")

    @Slot()
    def mediaInfo(self):
        if not self.path:
            return
        w, h = self.mp.video_get_size(0) or (0, 0)
        size = os.path.getsize(self.path) / 1024 ** 3
        self.toast.emit(f"{Path(self.path).name} · {w}×{h} · {size:.2f} GB".replace(".", ",", 1) if w else Path(self.path).name)

    @Slot()
    def openInVlc(self):
        exe = os.path.join(VLC_DIR, "vlc.exe")
        if self.path and os.path.exists(exe):
            self.mp.pause() if self.mp.is_playing() else None
            subprocess.Popen([exe, "--start-time", str(int(self.st["pos"])), self.path])

    @Slot()
    def toggleOnTop(self):
        self.on_top = not self.on_top
        hwnd = int(self.win.winId())
        # HWND_TOPMOST = -1, HWND_NOTOPMOST = -2; SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
        ctypes.windll.user32.SetWindowPos(hwnd, -1 if self.on_top else -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        self.tick()

    @Slot()
    def toggleFullscreen(self):
        if self.win.isFullScreen():
            self.win.showMaximized() if self.st.get("wasMax") else self.win.showNormal()
        else:
            self.st["wasMax"] = self.win.isMaximized()
            self.win.showFullScreen()

    @Slot()
    def minimize(self):
        self.win.showMinimized()

    @Slot()
    def toggleMaximize(self):
        if self.win.isFullScreen():
            return self.toggleFullscreen()
        self.win.showNormal() if self.win.isMaximized() else self.win.showMaximized()

    @Slot()
    def close(self):
        self.win.close()

    @Slot()
    def startMove(self):
        if not (self.win.isMaximized() or self.win.isFullScreen()):
            self.win.windowHandle().startSystemMove()

    @Slot(int)
    def startResize(self, edges):
        if not (self.win.isMaximized() or self.win.isFullScreen()):
            self.win.windowHandle().startSystemResize(Qt.Edge(edges))


def setup_log():
    """Registro em %LOCALAPPDATA%\\onVLC\\onvlc.log (até 1 MB, guarda os 2 anteriores)."""
    from logging.handlers import RotatingFileHandler
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    h = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s [pid %(process)d]: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[h])
    sys.excepthook = lambda t, v, tb: log.critical("erro não tratado", exc_info=(t, v, tb))
    log.info("onVLC aberto: %s", " ".join(sys.argv[1:]))


def main():
    setup_log()
    QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.OpenGL)
    app = QApplication(sys.argv)
    app.setApplicationName("onVLC")
    app.setWindowIcon(QIcon(str(HERE / "ui" / "icon.ico")))
    # agrupa na barra de tarefas com o ícone do app, e não com o do Python
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("PlayerLucas.Player")
    player = Player(app)
    player.show()
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        player.open_path(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
