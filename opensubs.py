"""Busca e download de legendas no OpenSubtitles (API REST v1).

Precisa de uma chave de API (opensubtitles.com/consumers). Buscar funciona só com a chave;
baixar usa o login da conta, que dá mais downloads por dia.
"""
import json
import os
import re
import struct
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.opensubtitles.com/api/v1"
USER_AGENT = "onVLC v1.0"
LANG_ORDER = {"pt-br": 0, "pt-pt": 1, "en": 2}


class OSError_(Exception):
    """Erro com mensagem pronta para mostrar na tela."""


def movie_hash(path):
    """Impressão digital do arquivo no formato do OpenSubtitles (tamanho + primeiros e últimos 64 KB)."""
    size = os.path.getsize(path)
    if size < 131072:
        return None
    h = size
    with open(path, "rb") as f:
        for off in (0, size - 65536):
            f.seek(off)
            h += sum(struct.unpack("<8192Q", f.read(65536)))
    return f"{h & 0xFFFFFFFFFFFFFFFF:016x}"


def clean_query(stem):
    """'Filme.2024.1080p.WEB-DL.x264' -> 'filme 2024'"""
    q = re.sub(r"[._]+", " ", stem)
    q = re.split(r"\b(1080p|720p|2160p|4k|web|webrip|web-dl|bluray|brrip|hdtv|x264|x265|h264|hevc|dual|dublado)\b", q, flags=re.I)[0]
    return re.sub(r"\s+", " ", q).strip().lower()


class Client:
    def __init__(self, api_key, user="", password=""):
        self.api_key = api_key
        self.user = user
        self.password = password
        self.token = None

    def _req(self, method, path, body=None, auth=False):
        headers = {"Api-Key": self.api_key, "User-Agent": USER_AGENT, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if auth and self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(API + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise OSError_("Chave de API, usuário ou senha errados")
            if e.code in (406, 429):
                raise OSError_("Limite de downloads de hoje acabou")
            raise OSError_(f"O OpenSubtitles respondeu com erro {e.code}")
        except (urllib.error.URLError, TimeoutError):
            raise OSError_("Sem conexão com o OpenSubtitles")

    def search(self, path, stem):
        params = {"languages": "en,pt-br,pt-pt"}
        h = movie_hash(path)
        if h:
            params["moviehash"] = h
        q = clean_query(stem)
        if q:
            params["query"] = q
        # a API pede os parâmetros em ordem alfabética para não redirecionar
        data = self._req("GET", "/subtitles?" + urllib.parse.urlencode(sorted(params.items())))
        out = []
        for d in data.get("data", []):
            a = d.get("attributes", {})
            files = a.get("files") or []
            if not files:
                continue
            out.append({
                "fileId": files[0]["file_id"],
                "name": a.get("release") or files[0].get("file_name") or "Sem nome",
                "lang": a.get("language", ""),
                "exact": bool(a.get("moviehash_match")),
                "downloads": a.get("download_count", 0),
            })
        out.sort(key=lambda r: (not r["exact"], LANG_ORDER.get(r["lang"], 9), -r["downloads"]))
        return out[:20]

    def download(self, file_id):
        """Devolve (conteúdo da legenda, downloads restantes hoje)."""
        if self.user and self.password and not self.token:
            self.token = self._req("POST", "/login", {"username": self.user, "password": self.password}).get("token")
        info = self._req("POST", "/download", {"file_id": file_id}, auth=True)
        link = info.get("link")
        if not link:
            raise OSError_(info.get("message") or "Não consegui baixar essa legenda")
        if urllib.parse.urlparse(link).scheme != "https":  # a API devolve o endereço; só https (nunca file: ou http:)
            raise OSError_("O OpenSubtitles devolveu um endereço estranho para a legenda")
        try:
            with urllib.request.urlopen(urllib.request.Request(link, headers={"User-Agent": USER_AGENT}), timeout=30) as r:
                return r.read(), info.get("remaining")
        except (urllib.error.URLError, TimeoutError):
            raise OSError_("Sem conexão com o OpenSubtitles")
