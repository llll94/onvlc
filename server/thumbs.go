package main

import (
	"context"
	"os"
	"path/filepath"
	"runtime/debug"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// Miniaturas da linha do tempo: ao abrir um vídeo, o ffmpeg tira um quadro pequeno de cada trecho em segundo
// plano (só quadros-chave, ~0,4 s cada mesmo num 4K), do grosso para o fino: primeiro quadros espalhados pelo
// filme inteiro, depois os do meio. Mesma pasta e mesmos nomes do thumbs.py.
const (
	thumbCount   = 200 // quadros por vídeo (um a cada ~40 s num filme de 2 h)
	thumbWidth   = 240
	thumbKeep    = 60 // vídeos guardados; os mais antigos saem
	thumbWorkers = 2
)

type thumbs struct {
	mu   sync.Mutex
	id   string // vídeo atual
	dir  string
	n    int
	have map[int]bool
	stop context.CancelFunc
}

// order: índices do grosso para o fino: 0, n/2, n/4, 3n/4, ...
func order(n int) []int {
	out, seen, step := []int{}, map[int]bool{}, 1
	for step*2 < n {
		step *= 2
	}
	for ; step >= 1; step /= 2 {
		for i := 0; i < n; i += step {
			if !seen[i] {
				seen[i] = true
				out = append(out, i)
			}
		}
	}
	return out
}

// start começa (ou continua) as miniaturas de um vídeo; devolve quantas serão e quais já existem.
func (t *thumbs) start(r *record) (int, []int) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.id != r.ID {
		if t.stop != nil {
			t.stop()
		}
		t.id, t.dir, t.n, t.have = r.ID, "", 0, map[int]bool{}
		ffmpeg := findTool("ffmpeg")
		st, err := os.Stat(r.Path)
		if ffmpeg != "" && err == nil && r.Dur > 0 {
			key := md5hex(strings.ToLower(r.Path) + "|" + strconv.FormatInt(st.Size(), 10) + "|" + strconv.FormatInt(st.ModTime().Unix(), 10))
			t.dir = filepath.Join(thumbDir, key)
			os.MkdirAll(t.dir, 0o755)
			now := time.Now()
			os.Chtimes(t.dir, now, now) // marca como usado agora, para a limpeza
			t.n = max(1, min(thumbCount, int(r.Dur/5)))
			entries, _ := os.ReadDir(t.dir)
			for _, e := range entries {
				if i, err := strconv.Atoi(strings.TrimSuffix(e.Name(), ".jpg")); err == nil && strings.HasSuffix(e.Name(), ".jpg") {
					t.have[i] = true
				}
			}
			var todo []int
			for _, i := range order(t.n) {
				if !t.have[i] {
					todo = append(todo, i)
				}
			}
			if len(todo) > 0 {
				ctx, cancel := context.WithCancel(context.Background())
				t.stop = cancel
				go t.run(ctx, r.ID, r.Path, r.Dur, r.HDR, t.dir, t.n, todo)
			}
		}
	}
	have := make([]int, 0, len(t.have))
	for i := range t.have {
		have = append(have, i)
	}
	sort.Ints(have)
	return t.n, have
}

func (t *thumbs) run(ctx context.Context, id, path string, dur float64, hdr bool, dir string, n int, todo []int) {
	prune()
	ffmpeg := findTool("ffmpeg")
	vf := "scale=" + strconv.Itoa(thumbWidth) + ":-2"
	if hdr {
		vf = hdrFilter(thumbWidth)
	}
	step := dur / float64(n)
	jobs := make(chan int)
	var wg sync.WaitGroup
	for w := 0; w < thumbWorkers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := range jobs {
				out := filepath.Join(dir, strconv.Itoa(i)+".jpg")
				tmp := filepath.Join(dir, strconv.Itoa(i)+".tmp.jpg")
				c, cancel := context.WithTimeout(ctx, 30*time.Second)
				_ = command(c, true, ffmpeg, "-hide_banner", "-v", "error", "-y", "-skip_frame", "nokey",
					"-ss", strconv.FormatFloat((float64(i)+0.5)*step, 'f', 1, 64), "-i", path, "-map", "0:v:0",
					"-frames:v", "1", "-vf", vf, "-q:v", "4", tmp).Run()
				cancel()
				if st, err := os.Stat(tmp); err == nil && st.Size() > 0 && os.Rename(tmp, out) == nil {
					t.mu.Lock()
					if t.id == id {
						t.have[i] = true
					}
					t.mu.Unlock()
				} else {
					os.Remove(tmp)
				}
			}
		}()
	}
	for _, i := range todo {
		select {
		case jobs <- i:
		case <-ctx.Done():
		}
		if ctx.Err() != nil {
			break
		}
	}
	close(jobs)
	wg.Wait()
	debug.FreeOSMemory()
}

// file: o arquivo de uma miniatura do vídeo atual, se já existe.
func (t *thumbs) file(id string, i int) string {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.id != id || !t.have[i] {
		return ""
	}
	return filepath.Join(t.dir, strconv.Itoa(i)+".jpg")
}

func prune() {
	entries, err := os.ReadDir(thumbDir)
	if err != nil {
		return
	}
	type d struct {
		path string
		t    time.Time
	}
	var dirs []d
	for _, e := range entries {
		if info, err := e.Info(); err == nil && e.IsDir() {
			dirs = append(dirs, d{filepath.Join(thumbDir, e.Name()), info.ModTime()})
		}
	}
	sort.Slice(dirs, func(i, j int) bool { return dirs[i].t.After(dirs[j].t) })
	for i := thumbKeep; i < len(dirs); i++ {
		os.RemoveAll(dirs[i].path)
	}
}
