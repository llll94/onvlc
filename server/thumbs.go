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
// filme inteiro, depois os do meio. Servem a TV (vídeos da biblioteca, por id) e o player do PC (qualquer
// arquivo, por caminho). Até 3 vídeos ao mesmo tempo: a TV e o PC não se atrapalham.
const (
	thumbCount   = 200 // quadros por vídeo (um a cada ~40 s num filme de 2 h)
	thumbWidth   = 240
	thumbKeep    = 60 // vídeos guardados no disco; os mais antigos saem
	thumbWorkers = 2
	thumbActive  = 3 // vídeos com miniaturas em andamento ao mesmo tempo
)

type thumbJob struct {
	dir  string
	n    int
	have map[int]bool
	stop context.CancelFunc
	used time.Time
}

type thumbs struct {
	mu   sync.Mutex
	jobs map[string]*thumbJob // id → trabalho
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
func (t *thumbs) start(id, path string, dur float64, hdr bool) (int, []int) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.jobs == nil {
		t.jobs = map[string]*thumbJob{}
	}
	j := t.jobs[id]
	if j == nil {
		j = &thumbJob{have: map[int]bool{}}
		ffmpeg := findTool("ffmpeg")
		st, err := os.Stat(path)
		if ffmpeg != "" && err == nil && dur > 0 {
			// mesma pasta e mesmos nomes do thumbs.py (as miniaturas já feitas continuam valendo)
			key := md5hex(strings.ToLower(path) + "|" + strconv.FormatInt(st.Size(), 10) + "|" + strconv.FormatInt(st.ModTime().Unix(), 10))
			j.dir = filepath.Join(thumbDir, key)
			os.MkdirAll(j.dir, 0o755)
			now := time.Now()
			os.Chtimes(j.dir, now, now) // marca como usado agora, para a limpeza
			j.n = max(1, min(thumbCount, int(dur/5)))
			entries, _ := os.ReadDir(j.dir)
			for _, e := range entries {
				if i, err := strconv.Atoi(strings.TrimSuffix(e.Name(), ".jpg")); err == nil && strings.HasSuffix(e.Name(), ".jpg") {
					j.have[i] = true
				}
			}
			var todo []int
			for _, i := range order(j.n) {
				if !j.have[i] {
					todo = append(todo, i)
				}
			}
			if len(todo) > 0 {
				ctx, cancel := context.WithCancel(context.Background())
				j.stop = cancel
				go t.run(ctx, j, path, dur, hdr, todo)
			}
		}
		t.jobs[id] = j
		t.evict()
	}
	j.used = time.Now()
	have := make([]int, 0, len(j.have))
	for i := range j.have {
		have = append(have, i)
	}
	sort.Ints(have)
	return j.n, have
}

// evict: com mais de thumbActive vídeos, para e esquece o usado há mais tempo (chamar com o lock).
func (t *thumbs) evict() {
	for len(t.jobs) > thumbActive {
		var oldID string
		var old *thumbJob
		for id, j := range t.jobs {
			if old == nil || j.used.Before(old.used) {
				oldID, old = id, j
			}
		}
		if old.stop != nil {
			old.stop()
		}
		delete(t.jobs, oldID)
	}
}

func (t *thumbs) run(ctx context.Context, j *thumbJob, path string, dur float64, hdr bool, todo []int) {
	prune()
	ffmpeg := findTool("ffmpeg")
	vf := "scale=" + strconv.Itoa(thumbWidth) + ":-2"
	if hdr {
		vf = hdrFilter(thumbWidth)
	}
	step := dur / float64(j.n)
	jobs := make(chan int)
	var wg sync.WaitGroup
	for w := 0; w < thumbWorkers; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for i := range jobs {
				out := filepath.Join(j.dir, strconv.Itoa(i)+".jpg")
				tmp := filepath.Join(j.dir, strconv.Itoa(i)+".tmp.jpg")
				c, cancel := context.WithTimeout(ctx, 30*time.Second)
				_ = command(c, true, ffmpeg, "-hide_banner", "-v", "error", "-y", "-skip_frame", "nokey",
					"-ss", strconv.FormatFloat((float64(i)+0.5)*step, 'f', 1, 64), "-i", path, "-map", "0:v:0",
					"-frames:v", "1", "-vf", vf, "-q:v", "4", tmp).Run()
				cancel()
				if st, err := os.Stat(tmp); err == nil && st.Size() > 0 && os.Rename(tmp, out) == nil {
					t.mu.Lock()
					j.have[i] = true
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

// file: o arquivo de uma miniatura, se já existe.
func (t *thumbs) file(id string, i int) string {
	t.mu.Lock()
	defer t.mu.Unlock()
	j := t.jobs[id]
	if j == nil || !j.have[i] {
		return ""
	}
	return filepath.Join(j.dir, strconv.Itoa(i)+".jpg")
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
