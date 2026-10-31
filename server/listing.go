package main

import (
	"crypto/md5"
	"encoding/hex"
	"math"
	"os"
	"sort"
	"strconv"
	"strings"
)

// O que a TV e o player do PC recebem em /api/library (mesmo formato do library.py, sem capítulos e favoritos).

type video struct {
	ID      string           `json:"id"`
	File    string           `json:"file"`
	Path    string           `json:"path"`
	Size    int64            `json:"size"`
	Dur     float64          `json:"dur"`
	Pos     float64          `json:"pos"`
	ExtSubs []map[string]any `json:"extSubs"`
	Quality string           `json:"quality"`
	Audio   []string         `json:"audio"`
	Subs    []string         `json:"subs"`
	Broken  bool             `json:"broken"`
	Added   float64          `json:"added"`
	Bps     int64            `json:"bps"`
	Watched string           `json:"watched"`
}

type movieOut struct {
	video
	Title    string   `json:"title"`
	Year     *int     `json:"year"`
	Runtime  int      `json:"runtime"`
	Genres   []string `json:"genres"`
	Vote     float64  `json:"vote"`
	Overview string   `json:"overview"`
	Poster   string   `json:"poster"`
	Backdrop string   `json:"backdrop"`
	Logo     string   `json:"logo"`
	Frame    string   `json:"frame"`
}

type episodeOut struct {
	video
	Season   int    `json:"season"`
	Episode  int    `json:"episode"`
	Title    string `json:"title"`
	Overview string `json:"overview"`
	Still    string `json:"still"`
	Runtime  int    `json:"runtime"`
}

type seasonOut struct {
	N        int           `json:"n"`
	Episodes []*episodeOut `json:"episodes"`
}

type showOut struct {
	ID       string       `json:"id"`
	Title    string       `json:"title"`
	Year     *int         `json:"year"`
	Genres   []string     `json:"genres"`
	Vote     float64      `json:"vote"`
	Overview string       `json:"overview"`
	Poster   string       `json:"poster"`
	Backdrop string       `json:"backdrop"`
	Logo     string       `json:"logo"`
	Frame    string       `json:"frame"`
	Added    float64      `json:"added"`
	Seasons  []*seasonOut `json:"seasons"`
}

type listingOut struct {
	PC       string           `json:"pc"`
	Folder   string           `json:"folder"`
	Busy     int              `json:"busy"`
	Version  int64            `json:"version"`
	History  []map[string]any `json:"history"`
	Tmdb     bool             `json:"tmdb"`
	Movies   []*movieOut      `json:"movies"`
	Shows    []*showOut       `json:"shows"`
	Continue []string         `json:"continue"`
}

func nonNil(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}

func common(r *record, pos float64) video {
	ext := make([]map[string]any, len(r.ExtSubs))
	for i, x := range r.ExtSubs {
		ext[i] = map[string]any{"n": i, "name": x.Name}
	}
	v := video{ID: r.ID, File: r.File, Path: r.Path, Size: r.Size, Dur: r.Dur, Pos: pos, ExtSubs: ext, Quality: r.Quality,
		Audio: nonNil(r.Audio), Subs: nonNil(r.Subs), Broken: r.Broken, Added: r.Added}
	if r.Dur > 0 {
		v.Bps = int64(float64(r.Size) * 8 / r.Dur)
	}
	return v
}

func (l *Library) listing() *listingOut {
	host, _ := os.Hostname()
	l.mu.Lock()
	recs := make([]record, 0, len(l.files))
	for _, r := range l.files {
		recs = append(recs, *r)
	}
	shows := map[string]show{}
	for k, s := range l.shows {
		shows[k] = *s
	}
	out := &listingOut{PC: host, Folder: libDir, Busy: l.busy, Version: l.version, Tmdb: l.tmdb.token != "",
		Movies: []*movieOut{}, Shows: []*showOut{}, Continue: []string{}}
	l.mu.Unlock()

	type prog struct {
		at float64
		id string
	}
	var progress []prog
	posOf := map[string]float64{}
	for _, r := range recs {
		p, at := positions.get(r.Path)
		if p = resumeAt(p, r.Dur); p > 0 {
			posOf[r.ID] = p
			progress = append(progress, prog{at, r.ID})
		}
	}

	hist := readHistory()
	sort.SliceStable(hist, func(i, j int) bool { return num(hist[i]["at"]) > num(hist[j]["at"]) })
	last := map[string]string{}
	for _, h := range hist {
		id, _ := h["id"].(string)
		if _, ok := last[id]; !ok {
			last[id], _ = h["date"].(string)
		}
	}
	if len(hist) > 40 {
		hist = hist[:40]
	}
	out.History = hist
	if out.History == nil {
		out.History = []map[string]any{}
	}

	byShow := map[string]*showOut{}
	seasons := map[string]map[int]*seasonOut{}
	for i := range recs {
		r := &recs[i]
		v := common(r, posOf[r.ID])
		v.Watched = last[r.ID]
		if r.Kind == "movie" {
			m := r.Meta
			if m == nil {
				m = &meta{}
			}
			mo := &movieOut{video: v, Title: firstNonEmpty(m.Title, r.Title), Year: m.Year, Runtime: m.Runtime,
				Genres: nonNil(m.Genres), Vote: m.Vote, Overview: m.Overview, Poster: m.Poster, Backdrop: m.Backdrop,
				Logo: m.Logo, Frame: r.Frame}
			if mo.Year == nil {
				mo.Year = r.Year
			}
			if mo.Runtime == 0 {
				mo.Runtime = int(math.Round(r.Dur / 60))
			}
			out.Movies = append(out.Movies, mo)
			continue
		}
		key := r.ShowKey
		if key == "" {
			key = strings.ToLower(r.Title)
		}
		sh, ok := shows[key]
		if !ok {
			sh = show{meta: meta{Title: r.Title}}
		}
		o := byShow[key]
		if o == nil {
			h := md5.Sum([]byte(key))
			o = &showOut{ID: "s_" + hex.EncodeToString(h[:])[:12], Title: firstNonEmpty(sh.Title, r.Title), Year: sh.Year,
				Genres: nonNil(sh.Genres), Vote: sh.Vote, Overview: sh.Overview, Poster: sh.Poster, Backdrop: sh.Backdrop,
				Logo: sh.Logo, Frame: r.Frame}
			if o.Year == nil {
				o.Year = r.Year
			}
			byShow[key] = o
			seasons[key] = map[int]*seasonOut{}
			out.Shows = append(out.Shows, o)
		}
		ep := sh.Seasons[strconv.Itoa(r.Season)][strconv.Itoa(r.Episode)]
		e := &episodeOut{video: v, Season: r.Season, Episode: r.Episode,
			Title: firstNonEmpty(ep.Name, "Episódio "+strconv.Itoa(r.Episode)), Overview: ep.Overview,
			Still: firstNonEmpty(ep.Still, r.Frame), Runtime: ep.Runtime}
		if e.Runtime == 0 {
			e.Runtime = int(math.Round(r.Dur / 60))
		}
		o.Added = math.Max(o.Added, r.Added)
		se := seasons[key][r.Season]
		if se == nil {
			se = &seasonOut{N: r.Season}
			seasons[key][r.Season] = se
			o.Seasons = append(o.Seasons, se)
		}
		se.Episodes = append(se.Episodes, e)
	}
	for _, o := range out.Shows {
		sort.Slice(o.Seasons, func(i, j int) bool { return o.Seasons[i].N < o.Seasons[j].N })
		for _, se := range o.Seasons {
			sort.Slice(se.Episodes, func(i, j int) bool { return se.Episodes[i].Episode < se.Episodes[j].Episode })
		}
	}
	sort.Slice(out.Movies, func(i, j int) bool { return out.Movies[i].Added > out.Movies[j].Added })
	sort.Slice(out.Shows, func(i, j int) bool { return out.Shows[i].Added > out.Shows[j].Added })
	sort.Slice(progress, func(i, j int) bool { return progress[i].at > progress[j].at })
	for _, p := range progress {
		out.Continue = append(out.Continue, p.id)
	}
	return out
}

func num(v any) float64 {
	f, _ := v.(float64)
	return f
}
