package main

import (
	"crypto/md5"
	"encoding/hex"
	"encoding/json"
	"log"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// record: um vídeo da pasta (mesmo formato do index.json do library.py).
type record struct {
	ID        string   `json:"id"`
	Path      string   `json:"path"`
	Size      int64    `json:"size"`
	Mtime     int64    `json:"mtime"`
	Added     float64  `json:"added"`
	File      string   `json:"file"`
	Kind      string   `json:"kind"`
	Title     string   `json:"title"`
	Year      *int     `json:"year"`
	Season    int      `json:"season,omitempty"`
	Episode   int      `json:"episode,omitempty"`
	Dur       float64  `json:"dur,omitempty"`
	Quality   string   `json:"quality,omitempty"`
	HDR       bool     `json:"hdr,omitempty"`
	Audio     []string `json:"audio,omitempty"`
	Subs      []string `json:"subs,omitempty"`
	ExtSubs   []extSub `json:"extSubs,omitempty"`
	EmbSubs   []string `json:"embSubs,omitempty"`
	V         int      `json:"v,omitempty"`
	Broken    bool     `json:"broken,omitempty"`
	Frame     string   `json:"frame,omitempty"`
	Meta      *meta    `json:"meta,omitempty"`
	TmdbTried bool     `json:"tmdbTried,omitempty"`
	ShowKey   string   `json:"showKey,omitempty"`
}

// show: os dados de uma série no TMDB, com as temporadas já consultadas.
type show struct {
	meta
	Seasons map[string]map[string]episodeMeta `json:"seasons"`
}

type Library struct {
	mu       sync.Mutex
	files    map[string]*record
	shows    map[string]*show
	seen     map[string][2]int64 // caminho → (tamanho, mtime) da última olhada: só lê depois de parar de crescer
	busy     int
	version  int64 // muda a cada mudança no que a TV recebe (para a TV saber se precisa montar a tela de novo)
	tmdb     *tmdbClient
	retry    map[string]tmdbRetry
	onChange func()
	thumbs   *thumbs
}

type tmdbRetry struct {
	n    int
	next time.Time
}

func newLibrary() *Library {
	os.MkdirAll(imgDir, 0o755)
	os.MkdirAll(libDir, 0o755)
	l := &Library{files: map[string]*record{}, shows: map[string]*show{}, seen: map[string][2]int64{},
		tmdb: newTmdb(), retry: map[string]tmdbRetry{}, onChange: func() {}, thumbs: &thumbs{}, version: time.Now().Unix()}
	if raw, err := os.ReadFile(indexF); err == nil {
		var data struct {
			Files map[string]*record `json:"files"`
			Shows map[string]*show   `json:"shows"`
		}
		if err := json.Unmarshal(raw, &data); err == nil {
			if data.Files != nil {
				l.files = data.Files
			}
			if data.Shows != nil {
				l.shows = data.Shows
			}
		} else { // índice corrompido: guarda em vez de sobrescrever no próximo save
			os.Rename(indexF, indexF+".bad")
			log.Printf("índice ilegível, guardado como index.json.bad: %v", err)
		}
	}
	for _, r := range l.files {
		if r.Kind == "movie" || r.Kind == "episode" {
			continue
		}
		r.Kind = "movie"
	}
	return l
}

// save grava o índice (chamar com o lock).
func (l *Library) save() {
	data, err := json.Marshal(map[string]any{"files": l.files, "shows": l.shows})
	if err == nil {
		err = writeAtomic(indexF, data)
	}
	if err != nil {
		log.Printf("não consegui gravar o índice: %v", err)
	}
	l.changed()
}

// changed: o que a TV recebe mudou (chamar com o lock).
func (l *Library) changed() {
	l.version++
	go l.onChange()
}

func writeAtomic(path string, data []byte) error {
	tmp := path + ".tmp"
	f, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	if _, err = f.Write(data); err == nil {
		err = f.Sync() // no disco antes de trocar: uma queda de energia não deixa o arquivo vazio
	}
	if cerr := f.Close(); err == nil {
		err = cerr
	}
	if err != nil {
		os.Remove(tmp)
		return err
	}
	return os.Rename(tmp, path)
}

func md5hex(s string) string {
	h := md5.Sum([]byte(s))
	return hex.EncodeToString(h[:])
}

// fileID: o mesmo do library.py (a TV guarda o cache e os assistidos por ele).
func fileID(path string, size int64) string {
	return md5hex(strings.ToLower(path) + "|" + strconv.FormatInt(size, 10))[:16]
}

// ---------- olhar a pasta ----------

type found struct {
	size, mtime int64
}

// scan compara a pasta com o índice. dirty: pastas em que o Windows avisou mudança (nil = todas).
// Devolve true se ainda há vídeo chegando (cópia em andamento) e vale olhar de novo logo.
func (l *Library) scan(dirty map[string]bool) bool {
	now := time.Now()
	disk := map[string]found{}
	filepath.WalkDir(libDir, func(p string, d os.DirEntry, err error) error {
		if err != nil || d.IsDir() || !videoExts[strings.ToLower(filepath.Ext(p))] {
			return nil
		}
		info, err := d.Info()
		if err != nil || info.Size() < minSize || isSample(p) || strings.HasSuffix(p, ".part") {
			return nil
		}
		disk[p] = found{info.Size(), info.ModTime().Unix()}
		return nil
	})

	l.mu.Lock()
	known := map[string]string{}
	for id, r := range l.files {
		known[r.Path] = id
	}
	gone := false
	for p, id := range known {
		r := l.files[id]
		if f, ok := disk[p]; !ok || f.size != r.Size || f.mtime != r.Mtime {
			delete(l.files, id)
			delete(known, p)
			gone = true
		}
	}
	for p := range l.seen {
		if _, ok := disk[p]; !ok {
			delete(l.seen, p)
		}
	}
	var todo []string
	waiting := false
	for p, f := range disk {
		if _, ok := known[p]; ok {
			continue
		}
		sig := [2]int64{f.size, f.mtime}
		// arquivo ainda chegando (cópia em andamento): espera o tamanho parar de mudar
		if l.seen[p] == sig && now.Unix()-f.mtime > 10 {
			todo = append(todo, p)
		} else {
			waiting = true
		}
		l.seen[p] = sig
	}
	l.busy = len(todo)
	if gone {
		l.dropEmptyShows()
		l.save()
	}
	// legenda colocada depois ao lado de um vídeo que já estava na biblioteca: só nas pastas que mudaram
	subsChanged := false
	for _, r := range l.files {
		if r.Broken || (dirty != nil && !dirty[strings.ToLower(filepath.Dir(r.Path))]) {
			continue
		}
		ext := findSubs(r.Path)
		if !sameSubs(ext, r.ExtSubs) {
			r.ExtSubs = ext
			r.Subs = dedupe(append(append([]string{}, r.EmbSubs...), subNames(ext)...))
			subsChanged = true
			log.Printf("legendas de %s: %v", r.File, subNames(ext))
		}
	}
	if subsChanged {
		l.save()
	}
	var reprobe []*record
	for _, r := range l.files {
		if !r.Broken && r.V < probeVer {
			reprobe = append(reprobe, r)
		}
	}
	if len(todo) > 0 {
		l.changed() // "lendo N vídeos novos"
	}
	l.mu.Unlock()

	for _, r := range reprobe {
		if info := probe(r.Path); info != nil {
			l.mu.Lock()
			applyProbe(r, info)
			l.mu.Unlock()
		}
	}
	if len(reprobe) > 0 {
		l.mu.Lock()
		l.save()
		l.mu.Unlock()
	}
	sort.Strings(todo)
	for _, p := range todo {
		l.add(p, disk[p])
		l.mu.Lock()
		l.busy--
		l.changed()
		l.mu.Unlock()
	}
	l.retryTmdb()
	return waiting
}

func applyProbe(r *record, info *probeInfo) {
	r.Dur, r.Quality, r.HDR, r.Audio = info.Dur, info.Quality, info.HDR, info.Audio
	r.EmbSubs = info.Subs
	r.ExtSubs = findSubs(r.Path)
	r.Subs = dedupe(append(append([]string{}, info.Subs...), subNames(r.ExtSubs)...))
	r.V = probeVer
}

func (l *Library) add(p string, f found) {
	pn := parseName(p)
	r := &record{ID: fileID(p, f.size), Path: p, Size: f.size, Mtime: f.mtime,
		Added: float64(time.Now().UnixNano()) / 1e9, File: filepath.Base(p),
		Kind: pn.Kind, Title: pn.Title, Year: pn.Year, Season: pn.Season, Episode: pn.Episode}
	if info := probe(p); info == nil {
		r.Broken = true
		log.Printf("arquivo com problema: %s", p)
	} else {
		applyProbe(r, info)
		frame := "frame_" + r.ID + ".jpg"
		if r.Dur > 0 && grabFrame(p, math.Round(r.Dur*0.3), r.HDR, 960, filepath.Join(imgDir, frame)) {
			r.Frame = frame
		}
	}
	l.lookup(r)
	l.mu.Lock()
	l.files[r.ID] = r
	l.save()
	l.mu.Unlock()
	log.Printf("na biblioteca: %s (%s)", r.Title, r.File)
}

// lookup busca o vídeo no TMDB (fora do lock: é rede). Falhou: tenta de novo mais tarde, com espera crescente.
func (l *Library) lookup(r *record) {
	if !l.tmdb.hasToken() {
		return
	}
	err := func() error {
		if r.Kind == "movie" {
			m, err := l.tmdb.details("movie", r.Title, r.Year)
			if err != nil {
				return err
			}
			if m == nil && filepath.Dir(r.Path) != libDir { // tenta pelo nome da pasta do filme
				pn := parseName(filepath.Base(filepath.Dir(r.Path)) + ".mkv")
				y := pn.Year
				if y == nil {
					y = r.Year
				}
				if m, err = l.tmdb.details("movie", pn.Title, y); err != nil {
					return err
				}
			}
			l.mu.Lock()
			r.Meta = m
			l.mu.Unlock()
			return nil
		}
		key := strings.ToLower(r.Title)
		l.mu.Lock()
		sh := l.shows[key]
		l.mu.Unlock()
		if sh == nil {
			m, err := l.tmdb.details("tv", r.Title, r.Year)
			if err != nil {
				return err
			}
			sh = &show{Seasons: map[string]map[string]episodeMeta{}}
			if m != nil {
				sh.meta = *m
			} else {
				sh.Title = r.Title
			}
			l.mu.Lock()
			l.shows[key] = sh
			l.mu.Unlock()
		}
		season := strconv.Itoa(r.Season)
		l.mu.Lock()
		_, have := sh.Seasons[season]
		l.mu.Unlock()
		if sh.TMDB != 0 && !have {
			eps, err := l.tmdb.season(sh.TMDB, r.Season)
			if err != nil {
				return err
			}
			l.mu.Lock()
			sh.Seasons[season] = eps
			l.mu.Unlock()
		}
		l.mu.Lock()
		r.ShowKey = key
		l.mu.Unlock()
		return nil
	}()
	l.mu.Lock()
	defer l.mu.Unlock()
	if err != nil {
		t := l.retry[r.ID]
		t.n++
		t.next = time.Now().Add(time.Duration(math.Min(math.Pow(4, float64(t.n)), 240)) * time.Minute)
		l.retry[r.ID] = t
		r.TmdbTried = false
		log.Printf("TMDB falhou para %s (tentativa %d): %v", r.File, t.n, err)
		return
	}
	delete(l.retry, r.ID)
	r.TmdbTried = true
}

// retryTmdb: vídeos guardados sem TMDB (antes da chave, ou a rede falhou); no máximo 5 tentativas por vídeo.
func (l *Library) retryTmdb() {
	if !l.tmdb.hasToken() {
		return
	}
	l.mu.Lock()
	var due []*record
	for id, r := range l.files {
		t := l.retry[id]
		if !r.TmdbTried && t.n < 5 && time.Now().After(t.next) {
			due = append(due, r)
		}
	}
	l.mu.Unlock()
	for _, r := range due {
		l.lookup(r)
		l.mu.Lock()
		l.save()
		l.mu.Unlock()
	}
}

func (l *Library) dropEmptyShows() {
	used := map[string]bool{}
	for _, r := range l.files {
		used[r.ShowKey] = true
	}
	for k := range l.shows {
		if !used[k] {
			delete(l.shows, k)
		}
	}
}

func sameSubs(a, b []extSub) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i].Path != b[i].Path {
			return false
		}
	}
	return true
}

func subNames(s []extSub) []string {
	out := make([]string, len(s))
	for i, x := range s {
		out[i] = x.Name
	}
	return out
}

func dedupe(s []string) []string {
	seen, out := map[string]bool{}, []string{}
	for _, x := range s {
		if !seen[x] {
			seen[x] = true
			out = append(out, x)
		}
	}
	return out
}

// ---------- regras (as mesmas para TV e PC) ----------

// isEnd: nos créditos, últimos 6% do vídeo ou últimos 2 min (o Harriet tem 7 min de créditos).
func isEnd(pos, dur float64) bool {
	return dur > 0 && dur-pos <= math.Max(120, dur*0.06)
}

// resumeAt: de onde continuar (0 = do começo): só depois de 30 s e antes dos créditos.
func resumeAt(pos, dur float64) float64 {
	if pos > 30 && !isEnd(pos, dur) {
		return pos
	}
	return 0
}

func (l *Library) byID(id string) *record {
	l.mu.Lock()
	defer l.mu.Unlock()
	r := l.files[id]
	if r == nil {
		return nil
	}
	c := *r // cópia: quem chamou lê sem o lock, e o scan pode trocar as fatias do original
	c.ExtSubs = append([]extSub(nil), r.ExtSubs...)
	return &c
}

func (l *Library) byPath(p string) *record {
	l.mu.Lock()
	defer l.mu.Unlock()
	for _, r := range l.files {
		if strings.EqualFold(r.Path, filepath.Clean(p)) {
			return r
		}
	}
	return nil
}

// setProgress: a TV (ou o PC) conta onde parou; nos créditos vira "assistido".
func (l *Library) setProgress(id string, pos, dur float64) bool {
	r := l.byID(id)
	if r == nil {
		return false
	}
	if dur > 0 && isEnd(pos, dur) {
		positions.clear(r.Path)
		l.markWatched(r, time.Now())
	} else if pos > 0 {
		positions.set(r.Path, pos)
	}
	l.mu.Lock()
	l.changed()
	l.mu.Unlock()
	return true
}

// ---------- assistidos ----------

var historyMu sync.Mutex

// readHistory lê os assistidos (chamar com historyMu). Arquivo ilegível é guardado como .bad, não sobrescrito.
func readHistory() []map[string]any {
	var h []map[string]any
	if raw, err := os.ReadFile(historyF); err == nil {
		if err := json.Unmarshal(raw, &h); err != nil {
			os.Rename(historyF, historyF+".bad")
			log.Printf("assistidos ilegível, guardado como .bad: %v", err)
			return nil
		}
	}
	return h
}

// history: cópia dos assistidos para quem não está com o lock.
func history() []map[string]any {
	historyMu.Lock()
	defer historyMu.Unlock()
	return readHistory()
}

// markWatched anota que o vídeo foi assistido até o fim (uma vez por dia por vídeo).
func (l *Library) markWatched(r *record, when time.Time) {
	l.mu.Lock()
	snap := snapshot(r, l.shows[r.ShowKey])
	l.mu.Unlock()
	historyMu.Lock()
	defer historyMu.Unlock()
	day := when.Format("2006-01-02")
	hist := readHistory()
	for _, h := range hist {
		if h["id"] == r.ID && h["date"] == day {
			return
		}
	}
	entry := map[string]any{"id": r.ID, "date": day, "at": float64(when.UnixNano()) / 1e9, "kind": r.Kind, "file": r.File}
	for k, v := range snap {
		entry[k] = v
	}
	hist = append(hist, entry)
	data, _ := json.MarshalIndent(hist, "", " ")
	if err := writeAtomic(historyF, data); err != nil {
		log.Printf("não consegui gravar os assistidos: %v", err)
		return
	}
	log.Printf("assistido: %v em %s", entry["title"], day)
	l.mu.Lock()
	l.changed()
	l.mu.Unlock()
}

// snapshot: os dados que a TV mostra, guardados junto do assistido (sobrevivem ao arquivo sair da pasta).
func snapshot(r *record, sh *show) map[string]any {
	m := r.Meta
	if m == nil {
		m = &meta{}
	}
	var s meta
	if sh != nil {
		s = sh.meta
	}
	ep := r.Kind == "episode"
	title := m.Title
	if title == "" && ep {
		title = s.Title
	}
	year := m.Year
	if year == nil {
		year = s.Year
	}
	if year == nil {
		year = r.Year
	}
	runtime := m.Runtime
	if runtime == 0 {
		runtime = int(math.Round(r.Dur / 60))
	}
	genres := m.Genres
	if len(genres) == 0 {
		genres = s.Genres
	}
	if genres == nil {
		genres = []string{}
	}
	vote := m.Vote
	if vote == 0 {
		vote = s.Vote
	}
	out := map[string]any{"title": firstNonEmpty(title, r.Title), "year": year, "runtime": runtime, "genres": genres,
		"vote": vote, "overview": firstNonEmpty(m.Overview, s.Overview), "poster": firstNonEmpty(m.Poster, s.Poster),
		"backdrop": firstNonEmpty(m.Backdrop, s.Backdrop), "logo": firstNonEmpty(m.Logo, s.Logo), "frame": r.Frame,
		"quality": r.Quality}
	if ep {
		name := "Episódio " + strconv.Itoa(r.Episode)
		if sh != nil {
			if e, ok := sh.Seasons[strconv.Itoa(r.Season)][strconv.Itoa(r.Episode)]; ok && e.Name != "" {
				name = e.Name
			}
		}
		out["season"], out["episode"], out["epTitle"] = r.Season, r.Episode, name
	}
	return out
}
