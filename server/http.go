package main

import (
	"bytes"
	"compress/gzip"
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"io"
	"log"
	"mime"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Rotas (as mesmas do library.py, para a TV não perceber a troca):
//
//	GET  /api/hello                 quem é (a TV usa para achar o PC na rede) e o MAC da placa (para a TV acordar o PC)
//	GET  /api/library               a lista inteira (com ETag: sem mudança, responde 304)
//	GET  /api/state                 só o que muda sozinho: versão do catálogo, posições, "continuar", "lendo"
//	GET  /api/media?path=           (só do próprio PC) um arquivo qualquer: id, posição, retomada, legendas
//	GET  /api/thumbs/<id>           começa as miniaturas e diz quais já existem
//	GET  /api/thumbs?path=&dur=     (só do próprio PC) o mesmo para um arquivo qualquer: {id, n, have}
//	GET  /img/<arquivo>             capas, fundos, logos, quadros; /img/thumb_<id>_<n>.jpg
//	GET  /sub/<id>/<n>              legenda em arquivo
//	GET  /v/<id>/<tamanho>/<nome>   o vídeo, com Range
//	GET  /tv/<arquivo>              a página da TV; /tv/api/* e /tv/img/* também valem
//	POST /api/progress {id,pos,dur} onde parou (nos créditos vira "assistido")
//	POST /api/watched  {id|path}    o player do PC chegou ao fim
//	(e as do assistente "Configurar a TV", em setup.go)
//
// Quem pode chamar (auth.go): tudo exige o cabeçalho X-onVLC-Key, menos /api/hello, /tv/* e /img/*. As rotas
// "só do próprio PC" exigem a chave e vir de 127.0.0.1. Host e origem são conferidos em todo pedido.
type server struct {
	lib *Library
	ui  string
}

var (
	subRe    = regexp.MustCompile(`^/sub/([0-9a-f]+)/(\d+)$`)
	thumbsRe = regexp.MustCompile(`^/api/thumbs/([0-9a-f]+)$`)
	thumbRe  = regexp.MustCompile(`^/img/thumb_([0-9a-f]+)_(\d+)\.jpg$`)
	imgRe    = regexp.MustCompile(`^/img/([A-Za-z0-9_.-]+)$`)
	videoRe  = regexp.MustCompile(`^/v/([0-9a-f]+)/(\d+)/`)
	uiRe     = regexp.MustCompile(`^/tv/([A-Za-z0-9_.-]+)$`)
)

func (s *server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	defer func() { // um erro num pedido vira 500 no log, e não derruba a biblioteca
		if e := recover(); e != nil && e != http.ErrAbortHandler {
			log.Printf("pedido %s %s: %v", r.Method, r.URL.Path, e)
			w.WriteHeader(500)
		}
	}()
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if !privateSource(r) || !hostOK(r) {
		w.WriteHeader(403)
		return
	}
	p := r.URL.Path
	if strings.HasPrefix(p, "/tv/api/") || strings.HasPrefix(p, "/tv/img/") || strings.HasPrefix(p, "/tv/sub/") {
		p = p[3:]
	}
	if r.Method == "OPTIONS" {
		w.WriteHeader(204)
		return
	}
	if r.Method != "GET" && r.Method != "HEAD" && r.Method != "POST" {
		w.WriteHeader(405)
		return
	}
	open := p == "/api/hello" || strings.HasPrefix(p, "/tv/") || p == "/" || p == "/tv" || strings.HasPrefix(p, "/img/")
	if !open && !hasKey(r) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(401)
		w.Write([]byte(`{"error":"sem chave: configure a TV pelo onVLC do PC"}`))
		return
	}
	if !open {
		noteClient(r)
	}
	if r.Method == "POST" {
		s.post(w, r, p)
		return
	}
	if s.setupGet(w, r, p) {
		return
	}
	switch p {
	case "/api/hello":
		host, _ := os.Hostname()
		out := map[string]any{"app": "onvlc-library", "name": host, "version": 3, "mac": macOf(r)}
		if nonce := r.URL.Query().Get("nonce"); nonce != "" && len(nonce) <= 128 {
			out["auth"] = hmacHex("hello:" + nonce) // a TV confere: só o PC de verdade sabe o segredo
		}
		s.json(w, r, out, false)
		return
	case "/api/library":
		s.json(w, r, s.lib.listing(local(r)), true)
		return
	case "/api/state":
		s.json(w, r, s.lib.state(), true)
		return
	case "/api/media":
		if !local(r) {
			w.WriteHeader(403)
			return
		}
		path, err := localPath(r.URL.Query().Get("path"))
		if err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		s.json(w, r, s.lib.media(path), false)
		return
	case "/api/thumbs":
		if !local(r) {
			w.WriteHeader(403)
			return
		}
		path, err := localPath(r.URL.Query().Get("path"))
		if err != nil {
			http.Error(w, err.Error(), 400)
			return
		}
		dur, _ := strconv.ParseFloat(r.URL.Query().Get("dur"), 64)
		s.json(w, r, s.lib.pathThumbs(path, dur), false)
		return
	case "/", "/tv", "/tv/":
		p = "/tv/index.html"
	}
	if m := subRe.FindStringSubmatch(p); m != nil {
		rec := s.lib.byID(m[1])
		n, _ := strconv.Atoi(m[2])
		if rec != nil && n < len(rec.ExtSubs) {
			// só arquivo comum e pequeno: um atalho (symlink) com nome .srt apontando para outro arquivo, ou um
			// "legenda" de gigabytes, não é servido
			sp := rec.ExtSubs[n].Path
			if st, err := os.Lstat(sp); err == nil && st.Mode().IsRegular() && st.Size() <= maxSubSize {
				if data, err := os.ReadFile(sp); err == nil {
					s.bytes(w, r, data, "text/plain; charset=utf-8", "no-cache")
					return
				}
			}
		}
		http.NotFound(w, r)
		return
	}
	if m := thumbsRe.FindStringSubmatch(p); m != nil {
		rec := s.lib.byID(m[1])
		if rec == nil || rec.Dur <= 0 {
			s.json(w, r, map[string]any{"n": 0, "have": []int{}}, false)
			return
		}
		n, have := s.lib.thumbs.start(rec.ID, rec.Path, rec.Dur, rec.HDR)
		s.json(w, r, map[string]any{"n": n, "have": have}, false)
		return
	}
	if m := thumbRe.FindStringSubmatch(p); m != nil {
		i, _ := strconv.Atoi(m[2])
		if f := s.lib.thumbs.file(m[1], i); f != "" {
			s.file(w, r, f, "public, max-age=86400")
			return
		}
		http.NotFound(w, r)
		return
	}
	if m := imgRe.FindStringSubmatch(p); m != nil {
		s.file(w, r, filepath.Join(imgDir, m[1]), "public, max-age=31536000, immutable")
		return
	}
	if m := videoRe.FindStringSubmatch(p); m != nil {
		rec := s.lib.byID(m[1])
		size, _ := strconv.ParseInt(m[2], 10, 64)
		f, err := os.Open(func() string {
			if rec != nil {
				return rec.Path
			}
			return ""
		}())
		if err != nil {
			http.NotFound(w, r)
			return
		}
		defer f.Close()
		st, err := f.Stat()
		if err != nil || st.Size() != size { // outro arquivo com o mesmo id: nunca manda pedaço errado
			http.NotFound(w, r)
			return
		}
		ctype := "video/mp4"
		if strings.EqualFold(filepath.Ext(rec.Path), ".mkv") {
			ctype = "video/x-matroska"
		}
		w.Header().Set("Content-Type", ctype)
		// Range, 416, HEAD; o Windows manda o arquivo direto para a rede (TransmitFile)
		http.ServeContent(w, r, "", time.Time{}, f)
		return
	}
	if m := uiRe.FindStringSubmatch(p); m != nil {
		s.file(w, r, filepath.Join(s.ui, m[1]), "no-cache")
		return
	}
	http.NotFound(w, r)
}

func (s *server) post(w http.ResponseWriter, r *http.Request, p string) {
	if s.setupPost(w, r, p) {
		return
	}
	var body struct {
		ID   string  `json:"id"`
		Path string  `json:"path"`
		Pos  float64 `json:"pos"`
		Dur  float64 `json:"dur"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body); err != nil {
		w.WriteHeader(400)
		return
	}
	ok := false
	switch p {
	case "/api/progress":
		ok = s.lib.setProgress(body.ID, body.Pos, body.Dur)
	case "/api/watched": // o player do PC chegou ao fim de um vídeo da biblioteca
		rec := s.lib.byID(body.ID)
		if rec == nil && body.Path != "" {
			rec = s.lib.byPath(body.Path)
		}
		if rec != nil {
			s.lib.markWatched(rec, time.Now())
			ok = true
		}
	default:
		w.WriteHeader(404)
		return
	}
	code := 200
	if !ok {
		code = 404
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	w.Write([]byte(`{"ok":` + strconv.FormatBool(ok) + `}`))
}

// macOf: o MAC da placa de rede por onde o pedido chegou (a da rede de casa, a mesma que a TV usa para acordar o PC).
func macOf(r *http.Request) string {
	la, ok := r.Context().Value(http.LocalAddrContextKey).(*net.TCPAddr)
	if !ok {
		return ""
	}
	ifaces, _ := net.Interfaces()
	for _, ifc := range ifaces {
		addrs, _ := ifc.Addrs()
		for _, a := range addrs {
			if n, ok := a.(*net.IPNet); ok && n.IP.Equal(la.IP) {
				return ifc.HardwareAddr.String()
			}
		}
	}
	return ""
}

func local(r *http.Request) bool {
	host := r.RemoteAddr
	if i := strings.LastIndex(host, ":"); i >= 0 {
		host = host[:i]
	}
	return host == "127.0.0.1"
}

const maxSubSize = 10 << 20

// json responde com ETag (sem mudança: 304) e gzip quando o outro lado aceita.
func (s *server) json(w http.ResponseWriter, r *http.Request, v any, etag bool) {
	data, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	if etag {
		h := sha1.Sum(data)
		tag := `"` + hex.EncodeToString(h[:10]) + `"`
		w.Header().Set("ETag", tag)
		if r.Header.Get("If-None-Match") == tag {
			w.WriteHeader(304)
			return
		}
	}
	s.bytes(w, r, data, "application/json", "no-cache")
}

func (s *server) bytes(w http.ResponseWriter, r *http.Request, data []byte, ctype, cache string) {
	w.Header().Set("Content-Type", ctype)
	w.Header().Set("Cache-Control", cache)
	if len(data) > 1024 && strings.Contains(r.Header.Get("Accept-Encoding"), "gzip") {
		var b bytes.Buffer
		z, _ := gzip.NewWriterLevel(&b, gzip.BestSpeed)
		z.Write(data)
		z.Close()
		data = b.Bytes()
		w.Header().Set("Content-Encoding", "gzip")
		w.Header().Set("Vary", "Accept-Encoding")
	}
	w.Header().Set("Content-Length", strconv.Itoa(len(data)))
	if r.Method != "HEAD" {
		w.Write(data)
	}
}

var compressible = map[string]bool{".html": true, ".js": true, ".css": true, ".svg": true, ".json": true}

// file manda um arquivo do disco; texto vai com gzip, e com ETag (a página da TV só baixa de novo se mudou).
func (s *server) file(w http.ResponseWriter, r *http.Request, path, cache string) {
	st, err := os.Stat(path)
	if err != nil || st.IsDir() {
		http.NotFound(w, r)
		return
	}
	ext := strings.ToLower(filepath.Ext(path))
	ctype := mime.TypeByExtension(ext)
	switch ext {
	case ".js":
		ctype = "text/javascript; charset=utf-8"
	case ".html":
		ctype = "text/html; charset=utf-8"
	case ".css":
		ctype = "text/css; charset=utf-8"
	}
	tag := `"` + strconv.FormatInt(st.ModTime().UnixNano(), 36) + "-" + strconv.FormatInt(st.Size(), 36) + `"`
	w.Header().Set("ETag", tag)
	if r.Header.Get("If-None-Match") == tag {
		w.WriteHeader(304)
		return
	}
	if !compressible[ext] { // imagens e vídeo: direto do disco, com Range, sem carregar o arquivo na memória
		f, err := os.Open(path)
		if err != nil {
			http.NotFound(w, r)
			return
		}
		defer f.Close()
		w.Header().Set("Content-Type", ctype)
		w.Header().Set("Cache-Control", cache)
		http.ServeContent(w, r, "", st.ModTime(), f)
		return
	}
	if st.Size() > 4<<20 {
		http.Error(w, "arquivo grande demais", 500)
		return
	}
	data, err := os.ReadFile(path)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	s.bytes(w, r, data, ctype, cache)
}

// ---------- estado pequeno e arquivos do PC ----------

func (l *Library) state() map[string]any {
	out := l.listing(false)
	pos := map[string]float64{}
	for _, m := range out.Movies {
		if m.Pos > 0 {
			pos[m.ID] = m.Pos
		}
	}
	for _, sh := range out.Shows {
		for _, se := range sh.Seasons {
			for _, e := range se.Episodes {
				if e.Pos > 0 {
					pos[e.ID] = e.Pos
				}
			}
		}
	}
	h := out.History
	if len(h) > 10 {
		h = h[:10]
	}
	return map[string]any{"version": out.Version, "busy": out.Busy, "continue": out.Continue, "pos": pos, "history": h}
}

// pathThumbs: miniaturas de um arquivo qualquer que o player do PC abriu. Da biblioteca, usa o mesmo id da TV;
// de fora, um id pelo caminho e um ffprobe para saber se é HDR (guardado: só na primeira vez).
func (l *Library) pathThumbs(path string, dur float64) map[string]any {
	path = filepath.Clean(path)
	id, hdr := "", false
	if rec := l.byPath(path); rec != nil {
		id, hdr = rec.ID, rec.HDR
		if dur <= 0 {
			dur = rec.Dur
		}
	} else {
		id = hmacHex("thumb:" + strings.ToLower(path))[:16] // só quem pediu sabe o nome; outro aparelho não adivinha
		hdrMu.Lock()
		h, ok := hdrOf[path]
		hdrMu.Unlock()
		if !ok {
			if info := probe(path); info != nil {
				h = info.HDR
				if dur <= 0 {
					dur = info.Dur
				}
			}
			hdrMu.Lock()
			hdrOf[path] = h
			hdrMu.Unlock()
		}
		hdr = h
	}
	n, have := l.thumbs.start(id, path, dur, hdr)
	return map[string]any{"id": id, "n": n, "have": have}
}

var (
	hdrMu sync.Mutex
	hdrOf = map[string]bool{}
)

// media: o que o player do PC precisa de um arquivo qualquer (dentro ou fora da pasta da biblioteca).
func (l *Library) media(path string) map[string]any {
	path = filepath.Clean(path)
	pos, _ := positions.get(path)
	out := map[string]any{"path": path, "pos": pos, "subs": findSubs(path)}
	if rec := l.byPath(path); rec != nil {
		out["id"], out["dur"] = rec.ID, rec.Dur
		out["resume"] = resumeAt(pos, rec.Dur)
	}
	return out
}
