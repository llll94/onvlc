"""Biblioteca da TV: os filmes e séries da pasta "Vídeos\\onVLC TV" do PC, mostrados na Mi Box.

Roda como um processo à parte (onVLC.exe --biblioteca), sem janela, com um ícone perto do relógio.
O onVLC abre esse processo quando inicia; fechar a janela do player não fecha a biblioteca.

- De 20 em 20 s olha a pasta. Cada vídeo novo passa pelo ffprobe (duração, qualidade, áudios, legendas;
  arquivo que não abre fica marcado como "com problema"), ganha um quadro do próprio filme e, com a chave
  do TMDB, título em português, sinopse, capa, fundo e logo. Tudo fica guardado em
  %LOCALAPPDATA%\\onVLC\\library, então só o que é novo é consultado.
- Servidor na porta 8780 (fixa, para a TV achar):
    /api/hello              quem é (a TV usa para achar o PC na rede)
    /api/library            a lista inteira, com a posição de cada vídeo
    /api/progress   (POST)  {id, pos, dur}: a TV conta onde parou
    /img/<arquivo>          capas, fundos, logos e quadros
    /tv/<arquivo>           a interface da TV (pasta tvui); /tv/api e /tv/img também valem
    /v/<id>/<tamanho>/<nome> o vídeo, com Range (o TV Cache baixa em blocos)
- A posição é a mesma do onVLC do PC (QSettings PlayerLucas/Player, "pos/<md5 do caminho>"):
  parou na TV, continua no PC, e o contrário.
"""
import hashlib
import json
import logging
import logging.handlers
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import thumbs
import tvfolder
import tvstream

try:
    import keyring
except ImportError:
    keyring = None

HERE = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
LIB_DIR = Path(os.environ.get("ONVLC_LIBRARY") or Path.home() / "Videos" / "onVLC TV")
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "onVLC" / "library"
IMG_DIR = DATA_DIR / "img"
INDEX = DATA_DIR / "index.json"
UI_DIR = HERE / "tvui"
PORT = 8780
SCAN_EVERY = 20
MIN_SIZE = 50 << 20  # menos que isso costuma ser "sample" ou trailer
VIDEO_EXTS = tvfolder.VIDEO_EXTS
TMDB = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p/"
log = logging.getLogger("onvlc.lib")

LANG = {"por": "Português", "pt": "Português", "pob": "Português", "eng": "Inglês", "en": "Inglês",
        "spa": "Espanhol", "es": "Espanhol", "fre": "Francês", "fra": "Francês", "fr": "Francês",
        "ita": "Italiano", "it": "Italiano", "ger": "Alemão", "deu": "Alemão", "de": "Alemão",
        "jpn": "Japonês", "ja": "Japonês", "kor": "Coreano", "ko": "Coreano", "rus": "Russo", "chi": "Chinês",
        "zho": "Chinês"}


# ---------- nome do arquivo ----------
SERIES_RE = re.compile(r"^(?P<show>.*?)[\s._-]*(?:[Ss](?P<s>\d{1,2})[\s._-]*[Ee](?P<e>\d{1,3})|(?P<s2>\d{1,2})x(?P<e2>\d{2}))")
# o ano mais à direita: "Blade Runner 2049 (2017)" → 2017
YEAR_RE = re.compile(r"^(?P<t>.+)[\s.\[(_-]+(?P<y>(?:19|20)\d\d)(?=$|[\s.\])_-])")
JUNK_RE = re.compile(r"\b(2160p|1080p|720p|480p|4k|uhd|blu-?ray|brrip|bdrip|web-?dl|web-?rip|hdtv|hdrip|dvdrip|"
                     r"camrip|cam|x26[45]|h\.?26[45]|hevc|10bit|hdr|atmos|truehd|multi|dublado|dual|legendado|"
                     r"remux|proper|repack|extended|imax)\b.*", re.I)


def _clean(s):
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = re.sub(r"[._]+", " ", s)
    s = JUNK_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip(" -([")


def parse_name(path):
    """Do nome do arquivo: título e ano (filme) ou série, temporada e episódio."""
    stem = Path(path).stem
    m = SERIES_RE.search(stem)
    if m:
        show = m.group("show") or Path(path).parent.name
        ym = YEAR_RE.match(show)
        year = int(ym.group("y")) if ym else None
        return {"kind": "episode", "title": _clean(ym.group("t") if ym else show), "year": year,
                "season": int(m.group("s") or m.group("s2")), "episode": int(m.group("e") or m.group("e2"))}
    m = YEAR_RE.match(stem)
    if m and int(m.group("y")) <= time.localtime().tm_year + 1:
        return {"kind": "movie", "title": _clean(m.group("t")), "year": int(m.group("y"))}
    return {"kind": "movie", "title": _clean(stem), "year": None}


# ---------- arquivo ----------
def probe(path):
    """Duração, qualidade, áudios e legendas; None se o arquivo não abre."""
    exe = tvfolder.find_tool("ffprobe")
    if not exe:
        return {}
    try:
        out = subprocess.run([exe, "-v", "error", "-show_entries",
                              "format=duration:stream=codec_type,height,width,channels,color_transfer"
                              ":stream_tags=language", "-of", "json", str(path)],
                             capture_output=True, timeout=60, creationflags=tvfolder.NO_WINDOW)
        data = json.loads(out.stdout or b"{}")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    dur = float(data.get("format", {}).get("duration") or 0)
    if not video or dur <= 0:
        return None
    h, w = int(video.get("height") or 0), int(video.get("width") or 0)
    q = "4K" if h >= 1600 or w >= 3200 else "1080p" if h >= 900 or w >= 1800 else "720p" if h >= 650 else "SD"
    if video.get("color_transfer") in ("smpte2084", "arib-std-b67"):
        q += " HDR"
    audio, subs = [], []
    for s in streams:
        lang = LANG.get(((s.get("tags") or {}).get("language") or "").lower(), "")
        if s.get("codec_type") == "audio":
            ch = {1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1"}.get(s.get("channels"), "")
            audio.append(" ".join(x for x in (lang or "Áudio", ch) if x))
        elif s.get("codec_type") == "subtitle":
            subs.append(lang or "Legenda")
    stem = Path(path).with_suffix("")
    for ext in (".srt", ".ass", ".ssa", ".vtt"):
        for f in stem.parent.glob(glob_escape(stem.name) + "*" + ext):
            tag = f.stem[len(stem.name):].strip(".").lower()
            subs.append(LANG.get(tag.split("-")[0], "Legenda") + " (arquivo)")
    return {"dur": dur, "quality": q, "audio": list(dict.fromkeys(audio)), "subs": list(dict.fromkeys(subs))}


def glob_escape(s):
    return re.sub(r"([\[\]*?])", r"[\1]", s)


def grab_frame(path, dur, out):
    """Um quadro do próprio vídeo (a 30%), usado quando o TMDB não tem imagem."""
    ffmpeg, ffprobe = tvfolder.find_tool("ffmpeg"), tvfolder.find_tool("ffprobe")
    if not ffmpeg or out.exists():
        return out.exists()
    vf = thumbs.HDR_VF.replace(f"w={thumbs.WIDTH}", "w=960") if thumbs._is_hdr(ffprobe, path) else "scale=960:-2"
    try:
        subprocess.run([ffmpeg, "-v", "error", "-y", "-skip_frame", "nokey", "-ss", f"{dur * 0.3:.0f}", "-i", str(path),
                        "-map", "0:v:0", "-frames:v", "1", "-vf", vf, "-q:v", "4", str(out)],
                       capture_output=True, timeout=60, creationflags=tvfolder.NO_WINDOW | thumbs.BELOW_NORMAL)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.exists()


# ---------- TMDB ----------
class Tmdb:
    def __init__(self):
        self.token = ""
        if keyring:
            try:
                self.token = keyring.get_password("onVLC", "tmdb_token") or ""
            except Exception:
                pass

    def get(self, path, **q):
        q = {k: v for k, v in q.items() if v is not None}
        req = urllib.request.Request(f"{TMDB}{path}?{urllib.parse.urlencode(q)}",
                                     headers={"Authorization": "Bearer " + self.token, "accept": "application/json"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 429:  # limite de pedidos: espera um pouco
                    time.sleep(2 + attempt * 2)
                    continue
                if e.code == 404:
                    return None
                raise
        return None

    def image(self, path, size):
        if not path:
            return ""
        name = f"{size}_{Path(path).name}"
        dst = IMG_DIR / name
        if not dst.exists():
            with urllib.request.urlopen(TMDB_IMG + size + path, timeout=30) as r:
                data = r.read()
            tmp = dst.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, dst)
        return name

    @staticmethod
    def _logo(images):
        # o TMDB marca o país: o logo de Portugal ("Regresso a casa") vem antes do brasileiro nos votos
        def rank(l):
            lang, country = l.get("iso_639_1"), l.get("iso_3166_1")
            group = 0 if (lang, country) == ("pt", "BR") else 1 if lang == "en" else 2 if lang is None else 9
            return group, -(l.get("vote_average") or 0)
        logos = sorted((l for l in (images or {}).get("logos", []) if rank(l)[0] < 9), key=rank)
        return logos[0]["file_path"] if logos else None

    def _search(self, kind, title, year):
        key = "primary_release_year" if kind == "movie" else "first_air_date_year"
        for q in ({"query": title, key: year}, {"query": title}):
            if q.get(key, 1) is None:
                continue
            res = (self.get(f"/search/{kind}", language="pt-BR", **q) or {}).get("results") or []
            if res:
                return res[0]["id"]
        return None

    def details(self, kind, title, year):
        tid = self._search(kind, title, year)
        if not tid:
            return None
        d = self.get(f"/{kind}/{tid}", language="pt-BR", append_to_response="images",
                     include_image_language="pt,en,null")
        if not d:
            return None
        date = d.get("release_date") or d.get("first_air_date") or ""
        runtime = d.get("runtime") or (d.get("episode_run_time") or [0])[0] or 0
        return {"tmdb": tid, "title": d.get("title") or d.get("name") or title, "year": int(date[:4]) if date[:4].isdigit() else year,
                "runtime": runtime, "genres": [g["name"] for g in d.get("genres", [])][:3],
                "vote": round(d.get("vote_average") or 0, 1), "overview": d.get("overview") or "",
                "poster": self.image(d.get("poster_path"), "w342"),
                "backdrop": self.image(d.get("backdrop_path"), "w1280"),
                "logo": self.image(self._logo(d.get("images")), "w500")}

    def season(self, tid, n):
        d = self.get(f"/tv/{tid}/season/{n}", language="pt-BR") or {}
        return {e["episode_number"]: {"name": e.get("name") or "", "overview": e.get("overview") or "",
                                      "runtime": e.get("runtime") or 0, "still": self.image(e.get("still_path"), "w300")}
                for e in d.get("episodes", [])}


# ---------- posição (a mesma do onVLC do PC) ----------
def _settings():
    from PySide6.QtCore import QSettings
    return QSettings("PlayerLucas", "Player")


def pos_key(path):
    return hashlib.md5(str(Path(path)).lower().encode()).hexdigest()


# ---------- biblioteca ----------
class Library:
    def __init__(self):
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        LIB_DIR.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.files = {}  # id → registro
        self.shows = {}  # chave da série → dados do TMDB + temporadas
        self.seen = {}  # caminho → (tamanho, mtime) da última olhada: só processa depois de parar de crescer
        self.busy = 0
        self.tmdb = Tmdb()
        try:
            data = json.loads(INDEX.read_text(encoding="utf-8"))
            self.files, self.shows = data.get("files", {}), data.get("shows", {})
        except (OSError, ValueError):
            pass

    def save(self):
        with self.lock:
            data = json.dumps({"files": self.files, "shows": self.shows}, ensure_ascii=False)
        tmp = INDEX.with_suffix(".tmp")
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, INDEX)

    @staticmethod
    def file_id(path, size):
        return hashlib.md5(f"{str(path).lower()}|{size}".encode()).hexdigest()[:16]

    def run(self):
        while True:
            try:
                self.scan()
            except Exception:
                log.exception("erro olhando a pasta")
            time.sleep(SCAN_EVERY)

    def scan(self):
        found = {}
        for root, _dirs, names in os.walk(LIB_DIR):
            for n in names:
                p = Path(root) / n
                if p.suffix.lower() not in VIDEO_EXTS:
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_size >= MIN_SIZE:
                    found[str(p)] = (st.st_size, int(st.st_mtime))
        with self.lock:
            known = {r["path"]: fid for fid, r in self.files.items()}
            gone = [fid for path, fid in known.items() if path not in found
                    or (self.files[fid]["size"], self.files[fid]["mtime"]) != found[path]]
            for fid in gone:
                del self.files[fid]
        todo = []
        for path, sig in found.items():
            if path in known and known[path] not in gone:
                continue
            # arquivo ainda chegando (cópia em andamento): espera o tamanho parar de mudar
            if self.seen.get(path) == sig and time.time() - sig[1] > 10:
                todo.append((path, sig))
            self.seen[path] = sig
        self.busy = len(todo)
        if gone:
            self._drop_empty_shows()
            self.save()
        retry = [fid for fid, r in self.files.items() if self.tmdb.token and not r.get("tmdbTried")]
        for path, sig in todo:
            self.add(path, sig)
            self.busy -= 1
        for fid in retry:  # vídeos guardados antes de existir a chave do TMDB
            r = self.files.get(fid)
            if r:
                self._tmdb(r)
                self.save()

    def add(self, path, sig):
        size, mtime = sig
        fid = self.file_id(path, size)
        r = {"id": fid, "path": path, "size": size, "mtime": mtime, "added": time.time(),
             "file": Path(path).name, **parse_name(path)}
        info = probe(path)
        if info is None:
            r["broken"] = True
            log.warning("arquivo com problema: %s", path)
        else:
            r.update(info)
            if info.get("dur") and grab_frame(path, info["dur"], IMG_DIR / f"frame_{fid}.jpg"):
                r["frame"] = f"frame_{fid}.jpg"
        self._tmdb(r)
        with self.lock:
            self.files[fid] = r
        self.save()
        log.info("na biblioteca: %s (%s)", r.get("name") or r["title"], Path(path).name)

    def _tmdb(self, r):
        if not self.tmdb.token:
            return
        r["tmdbTried"] = True
        try:
            if r["kind"] == "movie":
                d = self.tmdb.details("movie", r["title"], r.get("year"))
                if not d and Path(r["path"]).parent != LIB_DIR:  # tenta pelo nome da pasta do filme
                    p = parse_name(Path(r["path"]).parent.name + ".mkv")
                    d = self.tmdb.details("movie", p["title"], p.get("year") or r.get("year"))
                if d:
                    r["meta"] = d
            else:
                key = r["title"].lower()
                show = self.shows.get(key)
                if not show:
                    show = self.tmdb.details("tv", r["title"], r.get("year")) or {"title": r["title"]}
                    show["seasons"] = {}
                    with self.lock:
                        self.shows[key] = show
                season = str(r["season"])
                if show.get("tmdb") and season not in show["seasons"]:
                    show["seasons"][season] = {str(k): v for k, v in self.tmdb.season(show["tmdb"], r["season"]).items()}
                r["showKey"] = key
        except Exception:
            log.exception("TMDB falhou para %s", r["file"])
            r["tmdbTried"] = False  # tenta de novo na próxima volta

    def _drop_empty_shows(self):
        with self.lock:
            used = {r.get("showKey") for r in self.files.values()}
            for k in [k for k in self.shows if k not in used]:
                del self.shows[k]

    # ---------- o que a TV recebe ----------
    def listing(self):
        s = _settings()
        with self.lock:
            files = [dict(r) for r in self.files.values()]
            shows = {k: dict(v) for k, v in self.shows.items()}
        progress = []
        for r in files:
            k = pos_key(r["path"])
            pos = float(s.value("pos/" + k, 0) or 0)
            r["pos"] = pos if 30 < pos < r.get("dur", 0) - 60 else 0
            if r["pos"]:
                progress.append((float(s.value("posAt/" + k, 0) or 0), r["id"]))

        def movie(r):
            m = r.get("meta") or {}
            return {"id": r["id"], "title": m.get("title") or r["title"], "year": m.get("year") or r.get("year"),
                    "runtime": m.get("runtime") or round(r.get("dur", 0) / 60), "genres": m.get("genres", []),
                    "vote": m.get("vote", 0), "overview": m.get("overview", ""), "poster": m.get("poster", ""),
                    "backdrop": m.get("backdrop", ""), "logo": m.get("logo", ""), "frame": r.get("frame", ""),
                    **_common(r)}

        movies = [movie(r) for r in files if r["kind"] == "movie"]
        out_shows = {}
        for r in files:
            if r["kind"] != "episode":
                continue
            key = r.get("showKey") or r["title"].lower()
            sh = shows.get(key, {"title": r["title"]})
            o = out_shows.setdefault(key, {
                "id": "s_" + hashlib.md5(key.encode()).hexdigest()[:12], "title": sh.get("title") or r["title"],
                "year": sh.get("year") or r.get("year"), "genres": sh.get("genres", []), "vote": sh.get("vote", 0),
                "overview": sh.get("overview", ""), "poster": sh.get("poster", ""), "backdrop": sh.get("backdrop", ""),
                "logo": sh.get("logo", ""), "frame": r.get("frame", ""), "added": 0, "seasons": {}})
            ep = (sh.get("seasons", {}).get(str(r["season"])) or {}).get(str(r["episode"]), {})
            o["added"] = max(o["added"], r["added"])
            o["seasons"].setdefault(r["season"], []).append({
                "id": r["id"], "season": r["season"], "episode": r["episode"],
                "title": ep.get("name") or f"Episódio {r['episode']}", "overview": ep.get("overview", ""),
                "still": ep.get("still") or r.get("frame", ""), "runtime": ep.get("runtime") or round(r.get("dur", 0) / 60),
                **_common(r)})
        for o in out_shows.values():
            o["seasons"] = [{"n": n, "episodes": sorted(eps, key=lambda e: e["episode"])}
                            for n, eps in sorted(o["seasons"].items())]
        return {"pc": socket.gethostname(), "folder": str(LIB_DIR), "busy": self.busy,
                "tmdb": bool(self.tmdb.token),
                "movies": sorted(movies, key=lambda m: -m["added"]),
                "shows": sorted(out_shows.values(), key=lambda o: -o["added"]),
                "continue": [fid for _, fid in sorted(progress, reverse=True)]}

    def path_of(self, fid):
        with self.lock:
            r = self.files.get(fid)
        return r["path"] if r else None

    def set_progress(self, fid, pos, dur):
        path = self.path_of(fid)
        if not path:
            return False
        s = _settings()
        k = pos_key(path)
        if dur > 0 and pos > dur - 120:  # chegou nos créditos: assistido
            s.remove("pos/" + k)
            s.remove("posAt/" + k)
        elif pos > 0:
            s.setValue("pos/" + k, float(pos))
            s.setValue("posAt/" + k, time.time())
        s.sync()
        return True


def _common(r):
    return {"file": r["file"], "size": r["size"], "dur": r.get("dur", 0), "pos": r.get("pos", 0),
            "quality": r.get("quality", ""), "audio": r.get("audio", []), "subs": r.get("subs", []),
            "broken": bool(r.get("broken")), "added": r["added"],
            "bps": int(r["size"] * 8 / r["dur"]) if r.get("dur") else 0}


# ---------- servidor ----------
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".jpg": "image/jpeg", ".png": "image/png", ".svg": "image/svg+xml", ".json": "application/json"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    lib = None

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", cache="no-cache"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if path.startswith("/tv/api/") or path.startswith("/tv/img/"):
            path = path[3:]
        if path == "/api/hello":
            return self._send(200, {"app": "onvlc-library", "name": socket.gethostname(), "version": 1})
        if path == "/api/library":
            return self._send(200, self.lib.listing())
        m = re.match(r"^/img/([A-Za-z0-9_.-]+)$", path)
        if m:
            f = IMG_DIR / m.group(1)
            if f.is_file():
                return self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"),
                                  "public, max-age=31536000, immutable")
            return self._send(404, b"", "text/plain")
        m = re.match(r"^/v/([0-9a-f]+)/(\d+)/", path)
        if m:
            p = self.lib.path_of(m.group(1))
            if p and os.path.getsize(p) != int(m.group(2)):
                p = None
            return tvstream.send_file(self, p, self.command != "HEAD")
        if path in ("/", "/tv", "/tv/"):
            path = "/tv/index.html"
        m = re.match(r"^/tv/([A-Za-z0-9_.-]+)$", path)
        if m:
            f = UI_DIR / m.group(1)
            if f.is_file():
                return self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))
        self._send(404, b"", "text/plain")

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path.startswith("/tv/api/"):
            path = path[3:]
        n = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"ok": False})
        if path == "/api/progress":
            ok = self.lib.set_progress(str(body.get("id", "")), float(body.get("pos") or 0), float(body.get("dur") or 0))
            return self._send(200 if ok else 404, {"ok": ok})
        self._send(404, {"ok": False})


def running():
    """Já existe uma biblioteca respondendo neste PC?"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/hello", timeout=1) as r:
            return json.load(r).get("app") == "onvlc-library"
    except Exception:
        return False


def start_detached():
    """Abre a biblioteca como processo separado (sobrevive ao fechar o onVLC)."""
    if running():
        return
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--biblioteca"]
    else:
        exe = Path(sys.executable)
        exe = exe.with_name("pythonw.exe") if exe.with_name("pythonw.exe").exists() else exe
        cmd = [str(exe), str(Path(__file__).resolve().parent / "main.py"), "--biblioteca"]
    # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB
    flags = 0x00000008 | 0x00000200 | 0x01000000
    try:
        subprocess.Popen(cmd, creationflags=flags, close_fds=True)
    except OSError:
        subprocess.Popen(cmd, creationflags=0x00000008 | 0x00000200, close_fds=True)


def setup_log():
    path = DATA_DIR.parent / "biblioteca.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(path, maxBytes=1 << 20, backupCount=1, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(h)
    root.setLevel(logging.INFO)


def serve(lib):
    Handler.lib = lib
    httpd = tvstream._Server(("0.0.0.0", PORT), Handler)  # porta exclusiva: uma biblioteca só por PC
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main():
    setup_log()
    try:
        lib = Library()
        httpd = serve(lib)
    except OSError:
        log.info("biblioteca já está rodando; saindo")
        return
    log.info("biblioteca na porta %d, pasta %s", PORT, LIB_DIR)
    threading.Thread(target=lib.run, daemon=True).start()

    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    tray = QSystemTrayIcon(QIcon(str(HERE / "ui" / "icon.ico")))
    menu = QMenu()
    info = menu.addAction("Biblioteca da TV")
    info.setEnabled(False)
    menu.addAction("Abrir a pasta da biblioteca", lambda: os.startfile(LIB_DIR))
    menu.addSeparator()
    menu.addAction("Desligar a biblioteca da TV", app.quit)

    def refresh():
        with lib.lock:
            n_mov = sum(1 for r in lib.files.values() if r["kind"] == "movie")
            n_shows = len({r.get("showKey") or r["title"] for r in lib.files.values() if r["kind"] == "episode"})
        txt = f"{n_mov} filme{'s' if n_mov != 1 else ''}" + (f", {n_shows} série{'s' if n_shows != 1 else ''}" if n_shows else "")
        if lib.busy:
            txt += f" · lendo {lib.busy} novo{'s' if lib.busy > 1 else ''}"
        info.setText("Biblioteca da TV: " + txt)
        tray.setToolTip("onVLC · biblioteca da TV\n" + txt)

    from PySide6.QtCore import QTimer
    timer = QTimer()
    timer.timeout.connect(refresh)
    timer.start(3000)
    refresh()
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: menu.popup(tray.geometry().center()) if reason == QSystemTrayIcon.Trigger else None)
    tray.show()
    app.exec()
    httpd.shutdown()


if __name__ == "__main__":
    main()
