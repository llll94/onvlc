package main

// Desligar a biblioteca de verdade.
//
// A tarefa do Windows abre a biblioteca a cada minuto, para a TV sempre achar o PC. Quando a pessoa desliga pelo
// ícone perto do relógio (ou pelo menu do onVLC), fica gravado em library\desligada e a biblioteca, ao abrir, sai na
// hora sem fazer nada:
//   - "sempre": até a pessoa ligar de novo (menu TV → Biblioteca da TV, no onVLC);
//   - "boot:<segundos>": até o PC reiniciar (guarda a hora em que o Windows ligou; outra hora = outro boot).
// O player do PC (library.py) lê e grava o mesmo arquivo.

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

var pausedF = filepath.Join(dataDir, "desligada")

// bootTime: quando o Windows ligou (segundos desde 1970). Suspender e voltar não muda; reiniciar muda.
func bootTime() int64 {
	r, _, _ := syscall.NewLazyDLL("kernel32.dll").NewProc("GetTickCount64").Call()
	return time.Now().Add(-time.Duration(r) * time.Millisecond).Unix()
}

// pausedNow: a pessoa desligou a biblioteca (e, se foi "até reiniciar", o PC ainda não reiniciou).
func pausedNow() bool {
	raw, err := os.ReadFile(pausedF)
	if err != nil {
		return false
	}
	s := strings.TrimSpace(string(raw))
	if s == "sempre" {
		return true
	}
	if b, err := strconv.ParseInt(strings.TrimPrefix(s, "boot:"), 10, 64); err == nil && strings.HasPrefix(s, "boot:") {
		if d := b - bootTime(); d > -120 && d < 120 { // mesmo boot (a conta varia alguns segundos)
			return true
		}
	}
	os.Remove(pausedF) // reiniciou (ou arquivo estranho): volta ao normal
	return false
}

// pause grava a escolha da pessoa: untilReboot = só até reiniciar o PC.
func pause(untilReboot bool) {
	v := "sempre"
	if untilReboot {
		v = "boot:" + strconv.FormatInt(bootTime(), 10)
	}
	os.MkdirAll(dataDir, 0o700)
	os.WriteFile(pausedF, []byte(v+"\n"), 0o600)
}
