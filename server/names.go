package main

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

// ---------- nome do arquivo ----------

var (
	seriesRe = regexp.MustCompile(`^(?P<show>.*?)[\s._-]*(?:[Ss](?P<s>\d{1,2})[\s._-]*[Ee](?P<e>\d{1,3})|(?P<s2>\d{1,2})x(?P<e2>\d{2}))`)
	// o ano mais à direita: "Blade Runner 2049 (2017)" → 2017
	yearRe = regexp.MustCompile(`^(?P<t>.+)[\s.\[(_-]+(?P<y>(?:19|20)\d\d)(?:$|[\s.\])_-])`)
	junkRe = regexp.MustCompile(`(?i)\b(2160p|1080p|720p|480p|4k|uhd|blu-?ray|brrip|bdrip|web-?dl|web-?rip|hdtv|hdrip|dvdrip|` +
		`camrip|cam|x26[45]|h\.?26[45]|hevc|10bit|hdr|atmos|truehd|multi|dublado|dual|legendado|` +
		`remux|proper|repack|extended|imax)\b.*`)
	bracketRe = regexp.MustCompile(`\[[^\]]*\]`)
	sepRe     = regexp.MustCompile(`[._]+`)
	spaceRe   = regexp.MustCompile(`\s+`)
	sampleRe  = regexp.MustCompile(`(?i)(^|[\W_])sample([\W_]|$)`)
)

type parsed struct {
	Kind, Title     string
	Year            *int
	Season, Episode int
}

func group(re *regexp.Regexp, m []string, name string) string {
	return m[re.SubexpIndex(name)]
}

func clean(s string) string {
	s = bracketRe.ReplaceAllString(s, " ")
	s = sepRe.ReplaceAllString(s, " ")
	s = junkRe.ReplaceAllString(s, "")
	s = spaceRe.ReplaceAllString(s, " ")
	return strings.Trim(s, " -([")
}

func stem(path string) string {
	b := filepath.Base(path)
	return strings.TrimSuffix(b, filepath.Ext(b))
}

// parseName: do nome do arquivo, título e ano (filme) ou série, temporada e episódio.
func parseName(path string) parsed {
	st := stem(path)
	if m := seriesRe.FindStringSubmatch(st); m != nil {
		show := group(seriesRe, m, "show")
		if show == "" {
			show = filepath.Base(filepath.Dir(path))
		}
		p := parsed{Kind: "episode", Title: clean(show)}
		if ym := yearRe.FindStringSubmatch(show); ym != nil {
			y, _ := strconv.Atoi(group(yearRe, ym, "y"))
			p.Year, p.Title = &y, clean(group(yearRe, ym, "t"))
		}
		s, e := group(seriesRe, m, "s"), group(seriesRe, m, "e")
		if s == "" {
			s, e = group(seriesRe, m, "s2"), group(seriesRe, m, "e2")
		}
		p.Season, _ = strconv.Atoi(s)
		p.Episode, _ = strconv.Atoi(e)
		return p
	}
	if ym := yearRe.FindStringSubmatch(st); ym != nil {
		y, _ := strconv.Atoi(group(yearRe, ym, "y"))
		if y <= time.Now().Year()+1 {
			return parsed{Kind: "movie", Title: clean(group(yearRe, ym, "t")), Year: &y}
		}
	}
	return parsed{Kind: "movie", Title: clean(st)}
}

// isSample: amostra que vem junto no download (pasta Sample ou "sample" no nome).
func isSample(path string) bool {
	return strings.EqualFold(filepath.Base(filepath.Dir(path)), "sample") || sampleRe.MatchString(stem(path))
}

// ---------- legendas em arquivo ----------

var langNames = map[string]string{"por": "Português", "pt": "Português", "pob": "Português", "eng": "Inglês", "en": "Inglês",
	"spa": "Espanhol", "es": "Espanhol", "fre": "Francês", "fra": "Francês", "fr": "Francês",
	"ita": "Italiano", "it": "Italiano", "ger": "Alemão", "deu": "Alemão", "de": "Alemão",
	"jpn": "Japonês", "ja": "Japonês", "kor": "Coreano", "ko": "Coreano", "rus": "Russo", "chi": "Chinês",
	"zho": "Chinês"}

type extSub struct {
	Path string `json:"path"`
	Name string `json:"name"`
}

// findSubs: legendas ao lado do vídeo e nas pastas Subs/Legendas. Com um vídeo só na pasta, todas são dele.
func findSubs(video string) []extSub {
	folder := filepath.Dir(video)
	entries, err := os.ReadDir(folder)
	if err != nil {
		return nil
	}
	videos := 0
	dirs := []string{folder}
	for _, e := range entries {
		p := filepath.Join(folder, e.Name())
		if e.IsDir() && subDirs[strings.ToLower(e.Name())] {
			dirs = append(dirs, p)
		} else if !e.IsDir() && videoExts[strings.ToLower(filepath.Ext(p))] && !isSample(p) {
			videos++
		}
	}
	alone := videos <= 1
	vstem := strings.ToLower(stem(video))
	var found []extSub
	for _, d := range dirs {
		list := entries
		if d != folder {
			if list, err = os.ReadDir(d); err != nil {
				continue
			}
		}
		names := make([]string, 0, len(list))
		for _, e := range list {
			if !e.IsDir() && subExts[strings.ToLower(filepath.Ext(e.Name()))] {
				names = append(names, e.Name())
			}
		}
		sort.Strings(names)
		for _, n := range names {
			if !alone && d == folder && !strings.HasPrefix(strings.ToLower(n), vstem) {
				continue
			}
			f := filepath.Join(d, n)
			found = append(found, extSub{Path: f, Name: subLabel(f, video)})
		}
	}
	seen := map[string]int{}
	for i := range found { // nomes repetidos ganham um número: "Inglês", "Inglês 2"
		seen[found[i].Name]++
		if n := seen[found[i].Name]; n > 1 {
			found[i].Name += " " + strconv.Itoa(n)
		}
	}
	return found
}

var tokRe = regexp.MustCompile(`[\s._\-\[\]()]+`)

func subLabel(f, video string) string {
	fs, vs := stem(f), stem(video)
	rest := fs
	if strings.HasPrefix(strings.ToLower(fs), strings.ToLower(vs)) {
		rest = fs[len(vs):]
	}
	sdh := ""
	if strings.Contains(strings.ToLower(fs), "sdh") {
		sdh = " (SDH)"
	}
	toks := tokRe.Split(strings.ToLower(rest), -1)
	for i := len(toks) - 1; i >= 0; i-- {
		t := toks[i]
		if l, ok := langNames[t]; ok {
			return l + sdh
		}
		switch t {
		case "portuguese", "portugues", "português", "brazilian", "ptbr":
			return "Português" + sdh
		case "english", "british":
			return "Inglês" + sdh
		}
	}
	if l := sniffLang(f); l != "" {
		return l + sdh
	}
	return "Legenda" + sdh
}

// palavras bem comuns de cada idioma, para quando o nome do arquivo não diz qual é
var commonWords = map[string]map[string]bool{
	"Português": set("você", "não", "está", "que", "uma", "isso", "por", "com", "é", "meu", "mas"),
	"Inglês":    set("the", "you", "and", "is", "to", "what", "this", "it", "of", "that", "i'm"),
	"Espanhol":  set("que", "no", "el", "la", "es", "usted", "qué", "por", "una", "está", "pero"),
}

var wordRe = regexp.MustCompile(`[a-zà-ú']+`)

func set(w ...string) map[string]bool {
	m := map[string]bool{}
	for _, x := range w {
		m[x] = true
	}
	return m
}

// sniffLang: lê só o começo (20 KB) da legenda e conta palavras comuns de cada idioma.
func sniffLang(path string) string {
	f, err := os.Open(path)
	if err != nil {
		return ""
	}
	defer f.Close()
	buf := make([]byte, 20000)
	n, _ := f.Read(buf)
	raw := buf[:n]
	var text string
	if utf8.Valid(raw) {
		text = string(raw)
	} else { // cp1252: os acentos ficam no mesmo lugar do Latin-1
		r := make([]rune, len(raw))
		for i, b := range raw {
			r[i] = rune(b)
		}
		text = string(r)
	}
	words := wordRe.FindAllString(strings.ToLower(text), -1)
	best, bestN := "", 0
	for _, lang := range []string{"Português", "Inglês", "Espanhol"} {
		n := 0
		for _, w := range words {
			if commonWords[lang][w] {
				n++
			}
		}
		if n > bestN {
			best, bestN = lang, n
		}
	}
	if bestN >= 15 {
		return best
	}
	return ""
}
