package main

// Rotas do assistente "Configurar a TV" (tvsetup.py no PC, modo teste no app da TV):
//
//	GET  /api/clients              (só do próprio PC) {ip: quando falou pela última vez}: o PC sabe que a TV o achou
//	POST /api/reload-keys          (só do próprio PC) relê a chave do TMDB e busca capas do que ficou sem
//	GET  /api/selftest             vídeo de teste de 10 s (barras, movimento e um tom), gerado uma vez com o ffmpeg
//	GET  /api/speed?mb=32          bytes para a TV medir a velocidade da rede
//	POST /api/selftest/result      a TV conta o que conseguiu (velocidade, tocou, 4K/HDR, canais de som)
//	GET  /api/selftest/result      (só do próprio PC) o último resultado, com "at" (segundos desde 1970)

import (
	"context"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

var (
	clientsMu sync.Mutex
	clients   = map[string]int64{}

	testMu     sync.Mutex
	testResult = map[string]any{}
	testClip   = filepath.Join(dataDir, "selftest.mp4")

	speedBlock = func() []byte { // 1 MB que não comprime (a rede não "engana" a medida)
		b := make([]byte, 1<<20)
		x := uint32(2463534242)
		for i := range b {
			x ^= x << 13
			x ^= x >> 17
			x ^= x << 5
			b[i] = byte(x)
		}
		return b
	}()
)

func hostOf(r *http.Request) string {
	host := r.RemoteAddr
	if i := strings.LastIndex(host, ":"); i >= 0 {
		host = host[:i]
	}
	return strings.Trim(host, "[]")
}

// noteClient: guarda quando cada aparelho da rede pediu algo (a TV pede a lista e o estado o tempo todo).
func noteClient(r *http.Request) {
	if local(r) {
		return
	}
	clientsMu.Lock()
	clients[hostOf(r)] = time.Now().Unix()
	clientsMu.Unlock()
}

// setupGet responde as rotas GET do assistente; false = não era uma delas.
func (s *server) setupGet(w http.ResponseWriter, r *http.Request, p string) bool {
	switch p {
	case "/api/clients":
		if !local(r) {
			w.WriteHeader(403)
			return true
		}
		clientsMu.Lock()
		out := make(map[string]int64, len(clients))
		for k, v := range clients {
			out[k] = v
		}
		clientsMu.Unlock()
		s.json(w, r, out, false)
	case "/api/selftest/result":
		if !local(r) {
			w.WriteHeader(403)
			return true
		}
		testMu.Lock()
		out := testResult
		testMu.Unlock()
		s.json(w, r, out, false)
	case "/api/selftest":
		if err := makeTestClip(); err != nil {
			log.Printf("vídeo de teste: %v", err)
			http.Error(w, "sem ffmpeg", 503)
			return true
		}
		w.Header().Set("Content-Type", "video/mp4")
		s.file(w, r, testClip, "no-cache")
	case "/api/speed":
		mb, _ := strconv.Atoi(r.URL.Query().Get("mb"))
		if mb <= 0 || mb > 256 {
			mb = 32
		}
		w.Header().Set("Content-Type", "application/octet-stream")
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("Content-Length", strconv.Itoa(mb<<20))
		if r.Method == "HEAD" {
			return true
		}
		for i := 0; i < mb; i++ {
			if _, err := w.Write(speedBlock); err != nil {
				break
			}
		}
	default:
		return false
	}
	return true
}

// setupPost responde as rotas POST do assistente; false = não era uma delas.
func (s *server) setupPost(w http.ResponseWriter, r *http.Request, p string) bool {
	switch p {
	case "/api/selftest/result":
		var body map[string]any
		if err := json.NewDecoder(io.LimitReader(r.Body, 64<<10)).Decode(&body); err != nil {
			w.WriteHeader(400)
			return true
		}
		body["at"] = time.Now().Unix()
		body["from"] = hostOf(r)
		testMu.Lock()
		testResult = body
		testMu.Unlock()
		log.Printf("teste da TV (%s): %v", hostOf(r), body)
	case "/api/reload-keys":
		if !local(r) {
			w.WriteHeader(403)
			return true
		}
		token := readCredential("tmdb_token", "onVLC")
		s.lib.mu.Lock()
		changed := token != s.lib.tmdb.token
		s.lib.tmdb.token = token
		for id := range s.lib.retry { // chave nova: tudo pode tentar de novo já
			delete(s.lib.retry, id)
		}
		s.lib.mu.Unlock()
		if changed && token != "" {
			log.Printf("chave do TMDB nova: buscando capas")
			go func() {
				s.lib.retryTmdb()
				s.lib.changed()
			}()
		}
	default:
		return false
	}
	w.Header().Set("Content-Type", "application/json")
	w.Write([]byte(`{"ok":true}`))
	return true
}

var clipMu sync.Mutex

// makeTestClip: 10 s em 1080p H.264 (toca em qualquer TV) com barras que se mexem, contador e um tom de 1 kHz.
func makeTestClip() error {
	clipMu.Lock()
	defer clipMu.Unlock()
	if st, err := os.Stat(testClip); err == nil && st.Size() > 0 {
		return nil
	}
	ff := findTool("ffmpeg")
	if ff == "" {
		return os.ErrNotExist
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	tmp := testClip + ".tmp.mp4"
	c := command(ctx, false, ff, "-y", "-v", "error",
		"-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30",
		"-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000",
		"-t", "10", "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
		"-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-shortest", tmp)
	if out, err := c.CombinedOutput(); err != nil {
		os.Remove(tmp)
		log.Printf("ffmpeg: %s", out)
		return err
	}
	return os.Rename(tmp, testClip)
}
