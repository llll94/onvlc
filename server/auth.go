package main

// Quem pode falar com a biblioteca.
//
// Segredo: um valor aleatório criado na primeira execução, em %LOCALAPPDATA%\onVLC\library\secret (só o usuário
// lê). O player do PC lê o mesmo arquivo; a TV recebe o segredo pelo assistente (canal do adb, confiável) e guarda.
// Quase toda rota exige o cabeçalho "X-onVLC-Key: <segredo>". Ficam abertas só:
//   - /api/hello (para a TV achar o PC; com ?nonce= responde auth = HMAC(segredo, nonce), e assim a TV sabe que é
//     o PC de verdade antes de mandar o segredo para ele),
//   - /tv/* (a página, sem dados) e /img/* (capas e miniaturas; o nome da miniatura de um arquivo fora da
//     biblioteca é um HMAC do caminho, não dá para adivinhar).
//
// Host: o cabeçalho Host precisa ser o IP por onde o pedido chegou (ou localhost / o nome do PC). Isso impede que
// uma página na internet, aberta no navegador do PC, fale com a biblioteca por "DNS rebinding".
// Origem: só a rede local (loopback, 10/8, 172.16/12, 192.168/16, 169.254/16). A placa pode estar numa VPN.

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"errors"
	"log"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

var (
	secretF    = filepath.Join(dataDir, "secret")
	secretOnce sync.Once
	secretVal  string
)

// secret lê (ou cria) o segredo. Vazio só se o disco falhar; aí nada que precise dele funciona, de propósito.
func secret() string {
	secretOnce.Do(func() {
		if raw, err := os.ReadFile(secretF); err == nil {
			if s := strings.TrimSpace(string(raw)); len(s) >= 32 {
				secretVal = s
				return
			}
		}
		b := make([]byte, 32)
		if _, err := rand.Read(b); err != nil {
			log.Printf("sem segredo: %v", err)
			return
		}
		s := hex.EncodeToString(b)
		os.MkdirAll(dataDir, 0o700)
		if err := os.WriteFile(secretF, []byte(s+"\n"), 0o600); err != nil {
			log.Printf("não consegui gravar o segredo: %v", err)
			return
		}
		secretVal = s
	})
	return secretVal
}

// hasKey: o pedido trouxe o segredo certo (comparação em tempo constante).
func hasKey(r *http.Request) bool {
	s := secret()
	k := r.Header.Get("X-onVLC-Key")
	return s != "" && len(k) == len(s) && subtle.ConstantTimeCompare([]byte(k), []byte(s)) == 1
}

// hmacHex: HMAC-SHA256 do texto com o segredo, em hexadecimal. É o que o /api/hello devolve para um nonce e o que
// dá nome às miniaturas de arquivos fora da biblioteca.
func hmacHex(text string) string {
	m := hmac.New(sha256.New, []byte(secret()))
	m.Write([]byte(text))
	return hex.EncodeToString(m.Sum(nil))
}

// hostOK: o Host do pedido é este PC, visto pelo endereço por onde o pedido entrou.
func hostOK(r *http.Request) bool {
	host := r.Host
	if h, _, err := net.SplitHostPort(host); err == nil {
		host = h
	}
	host = strings.Trim(strings.ToLower(host), "[]")
	if host == "localhost" || host == "127.0.0.1" || host == "::1" {
		return true
	}
	if la, ok := r.Context().Value(http.LocalAddrContextKey).(*net.TCPAddr); ok && la.IP.String() == host {
		return true
	}
	if hn, err := os.Hostname(); err == nil && strings.ToLower(hn) == host {
		return true
	}
	return false
}

// privateSource: o pedido veio da rede local (não de um endereço da internet, por uma placa exposta).
func privateSource(r *http.Request) bool {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		host = r.RemoteAddr
	}
	ip := net.ParseIP(host)
	return ip != nil && (ip.IsLoopback() || ip.IsPrivate() || ip.IsLinkLocalUnicast())
}

// localPath: um caminho de arquivo que o player do PC mandou. Só caminho absoluto com letra de unidade
// (C:\...), nunca de rede (\\servidor\...) nem de dispositivo (\\?\...): abrir um caminho de rede faria o Windows
// se apresentar ao servidor de lá. Devolve o caminho limpo ou um erro.
func localPath(p string) (string, error) {
	p = filepath.Clean(p)
	if strings.HasPrefix(p, `\\`) || strings.HasPrefix(p, "//") || strings.HasPrefix(p, "-") {
		return "", errors.New("caminho de rede ou inválido")
	}
	vol := filepath.VolumeName(p)
	if len(vol) != 2 || vol[1] != ':' || !filepath.IsAbs(p) {
		return "", errors.New("caminho precisa ser absoluto, com letra de unidade")
	}
	st, err := os.Stat(p)
	if err != nil || !st.Mode().IsRegular() {
		return "", errors.New("arquivo não existe")
	}
	return p, nil
}

// rotatingLog: biblioteca.log até 1 MB em execução (não só na abertura); o anterior vira biblioteca.log.1.
type rotatingLog struct {
	mu   sync.Mutex
	f    *os.File
	size int64
	max  int64
}

func openRotatingLog(path string, max int64) (*rotatingLog, error) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return nil, err
	}
	st, _ := f.Stat()
	return &rotatingLog{f: f, size: st.Size(), max: max}, nil
}

func (l *rotatingLog) Write(p []byte) (int, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.size+int64(len(p)) > l.max {
		l.f.Close()
		os.Remove(logF + ".1")
		os.Rename(logF, logF+".1")
		f, err := os.OpenFile(logF, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
		if err != nil {
			return 0, err
		}
		l.f, l.size = f, 0
	}
	n, err := l.f.Write(p)
	l.size += int64(n)
	return n, err
}
