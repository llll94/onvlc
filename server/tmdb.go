package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"math"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"sort"
	"strconv"
	"syscall"
	"time"
	"unicode/utf16"
	"unsafe"
)

type meta struct {
	TMDB     int      `json:"tmdb,omitempty"`
	Title    string   `json:"title"`
	Year     *int     `json:"year"`
	Runtime  int      `json:"runtime"`
	Genres   []string `json:"genres"`
	Vote     float64  `json:"vote"`
	Overview string   `json:"overview"`
	Poster   string   `json:"poster"`
	Backdrop string   `json:"backdrop"`
	Logo     string   `json:"logo"`
}

type episodeMeta struct {
	Name     string `json:"name"`
	Overview string `json:"overview"`
	Runtime  int    `json:"runtime"`
	Still    string `json:"still"`
}

type tmdbClient struct {
	token string
	http  *http.Client
}

var errNotFound = errors.New("não encontrado")

func newTmdb() *tmdbClient {
	return &tmdbClient{token: readCredential("tmdb_token", "onVLC"), http: &http.Client{Timeout: 30 * time.Second}}
}

// readCredential: o mesmo lugar onde o keyring do Python guarda (Gerenciador de Credenciais do Windows):
// alvo "usuário@serviço", ou só "serviço" com aquele usuário.
func readCredential(user, service string) string {
	advapi := syscall.NewLazyDLL("advapi32.dll")
	credRead, credFree := advapi.NewProc("CredReadW"), advapi.NewProc("CredFree")
	type credential struct {
		Flags              uint32
		Type               uint32
		TargetName         *uint16
		Comment            *uint16
		LastWritten        syscall.Filetime
		CredentialBlobSize uint32
		CredentialBlob     *byte
		Persist            uint32
		AttributeCount     uint32
		Attributes         uintptr
		TargetAlias        *uint16
		UserName           *uint16
	}
	for _, target := range []string{user + "@" + service, service} {
		t, _ := syscall.UTF16PtrFromString(target)
		var c *credential
		if r, _, _ := credRead.Call(uintptr(unsafe.Pointer(t)), 1, 0, uintptr(unsafe.Pointer(&c))); r == 0 || c == nil {
			continue
		}
		if target == service && c.UserName != nil && syscall.UTF16ToString((*[256]uint16)(unsafe.Pointer(c.UserName))[:]) != user {
			credFree.Call(uintptr(unsafe.Pointer(c)))
			continue
		}
		blob := unsafe.Slice(c.CredentialBlob, c.CredentialBlobSize)
		u := make([]uint16, len(blob)/2)
		for i := range u {
			u[i] = uint16(blob[2*i]) | uint16(blob[2*i+1])<<8
		}
		credFree.Call(uintptr(unsafe.Pointer(c)))
		return string(utf16.Decode(u))
	}
	return ""
}

func (t *tmdbClient) get(p string, q url.Values, out any) error {
	req, _ := http.NewRequest("GET", tmdbAPI+p+"?"+q.Encode(), nil)
	req.Header.Set("Authorization", "Bearer "+t.token)
	req.Header.Set("Accept", "application/json")
	for attempt := 0; attempt < 3; attempt++ {
		res, err := t.http.Do(req)
		if err != nil {
			return err
		}
		body, _ := io.ReadAll(res.Body)
		res.Body.Close()
		switch {
		case res.StatusCode == 429: // limite de pedidos: espera um pouco
			time.Sleep(time.Duration(2+attempt*2) * time.Second)
			continue
		case res.StatusCode == 404:
			return errNotFound
		case res.StatusCode != 200:
			return fmt.Errorf("TMDB respondeu %d", res.StatusCode)
		}
		return json.Unmarshal(body, out)
	}
	return errors.New("TMDB: limite de pedidos")
}

// image baixa uma imagem do TMDB (uma vez só). Falha numa imagem não derruba o resto: devolve "".
func (t *tmdbClient) image(p, size string) string {
	if p == "" {
		return ""
	}
	name := size + "_" + path.Base(p)
	dst := filepath.Join(imgDir, name)
	if _, err := os.Stat(dst); err == nil {
		return name
	}
	res, err := t.http.Get(tmdbImg + size + p)
	if err != nil {
		log.Printf("imagem %s: %v", name, err)
		return ""
	}
	defer res.Body.Close()
	if res.StatusCode != 200 {
		log.Printf("imagem %s: TMDB respondeu %d", name, res.StatusCode)
		return ""
	}
	data, err := io.ReadAll(res.Body)
	if err != nil {
		return ""
	}
	if writeAtomic(dst, data) != nil {
		return ""
	}
	return name
}

type tmdbImage struct {
	FilePath string  `json:"file_path"`
	Lang     *string `json:"iso_639_1"`
	Country  *string `json:"iso_3166_1"`
	Vote     float64 `json:"vote_average"`
}

// bestLogo: o TMDB marca o país; o logo de Portugal ("Regresso a casa") vem antes do brasileiro nos votos.
func bestLogo(logos []tmdbImage) string {
	rank := func(l tmdbImage) int {
		lang, country := "", ""
		if l.Lang != nil {
			lang = *l.Lang
		}
		if l.Country != nil {
			country = *l.Country
		}
		switch {
		case lang == "pt" && country == "BR":
			return 0
		case lang == "en":
			return 1
		case l.Lang == nil:
			return 2
		}
		return 9
	}
	var ok []tmdbImage
	for _, l := range logos {
		if rank(l) < 9 {
			ok = append(ok, l)
		}
	}
	sort.SliceStable(ok, func(i, j int) bool {
		if rank(ok[i]) != rank(ok[j]) {
			return rank(ok[i]) < rank(ok[j])
		}
		return ok[i].Vote > ok[j].Vote
	})
	if len(ok) == 0 {
		return ""
	}
	return ok[0].FilePath
}

func (t *tmdbClient) search(kind, title string, year *int) (int, error) {
	key := "primary_release_year"
	if kind == "tv" {
		key = "first_air_date_year"
	}
	tries := []url.Values{{"query": {title}}}
	if year != nil {
		tries = append([]url.Values{{"query": {title}, key: {strconv.Itoa(*year)}}}, tries...)
	}
	for _, q := range tries {
		q.Set("language", "pt-BR")
		var res struct {
			Results []struct {
				ID int `json:"id"`
			} `json:"results"`
		}
		if err := t.get("/search/"+kind, q, &res); err != nil && err != errNotFound {
			return 0, err
		}
		if len(res.Results) > 0 {
			return res.Results[0].ID, nil
		}
	}
	return 0, nil
}

// details: título em português, sinopse, gêneros, nota e imagens. nil, nil = não achou.
func (t *tmdbClient) details(kind, title string, year *int) (*meta, error) {
	id, err := t.search(kind, title, year)
	if err != nil || id == 0 {
		return nil, err
	}
	var d struct {
		Title          string  `json:"title"`
		Name           string  `json:"name"`
		ReleaseDate    string  `json:"release_date"`
		FirstAirDate   string  `json:"first_air_date"`
		Overview       string  `json:"overview"`
		Runtime        int     `json:"runtime"`
		EpisodeRunTime []int   `json:"episode_run_time"`
		VoteAverage    float64 `json:"vote_average"`
		PosterPath     string  `json:"poster_path"`
		BackdropPath   string  `json:"backdrop_path"`
		Genres         []struct {
			Name string `json:"name"`
		} `json:"genres"`
		Images struct {
			Logos []tmdbImage `json:"logos"`
		} `json:"images"`
	}
	q := url.Values{"language": {"pt-BR"}, "append_to_response": {"images"}, "include_image_language": {"pt,en,null"}}
	if err := t.get(fmt.Sprintf("/%s/%d", kind, id), q, &d); err != nil {
		if err == errNotFound {
			return nil, nil
		}
		return nil, err
	}
	date := firstNonEmpty(d.ReleaseDate, d.FirstAirDate)
	m := &meta{TMDB: id, Title: firstNonEmpty(d.Title, d.Name, title), Year: year, Runtime: d.Runtime,
		Genres: []string{}, Vote: math.Round(d.VoteAverage*10) / 10, Overview: d.Overview}
	if len(date) >= 4 {
		if y, err := strconv.Atoi(date[:4]); err == nil {
			m.Year = &y
		}
	}
	if m.Runtime == 0 && len(d.EpisodeRunTime) > 0 {
		m.Runtime = d.EpisodeRunTime[0]
	}
	for i, g := range d.Genres {
		if i < 3 {
			m.Genres = append(m.Genres, g.Name)
		}
	}
	m.Poster = t.image(d.PosterPath, "w342")
	m.Backdrop = t.image(d.BackdropPath, "w1280")
	m.Logo = t.image(bestLogo(d.Images.Logos), "w500")
	return m, nil
}

// season: os episódios de uma temporada (nome, sinopse, duração, imagem).
func (t *tmdbClient) season(id, n int) (map[string]episodeMeta, error) {
	var d struct {
		Episodes []struct {
			EpisodeNumber int    `json:"episode_number"`
			Name          string `json:"name"`
			Overview      string `json:"overview"`
			Runtime       int    `json:"runtime"`
			StillPath     string `json:"still_path"`
		} `json:"episodes"`
	}
	if err := t.get(fmt.Sprintf("/tv/%d/season/%d", id, n), url.Values{"language": {"pt-BR"}}, &d); err != nil && err != errNotFound {
		return nil, err
	}
	out := map[string]episodeMeta{}
	for _, e := range d.Episodes {
		// w780 e não w300: o card do episódio na TV tem 420 px de largura
		out[strconv.Itoa(e.EpisodeNumber)] = episodeMeta{Name: e.Name, Overview: e.Overview, Runtime: e.Runtime,
			Still: t.image(e.StillPath, "w780")}
	}
	return out, nil
}

func firstNonEmpty(s ...string) string {
	for _, x := range s {
		if x != "" {
			return x
		}
	}
	return ""
}
