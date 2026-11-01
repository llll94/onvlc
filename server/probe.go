package main

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

const (
	noWindow    = 0x08000000 // CREATE_NO_WINDOW: ffmpeg sem janela preta
	belowNormal = 0x00004000 // prioridade baixa: não disputa com o vídeo tocando
)

var toolCache sync.Map

// findTool: ffmpeg/ffprobe da pasta ffmpeg ao lado do .exe, do PATH ou do pacote do winget (procurado uma vez só).
func findTool(name string) string {
	if v, ok := toolCache.Load(name); ok {
		return v.(string)
	}
	// o que vai dentro do onVLC (pasta ffmpeg ao lado da biblioteca) vem primeiro
	if exe, err := os.Executable(); err == nil {
		own := filepath.Join(filepath.Dir(exe), "ffmpeg", name+".exe")
		if _, err := os.Stat(own); err == nil {
			toolCache.Store(name, own)
			return own
		}
	}
	found, err := exec.LookPath(name)
	if err != nil {
		found = ""
		pkgs := filepath.Join(os.Getenv("LOCALAPPDATA"), "Microsoft", "WinGet", "Packages")
		if hits, _ := filepath.Glob(filepath.Join(pkgs, "Gyan.FFmpeg*", "*", "bin", name+".exe")); len(hits) > 0 {
			found = hits[0]
		}
	}
	toolCache.Store(name, found)
	return found
}

func command(ctx context.Context, low bool, exe string, args ...string) *exec.Cmd {
	c := exec.CommandContext(ctx, exe, args...)
	flags := uint32(noWindow)
	if low {
		flags |= belowNormal
	}
	c.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: flags}
	return c
}

type probeInfo struct {
	Dur     float64
	Quality string
	HDR     bool
	Audio   []string
	Subs    []string // do próprio arquivo
}

// probe: duração, qualidade, áudios e legendas; nil se o arquivo não abre.
func probe(path string) *probeInfo {
	exe := findTool("ffprobe")
	if exe == "" {
		return &probeInfo{}
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	out, err := command(ctx, false, exe, "-v", "error", "-show_entries",
		"format=duration:stream=codec_type,height,width,channels,color_transfer:stream_tags=language",
		"-of", "json", path).Output()
	if err != nil && len(out) == 0 {
		return nil
	}
	var data struct {
		Format struct {
			Duration string `json:"duration"`
		} `json:"format"`
		Streams []struct {
			CodecType     string            `json:"codec_type"`
			Height        int               `json:"height,omitempty"`
			Width         int               `json:"width,omitempty"`
			Channels      int               `json:"channels,omitempty"`
			ColorTransfer string            `json:"color_transfer,omitempty"`
			Tags          map[string]string `json:"tags,omitempty"`
		} `json:"streams"`
	}
	if json.Unmarshal(out, &data) != nil {
		return nil
	}
	dur, _ := strconv.ParseFloat(data.Format.Duration, 64)
	info := &probeInfo{Dur: dur, Audio: []string{}, Subs: []string{}}
	video := -1
	for i, s := range data.Streams {
		if s.CodecType == "video" {
			video = i
			break
		}
	}
	if video < 0 || dur <= 0 {
		return nil
	}
	v := data.Streams[video]
	h, w := v.Height, v.Width
	switch {
	case h >= 1600 || w >= 3200:
		info.Quality = "4K"
	case h >= 900 || w >= 1800:
		info.Quality = "1080p"
	case h >= 650:
		info.Quality = "720p"
	default:
		info.Quality = "SD"
	}
	if v.ColorTransfer == "smpte2084" || v.ColorTransfer == "arib-std-b67" {
		info.Quality += " HDR"
		info.HDR = true
	}
	channels := map[int]string{1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1"}
	for _, s := range data.Streams {
		lang := langNames[strings.ToLower(s.Tags["language"])]
		switch s.CodecType {
		case "audio":
			name := lang
			if name == "" {
				name = "Áudio"
			}
			if ch := channels[s.Channels]; ch != "" {
				name += " " + ch
			}
			info.Audio = append(info.Audio, name)
		case "subtitle":
			if lang == "" {
				lang = "Legenda"
			}
			info.Subs = append(info.Subs, lang)
		}
	}
	return info
}

// HDR (PQ/HLG) sem conversão sai lavado; isto leva para cores normais
func hdrFilter(width int) string {
	return "zscale=w=" + strconv.Itoa(width) + ":h=-2:t=linear:npl=100,format=gbrpf32le,zscale=p=bt709," +
		"tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
}

// grabFrame: um quadro do próprio vídeo, usado quando o TMDB não tem imagem. at = segundo do quadro.
func grabFrame(path string, at float64, hdr bool, width int, out string) bool {
	if _, err := os.Stat(out); err == nil {
		return true
	}
	ffmpeg := findTool("ffmpeg")
	if ffmpeg == "" {
		return false
	}
	vf := "scale=" + strconv.Itoa(width) + ":-2"
	if hdr {
		vf = hdrFilter(width)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()
	tmp := strings.TrimSuffix(out, ".jpg") + ".tmp.jpg"
	_ = command(ctx, true, ffmpeg, "-hide_banner", "-v", "error", "-y", "-skip_frame", "nokey",
		"-ss", strconv.FormatFloat(at, 'f', 1, 64), "-i", path, "-map", "0:v:0", "-frames:v", "1",
		"-vf", vf, "-q:v", "4", tmp).Run()
	if st, err := os.Stat(tmp); err == nil && st.Size() > 0 {
		return os.Rename(tmp, out) == nil
	}
	os.Remove(tmp)
	return false
}
