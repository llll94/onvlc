// Biblioteca do onVLC: os filmes e séries da pasta "Vídeos\onVLC" do PC, servidos para a TV e para o player do PC.
//
// Um processo só, sem janela, com um ícone perto do relógio. Substitui o library.py: mesmos arquivos de dados
// (index.json, assistidos.json, img\, thumbs\), mesma porta e mesmas rotas, então a TV não percebe a troca.
//
//   - A pasta é vigiada pelo Windows (ReadDirectoryChangesW): um vídeo novo é lido segundos depois de parar de
//     crescer, sem varrer a pasta o tempo todo. Uma conferência completa acontece a cada 10 min.
//   - Cada vídeo novo passa uma vez pelo ffprobe (duração, qualidade, HDR, áudios, legendas), ganha um quadro do
//     próprio filme e, com a chave do TMDB, título em português, sinopse, capa, fundo e logo.
//   - A posição de cada vídeo continua no registro (HKCU\Software\PlayerLucas\Player, "pos" e "posAt"),
//     a mesma que o player do PC usa: parou na TV, continua no PC, e o contrário.
package main

import (
	"os"
	"path/filepath"
	"time"
)

var port = envOr("ONVLC_PORT", "8780") // outra porta só para testes

const (
	minSize     = 50 << 20 // menos que isso costuma ser "sample" ou trailer
	probeVer    = 4        // sobe quando o probe passa a ler algo novo: os vídeos guardados são lidos de novo
	fullScan    = 10 * time.Minute
	settleDelay = 2 * time.Second // espera depois de um aviso do Windows, para juntar vários de uma vez
	tmdbAPI     = "https://api.themoviedb.org/3"
	tmdbImg     = "https://image.tmdb.org/t/p/"
)

var (
	libDir   = envOr("ONVLC_LIBRARY", filepath.Join(home(), "Videos", "onVLC"))
	appData  = filepath.Join(envOr("LOCALAPPDATA", home()), "onVLC")
	dataDir  = filepath.Join(appData, "library")
	imgDir   = filepath.Join(dataDir, "img")
	indexF   = filepath.Join(dataDir, "index.json")
	historyF = filepath.Join(dataDir, "assistidos.json") // assistidos até o fim, com a data (ficam mesmo se o arquivo sair)
	thumbDir = filepath.Join(appData, "thumbs")
	logF     = filepath.Join(appData, "biblioteca.log")
)

var videoExts = map[string]bool{".mkv": true, ".mp4": true, ".avi": true, ".mov": true, ".webm": true, ".m4v": true,
	".wmv": true, ".flv": true, ".ts": true, ".m2ts": true, ".mpg": true, ".mpeg": true}

var subExts = map[string]bool{".srt": true, ".ass": true, ".ssa": true, ".vtt": true}

var subDirs = map[string]bool{"subs": true, "sub": true, "legendas": true, "legenda": true, "subtitles": true}

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func home() string {
	h, _ := os.UserHomeDir()
	return h
}

// uiDir: a página da TV (pasta tvui). Ao lado do .exe, dentro do pacote do player (_internal) ou no repositório.
func uiDir() string {
	exe, _ := os.Executable()
	base := filepath.Dir(exe)
	for _, d := range []string{filepath.Join(base, "tvui"), filepath.Join(base, "_internal", "tvui"), filepath.Join(base, "..", "tvui")} {
		if st, err := os.Stat(filepath.Join(d, "index.html")); err == nil && !st.IsDir() {
			return d
		}
	}
	return filepath.Join(base, "tvui")
}
