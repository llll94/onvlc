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
import urllib.parse
import urllib.request
from pathlib import Path

# o motor do VLC: o que vai dentro do onVLC (pasta vlc ao lado do .exe); rodando o .py, o VLC instalado no PC
_OWN_VLC = Path(sys.executable).with_name("vlc")
VLC_DIR = (str(_OWN_VLC) if getattr(sys, "frozen", False) and (_OWN_VLC / "libvlc.dll").is_file()
           else r"C:\Program Files\VideoLAN\VLC")
os.environ.setdefault("PYTHON_VLC_MODULE_PATH", VLC_DIR)
os.environ.setdefault("PYTHON_VLC_LIB_PATH", os.path.join(VLC_DIR, "libvlc.dll"))
os.environ.setdefault("VLC_PLUGIN_PATH", os.path.join(VLC_DIR, "plugins"))

import vlc  # noqa: E402
from PySide6.QtCore import QEvent, QObject, Property, QSettings, Qt, QTimer, QUrl, Signal, Slot  # noqa: E402
from PySide6.QtGui import QColor, QIcon, QPalette, QSurfaceFormat  # noqa: E402
from PySide6.QtQuick import QQuickView, QQuickWindow, QSGRendererInterface  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QWidget  # noqa: E402

import library  # noqa: E402
import opensubs  # noqa: E402
import thumbs  # noqa: E402
import tvfolder  # noqa: E402
import tvsetup  # noqa: E402
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
    posChanged = Signal()  # só a posição (várias vezes por segundo, sem mandar o estado inteiro para a tela)
    toast = Signal(str)
    _vlcEvent = Signal(str)  # avisos do motor do VLC, que chegam de outra thread
    _mediaInfo = Signal(object)  # legendas e miniaturas que a biblioteca achou para o vídeo aberto
    _webResult = Signal(object)  # resposta do OpenSubtitles, vinda de outra thread
    _tvState = Signal(object)  # estado da pasta onVLC, vindo de outra thread
    _watchState = Signal(object)  # estado do Assistir na TV, vindo de outra thread
    libraryShow = Signal(bool)  # mostra ou esconde a biblioteca por cima do player

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.settings = QSettings("PlayerLucas", "Player")
        self.os_configured = bool(self._secret("api_key"))
        self.vlc = vlc.Instance(["--quiet", "--no-video-title-show", "--no-sub-autodetect-file"])
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
        self.thumbs = thumbs.Thumbs()  # só quando a biblioteca está desligada
        self.thumbs_for = None
        self.lib_thumbs = None  # {"path", "id", "n", "have"}: miniaturas feitas pela biblioteca
        self._mediaInfo.connect(self._on_media_info)
        self._webResult.connect(self._on_web)
        self.st = {"tv": {}}
        self._reset_media_state()

        self.tv = tvfolder.LibraryFolder(self._tvState.emit)
        self._tvState.connect(self._on_tv)
        self.tv.refresh_async()

        self.box = tvstream.TVBox()
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
        self.setup = tvsetup.Setup(self)  # assistente "Configurar a TV" (ui/Setup.qml)
        self.overlay.rootContext().setContextProperty("setup", self.setup)
        self.overlay.setSource(QUrl.fromLocalFile(str(HERE / "ui" / "Overlay.qml")))
        if self.overlay.status() == QQuickView.Error:
            for err in self.overlay.errors():
                print(err.toString(), file=sys.stderr)
            sys.exit(1)

        # a posição é lida só com o vídeo tocando e a janela à vista; o resto chega pelos avisos do VLC
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.tick)
        self._vlcEvent.connect(self._on_vlc)
        em = self.mp.event_manager()
        self._vlc_handlers = []
        for name in ("MediaPlayerPlaying", "MediaPlayerPaused", "MediaPlayerStopped", "MediaPlayerEndReached",
                     "MediaPlayerLengthChanged", "MediaPlayerESAdded", "MediaPlayerESDeleted", "MediaPlayerESSelected",
                     "MediaPlayerEncounteredError"):
            cb = (lambda n: lambda e: self._vlcEvent.emit(n))(name)
            self._vlc_handlers.append(cb)  # o python-vlc não guarda a função: sem isso ela some
            em.event_attach(getattr(vlc.EventType, name), cb)
        self._tracks_soon = QTimer(self)
        self._tracks_soon.setSingleShot(True)
        self._tracks_soon.setInterval(300)
        self._tracks_soon.timeout.connect(self._tracks_changed)

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
            self.timer.stop()
        else:
            if self.st.get("playing"):
                self.timer.start()
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
        self.box.close()  # fecha o servidor do adb, se foi usado: outro programa não herda a conexão com a TV
        self.overlay.close()
        self.app.quit()

    # ---------- estado para a interface ----------
    def _reset_media_state(self):
        self.st.update(hasMedia=False, playing=False, pos=0.0, dur=0.0, title="",
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

    def _get_pos(self):
        return float(self.st.get("pos", 0.0))

    pos = Property(float, _get_pos, notify=posChanged)

    def _changed(self):
        """Algo que a tela mostra mudou (volume, repetir, proporção…): manda o estado de novo."""
        self._refresh_static()
        self.stateChanged.emit()

    def tick(self):
        """Com o vídeo tocando: a posição, 4 vezes por segundo; a cada 5 s ela fica salva."""
        if not self.path:
            self.timer.stop()
            return
        pos = max(0, self.mp.get_time()) / 1000
        if pos != self.st["pos"]:
            self.st["pos"] = pos
            self.posChanged.emit()
        self.ticks += 1
        if self.ticks % 20 == 0:
            self._save_position()

    def _apply_volume(self):
        want = 0 if self.muted else self.vol
        if self.mp.audio_get_volume() != want:
            self.mp.audio_set_volume(want)

    def _tracks_changed(self):
        self._refresh_tracks()
        self.stateChanged.emit()

    def _on_vlc(self, name):
        """Avisos do motor do VLC (já na thread da tela)."""
        if not self.path:
            return
        mp = self.mp
        if name == "MediaPlayerEndReached":
            self._on_end()
            return
        if name in ("MediaPlayerPlaying", "MediaPlayerPaused", "MediaPlayerStopped", "MediaPlayerEncounteredError"):
            self.st["playing"] = name == "MediaPlayerPlaying"
            if self.st["playing"]:
                self._apply_volume()  # o VLC volta ao volume padrão a cada vídeo novo
                if not self.win.isMinimized():
                    self.timer.start()
            else:
                self.timer.stop()
                self.tick()
            self._refresh_tracks()
        elif name == "MediaPlayerLengthChanged":
            dur = mp.get_length() / 1000
            if dur <= 0:
                return
            self.st["dur"] = dur
            if self.thumbs_for != self.path:
                self.thumbs_for = self.path
                self._fetch_thumbs(self.path, dur)
            if self.pending_resume is not None:
                at = self.pending_resume
                self.pending_resume = None
                if 30 < at < dur - 60:
                    mp.set_time(int(at * 1000))
                    self.toast.emit("Continuando de " + self._fmt(at))
        else:  # faixas de áudio/legenda apareceram ou mudaram: junta vários avisos seguidos num só
            self._tracks_soon.start()
            return
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
        self.st["badge"] = bool(self.st["found"]) and self.st["subId"] == -1

    @Slot(float, result=str)
    def thumbAt(self, sec):
        lt = self.lib_thumbs
        if lt and lt["path"] == self.path:
            have, dur = lt["have"], self.st.get("dur") or 0
            if not have or dur <= 0:
                return ""
            want = max(0, min(lt["n"] - 1, int(sec / dur * lt["n"])))
            best = min(have, key=lambda i: abs(i - want))
            return f"http://127.0.0.1:8780/img/thumb_{lt['id']}_{best}.jpg"
        f = self.thumbs.at(sec)
        return QUrl.fromLocalFile(f).toString() if f else ""

    def _fetch_thumbs(self, path, dur):
        """A biblioteca faz as miniaturas (as mesmas que a TV usa). Pergunta de novo enquanto ela gera;
        5 vezes seguidas sem nenhuma nova, para. Biblioteca desligada: o player faz sozinho, como antes."""
        def work():
            still = 0
            while self.path == path:
                try:
                    q = urllib.parse.urlencode({"path": path, "dur": f"{dur:.1f}"})
                    d = library.get_json(f"/api/thumbs?{q}")
                except Exception:
                    if self.lib_thumbs is None and self.path == path:
                        log.info("miniaturas: biblioteca fora, o player faz")
                        self.thumbs.start(path, dur)
                    return
                before = len(self.lib_thumbs["have"]) if self.lib_thumbs else 0
                self.lib_thumbs = {"path": path, "id": d["id"], "n": d["n"] or 1, "have": d["have"]}
                if d["n"] and len(d["have"]) >= d["n"]:
                    return
                still = still + 1 if len(d["have"]) <= before else 0
                if still >= 5:
                    return
                time.sleep(4)
        threading.Thread(target=work, daemon=True).start()

    # ---------- legendas: as mesmas da TV ----------
    def _fetch_media(self, path, announce=False):
        """Pergunta à biblioteca quais legendas o vídeo tem (mesmas regras e nomes da TV), fora da thread da tela."""
        def work():
            try:
                q = urllib.parse.urlencode({"path": path})
                subs = library.get_json(f"/api/media?{q}").get("subs") or []
                found = [{"name": s["name"], "path": s["path"], "where": Path(s["path"]).name} for s in subs]
            except Exception:  # biblioteca desligada: procura aqui mesmo, como antes
                found = self._find_subs(path)
            self._mediaInfo.emit({"path": path, "found": found, "announce": announce})
        threading.Thread(target=work, daemon=True).start()

    def _on_media_info(self, d):
        if d["path"] != self.path:
            return
        self.st["found"] = d["found"]
        if d["announce"] and d["found"]:
            # como na TV: a legenda em português (a do Brasil primeiro, nunca a "só falas estrangeiras") entra ligada
            pts = [f for f in d["found"] if f["name"].startswith("Português") and "forced" not in f["where"].lower()]
            pt = next((f for f in pts if "br" in f["where"].lower()), pts[0] if pts else None)
            if pt:
                self.mp.add_slave(vlc.MediaSlaveType.subtitle, QUrl.fromLocalFile(pt["path"]).toString(), True)
                self._tracks_soon.start()
            n = len(d["found"])
            QTimer.singleShot(1500, lambda: self.toast.emit(
                ("Legenda em português ligada · " if pt else "") + f"{n} legenda{'s' if n > 1 else ''} na pasta do vídeo"))
        self.stateChanged.emit()

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
        self.st.update(hasMedia=True, title=Path(path).stem, found=[])
        self.lib_thumbs = None
        self._fetch_media(path, announce=True)
        self.posChanged.emit()
        nxt = self.playlist[self.index + 1] if 0 <= self.index < len(self.playlist) - 1 else ""
        prv = self.playlist[self.index - 1] if self.index > 0 else ""
        self.st.update(nextName=Path(nxt).stem if nxt else "", prevName=Path(prv).stem if prv else "")
        self.pending_resume = float(self.settings.value("pos/" + self._key(path), 0)) if self.resume else None
        self._add_recent(path)
        self.stateChanged.emit()
        self.libraryShow.emit(False)  # abriu um vídeo: a biblioteca sai da frente

    def _on_end(self):
        self.settings.remove("pos/" + self._key(self.path))
        self._tell_library_watched(self.path)
        if self.repeat:
            self.open_path(self.path, self.playlist)
        elif 0 <= self.index < len(self.playlist) - 1:
            self.open_path(self.playlist[self.index + 1], self.playlist)
        else:
            self.mp.stop()
            self.st.update(playing=False, pos=self.st["dur"])
            self.timer.stop()
            self.posChanged.emit()
            self.stateChanged.emit()

    @staticmethod
    def _tell_library_watched(path):
        """Vídeo da pasta da biblioteca visto até o fim aqui no PC: entra nos "assistidos" da TV também."""
        def send():
            try:
                library.request("/api/watched", {"path": path}, timeout=3).read()
            except Exception:
                pass  # biblioteca desligada: tudo bem
        threading.Thread(target=send, daemon=True).start()

    @staticmethod
    def _key(path):
        return hashlib.md5(path.lower().encode()).hexdigest()

    def _save_position(self):
        if self.path and self.st.get("dur", 0) > 0:
            self.settings.setValue("pos/" + self._key(self.path), self.st["pos"])
            self.settings.setValue("posAt/" + self._key(self.path), time.time())  # ordem do "Continuar assistindo" da TV

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
        """Só do Gerenciador de Credenciais do Windows (cifrado). Sem ele, não há segredo: nunca cai para o registro."""
        if not keyring:
            return ""
        try:
            return keyring.get_password("onVLC", name) or ""
        except Exception:
            log.exception("Gerenciador de Credenciais")
            return ""

    def _set_secret(self, name, value):
        if not keyring:
            raise RuntimeError("Sem acesso ao Gerenciador de Credenciais do Windows: a senha não foi guardada")
        keyring.set_password("onVLC", name, value)

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
        try:
            if key.strip():
                self._set_secret("api_key", key.strip())
            if password:
                self._set_secret("password", password)
        except Exception as e:
            self.toast.emit(str(e))
            return
        self.settings.setValue("osUser", user.strip())
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
                self._fetch_media(self.path)
                if r["remaining"] is not None:
                    n = r["remaining"]
                    QTimer.singleShot(2600, lambda: self.toast.emit(f"Restam {n} downloads de legenda hoje"))
        self.stateChanged.emit()

    def _save_sub(self, data, lang):
        """Salva ao lado do vídeo; se a pasta não deixar, salva em %APPDATA%\\onVLC\\legendas."""
        video = Path(self.path)
        tag = lang if re.fullmatch(r"[A-Za-z-]{2,8}", lang or "") else "sub"  # vem da internet: nada de \ ou /
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

    # ---------- biblioteca dentro do onVLC ----------
    @Slot()
    def closeVideo(self):
        """Voltou para a biblioteca: o filme fecha (como na TV). A posição fica salva para o "Continuar assistindo"."""
        if not self.path:
            return
        self._save_position()
        self.timer.stop()
        self.path = None  # antes do stop: o aviso "parou" do VLC chega e é ignorado
        self.mp.stop()
        self.playlist, self.index = [], -1
        self.lib_thumbs, self.thumbs_for = None, None
        self._reset_media_state()
        self.posChanged.emit()
        self.stateChanged.emit()

    @Slot(str, float)
    def libraryOpenVideo(self, path, pos):
        """A biblioteca pediu um vídeo. Abre aqui, no ponto pedido (0 = do começo)."""
        if not os.path.isfile(path):
            self.toast.emit("Esse vídeo não está mais na pasta onVLC")
            return
        self.open_path(path)
        self.pending_resume = pos if pos > 0 else None  # "do começo": ignora a posição salva

    # ---------- pasta onVLC (o que a TV mostra) ----------
    def _on_tv(self, data):
        msg = data.pop("toast", None)
        if data:
            self.st["tv"] = data
            self.stateChanged.emit()
        if msg:
            self.toast.emit(msg)

    @Slot()
    def tvRefresh(self):
        self.tv.refresh_async()

    @Slot()
    def tvSendCurrent(self):
        if self.path:
            self.toast.emit(self.tv.add(self.path))

    @Slot()
    def tvAddFiles(self):
        start = str(Path(self.path).parent) if self.path else str(Path.home() / "Videos")
        exts = " ".join("*" + e for e in sorted(VIDEO_EXTS))
        files, _ = self._dialog(lambda w: QFileDialog.getOpenFileNames(
            w, "Mandar para a TV", start, f"Vídeos ({exts})"))
        msg = ""
        for f in files:
            msg = self.tv.add(f)
        if len(files) > 1:
            msg = f"{len(files)} vídeos indo para a pasta onVLC"
        if msg:
            self.toast.emit(msg)

    @Slot(str)
    def tvDelete(self, path):
        name = Path(path).stem
        if self.tv.delete(path):
            self.toast.emit(name + " saiu da TV (está na Lixeira do PC)")
        else:
            self.toast.emit("Não consegui apagar " + name)

    @Slot(str)
    def tvPlay(self, path):
        if os.path.isfile(path):
            self.open_path(path)

    @Slot()
    def tvOpenFolder(self):
        folder = tvfolder.lib_dir()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))

    # ---------- Assistir na TV ----------
    def _on_watch(self, data):
        data["tv"] = self.box.name
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

    @staticmethod
    def _library_items():
        """Todos os vídeos que a biblioteca conhece (filmes e episódios), com id, caminho e posição."""
        lib = library.get_json("/api/library", timeout=3)
        return list(lib["movies"]) + [e for s in lib["shows"] for se in s["seasons"] for e in se["episodes"]]

    @staticmethod
    def _same_file(a, b):
        return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))

    def _library_id(self, path):
        """Id do vídeo na biblioteca: ele mesmo, se já está na pasta, ou o link/cópia com o mesmo nome."""
        try:
            items = self._library_items()
        except Exception:
            return None
        for x in items:
            if x.get("path") and self._same_file(x["path"], path):
                return x["id"]
        inside = str(tvfolder.lib_dir() / Path(path).name)
        for x in items:
            if x.get("path") and self._same_file(x["path"], inside) and not x.get("broken"):
                return x["id"]
        return None

    @Slot()
    def tvWatch(self):
        """Assistir na TV: põe o vídeo na biblioteca (se ainda não está), liga a caixa e a TV e pede para o
        app onVLC de lá tocar a partir daqui. Depois disso a TV cuida de tudo e manda a posição para a biblioteca."""
        if not self.path or self.watching:
            return
        path, title = self.path, Path(self.path).stem
        start = self.st["pos"]
        if self.mp.is_playing():
            self.mp.pause()
        self._save_position()
        self.watching = True
        session = object()  # identifica esta tentativa; um clique novo invalida a anterior
        self.watch_session = session
        log.info("Assistir na TV: %s a partir de %ds", path, start)
        self._watchState.emit({"stage": "connect", "title": title})

        def alive():
            return self.watching and self.watch_session is session

        def work():
            try:
                if not library.enabled():
                    raise RuntimeError("A biblioteca da TV está desligada. Ligue no menu da TV → Biblioteca da TV.")
                if not self.box.tv:
                    raise RuntimeError("Nenhuma TV ligada a este PC ainda. Configure em Configurações → TV.")
                # 1. o vídeo precisa estar na biblioteca (pasta onVLC): de fora, vai por link ou cópia
                vid = self._library_id(path)
                if not vid:
                    self._watchState.emit({"stage": "library", "title": title})
                    msg = self.tv.add(path)
                    log.info("Assistir na TV: %s", msg)
                    waited = 0
                    while alive() and not vid:
                        time.sleep(2)
                        waited += 2
                        copying = [j for j in self.tv.jobs if j["src"] == Path(path)]
                        if copying:
                            self._watchState.emit({"stage": "library", "title": title, "p": copying[0]["p"]})
                            waited = 0  # cópia de outro disco: espera o tempo que precisar
                        elif waited > 120:
                            raise RuntimeError("A biblioteca não leu o vídeo. Ela está aberta?")
                        vid = self._library_id(path)
                    if not alive():
                        return
                # 2. caixa e TV
                if not self.box.connect(lambda m: self._watchState.emit({"stage": "wake", "title": title})):
                    raise RuntimeError(f"A TV ({self.box.name}) não respondeu. Ela está ligada na tomada?")
                # o app da TV mais velho que o deste onVLC: o PC atualiza antes (ele faz o papel da loja)
                if tvsetup.tv_app_outdated(self.box):
                    self._watchState.emit({"stage": "update", "title": title})
                    ok, msg = tvsetup.install_apk(
                        self.box, lambda pct: self._watchState.emit({"stage": "update", "title": title, "p": pct}))
                    log.info("atualizar o app da TV: %s", msg)
                self.box.wake_screen()
                if not self.box.ensure_tv(lambda m: self._watchState.emit({"stage": "tv", "title": title})):
                    log.warning("a TV não ligou; mandando mesmo assim")
                # 3. o app onVLC toca
                self._watchState.emit({"stage": "open", "title": title})
                for attempt in range(3):
                    self.box.open_library(vid, start * 1000)
                    if self.box.app_in_front():
                        break
                    log.warning("o onVLC não apareceu na TV (tentativa %d); pedindo de novo", attempt + 1)
                else:
                    raise RuntimeError("O onVLC da TV não abriu")
                if alive():
                    self.watch_id = vid
                    self._watchState.emit({"stage": "playing", "title": title, "toast": "Tocando na TV"})
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
        """Para na TV e continua aqui de onde ela parou (a posição chega pela biblioteca)."""
        if not self.watching:
            return
        self.watching = False
        vid = getattr(self, "watch_id", None)
        self._watchState.emit({"stage": "stopping"})

        def work():
            pos = None
            try:
                self.box.stop_library()
                for _ in range(6):  # a TV manda a posição para a biblioteca logo depois de parar
                    time.sleep(1)
                    it = next((x for x in self._library_items() if x["id"] == vid), None)
                    if it and it.get("pos"):
                        pos = it["pos"]
                        break
            except Exception:
                log.exception("parar na TV")
            if pos is None:
                self._watchState.emit({"stage": "", "toast": "Parei a TV, mas não soube onde ela estava"})
            else:
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
            self.posChanged.emit()

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
        self._apply_volume()
        self._changed()

    @Slot()
    def toggleMute(self):
        self.muted = not self.muted
        self._apply_volume()
        self._changed()

    @Slot()
    def toggleBoost(self):
        self.vol_max = 200 if self.vol_max == 100 else 100
        self.vol = min(self.vol, self.vol_max)
        self._apply_volume()
        self.toast.emit("Volume até " + str(self.vol_max) + "%")
        self._changed()

    @Slot(float)
    def setRate(self, r):
        self.mp.set_rate(r)
        self._changed()

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
        self._changed()

    @Slot()
    def toggleResume(self):
        self.resume = not self.resume
        self.settings.setValue("resume", "true" if self.resume else "false")
        self._changed()

    @Slot()
    def cycleAspect(self):
        self.aspect_i = (self.aspect_i + 1) % len(ASPECTS)
        self.mp.video_set_aspect_ratio(ASPECTS[self.aspect_i])
        self._changed()

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
        self._changed()

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

    @Slot(result=bool)
    def libraryEnabled(self):
        """A biblioteca da TV está ligada (a pessoa não desligou)?"""
        return library.enabled()

    @Slot()
    def toggleLibrary(self):
        """Liga ou desliga a biblioteca da TV (a escolha vale até mudar de novo; ver server/pause.go)."""
        on = not library.enabled()
        threading.Thread(target=library.set_enabled, args=(on,), daemon=True).start()
        self.toast.emit("Biblioteca da TV ligada" if on else "Biblioteca da TV desligada: a TV não acha o PC até você ligar de novo")
        self.stateChanged.emit()

    @Slot(result=str)
    def libraryKey(self):
        """A chave da biblioteca, para a tela Library.qml pôr no cabeçalho dos pedidos."""
        return library.key()

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
    # avisos do Qt e da interface (erro num arquivo QML, imagem que não carregou) também vão para o registro
    from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    qlog = logging.getLogger("onvlc.qt")
    qInstallMessageHandler(lambda kind, ctx, msg: qlog.warning(msg) if kind != QtMsgType.QtDebugMsg else None)
    log.info("onVLC aberto: %s", " ".join(sys.argv[1:]))


def _start_library():
    try:
        import library
        library.start_detached()  # continua rodando depois que a janela fecha
    except Exception:
        log.exception("não consegui abrir a biblioteca da TV")


def main():
    setup_log()
    # o instalador chama estas duas, sem janela
    if "--registrar" in sys.argv or "--desregistrar" in sys.argv or "--apagar-dados" in sys.argv:
        import winsetup
        if "--registrar" in sys.argv:
            winsetup.register_all(Path(sys.executable))
        elif "--desregistrar" in sys.argv:
            winsetup.unregister()
        else:
            logging.shutdown()  # o registro fica dentro da pasta que vai ser apagada
            winsetup.wipe_data()
            return
        log.info("onVLC %s no Windows", "registrado" if "--registrar" in sys.argv else "desregistrado")
        return
    # a biblioteca (onvlc-biblioteca.exe) abre antes da janela, em paralelo: quando a tela pedir a lista, ela já responde
    threading.Thread(target=_start_library, daemon=True).start()
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
