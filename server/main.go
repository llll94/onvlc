package main

import (
	"context"
	_ "embed"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"runtime/debug"
	"strings"
	"syscall"
	"time"

	"fyne.io/systray"
	"golang.org/x/sys/windows"
)

//go:embed icon.ico
var icon []byte

func main() {
	os.MkdirAll(appData, 0o755)
	setupLog()
	// processo que fica ligado o dia todo: o coletor de memória trabalha mais cedo e devolve ao Windows
	debug.SetGCPercent(50)
	debug.SetMemoryLimit(32 << 20)
	ln, err := listen()
	if err != nil { // a porta já é de outra biblioteca: uma só por PC
		log.Printf("biblioteca já está rodando; saindo (%v)", err)
		return
	}
	lib := newLibrary()
	srv := &http.Server{Handler: &server{lib: lib, ui: uiDir()}, ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout: 2 * time.Minute, ErrorLog: log.New(discard{}, "", 0)}
	go srv.Serve(ln)
	log.Printf("biblioteca na porta %s, pasta %s, página %s", port, libDir, uiDir())
	go lib.watch()
	systray.Run(func() { tray(lib) }, func() {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		srv.Shutdown(ctx)
	})
}

// listen abre a porta em modo exclusivo: no Windows, sem isso, outro programa pode abrir a mesma porta
// e os pedidos caírem no lugar errado.
func listen() (net.Listener, error) {
	lc := net.ListenConfig{Control: func(_, _ string, c syscall.RawConn) error {
		var serr error
		c.Control(func(fd uintptr) {
			serr = windows.SetsockoptInt(windows.Handle(fd), windows.SOL_SOCKET, ^windows.SO_REUSEADDR, 1) // SO_EXCLUSIVEADDRUSE
		})
		return serr
	}}
	return lc.Listen(context.Background(), "tcp4", "0.0.0.0:"+port)
}

// watch: o Windows avisa das mudanças; um vídeo chegando é olhado de novo em 5 s até parar de crescer.
func (l *Library) watch() {
	events := make(chan string, 256)
	go watchFolder(libDir, events)
	waiting := l.scan(nil)
	full := time.NewTicker(fullScan)
	for {
		var dirty map[string]bool
		var soon <-chan time.Time
		if waiting {
			soon = time.After(5 * time.Second)
		}
		select {
		case d := <-events:
			dirty = map[string]bool{}
			collect := func(d string) {
				if d == "" {
					dirty = nil
				} else if dirty != nil {
					dirty[d] = true
				}
			}
			collect(d)
			for settle := time.After(settleDelay); ; {
				select {
				case d := <-events:
					collect(d)
					continue
				case <-settle:
				}
				break
			}
		case <-soon:
			dirty = map[string]bool{}
		case <-full.C:
			dirty = nil
		}
		func() {
			defer func() {
				if e := recover(); e != nil {
					log.Printf("erro olhando a pasta: %v", e)
				}
			}()
			waiting = l.scan(dirty)
		}()
		debug.FreeOSMemory()
	}
}

func tray(lib *Library) {
	systray.SetIcon(icon)
	info := systray.AddMenuItem("Biblioteca da TV", "")
	info.Disable()
	folder := systray.AddMenuItem("Abrir a pasta da biblioteca", "")
	systray.AddSeparator()
	quit := systray.AddMenuItem("Desligar a biblioteca da TV", "")
	refresh := func() {
		lib.mu.Lock()
		movies, shows := 0, map[string]bool{}
		for _, r := range lib.files {
			if r.Kind == "movie" {
				movies++
			} else {
				shows[firstNonEmpty(r.ShowKey, r.Title)] = true
			}
		}
		busy := lib.busy
		lib.mu.Unlock()
		txt := plural(movies, "filme", "filmes")
		if len(shows) > 0 {
			txt += ", " + plural(len(shows), "série", "séries")
		}
		if busy > 0 {
			txt += " · lendo " + plural(busy, "novo", "novos")
		}
		info.SetTitle("Biblioteca da TV: " + txt)
		systray.SetTooltip("onVLC · biblioteca da TV\n" + txt)
	}
	refresh()
	changes := make(chan struct{}, 1)
	lib.onChange = func() {
		select {
		case changes <- struct{}{}:
		default:
		}
	}
	go func() {
		for {
			select {
			case <-changes:
				refresh()
			case <-folder.ClickedCh:
				exec.Command("explorer", libDir).Start()
			case <-quit.ClickedCh:
				systray.Quit()
				return
			}
		}
	}()
}

func plural(n int, one, many string) string {
	if n == 1 {
		return fmt.Sprintf("%d %s", n, one)
	}
	return fmt.Sprintf("%d %s", n, many)
}

// setupLog: biblioteca.log, até 1 MB (o anterior fica em biblioteca.log.1).
func setupLog() {
	if st, err := os.Stat(logF); err == nil && st.Size() > 1<<20 {
		os.Rename(logF, logF+".1")
	}
	f, err := os.OpenFile(logF, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	log.SetOutput(f)
	// se a biblioteca cair por um erro grave, o motivo fica no registro (sem janela, não há outro lugar para ver)
	debug.SetCrashOutput(f, debug.CrashOptions{})
	log.SetFlags(log.Ldate | log.Ltime)
	log.SetPrefix("biblioteca ")
}

type discard struct{}

func (discard) Write(p []byte) (int, error) {
	if s := string(p); strings.Contains(s, "panic") {
		log.Print(s)
	}
	return len(p), nil
}
