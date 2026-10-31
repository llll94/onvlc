package main

import (
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"golang.org/x/sys/windows/registry"
)

// A posição de cada vídeo fica no mesmo lugar em que o player do PC guarda (QSettings PlayerLucas/Player):
// HKCU\Software\PlayerLucas\Player\pos\<md5 do caminho em minúsculas> = segundos, e posAt\<…> = quando foi.
const regBase = `Software\PlayerLucas\Player`

type posStore struct{}

var positions posStore

func posKey(path string) string {
	return md5hex(strings.ToLower(filepath.Clean(path)))
}

func readFloat(sub, name string) float64 {
	k, err := registry.OpenKey(registry.CURRENT_USER, regBase+`\`+sub, registry.QUERY_VALUE)
	if err != nil {
		return 0
	}
	defer k.Close()
	s, _, err := k.GetStringValue(name)
	if err != nil {
		return 0
	}
	f, _ := strconv.ParseFloat(s, 64)
	return f
}

func writeFloat(sub, name string, v float64) {
	k, _, err := registry.CreateKey(registry.CURRENT_USER, regBase+`\`+sub, registry.SET_VALUE)
	if err != nil {
		return
	}
	defer k.Close()
	k.SetStringValue(name, strconv.FormatFloat(v, 'f', -1, 64))
}

func deleteValue(sub, name string) {
	k, err := registry.OpenKey(registry.CURRENT_USER, regBase+`\`+sub, registry.SET_VALUE)
	if err != nil {
		return
	}
	defer k.Close()
	k.DeleteValue(name)
}

// get: onde parou (segundos) e quando (para a ordem do "Continuar assistindo").
func (posStore) get(path string) (pos, at float64) {
	k := posKey(path)
	return readFloat("pos", k), readFloat("posAt", k)
}

func (posStore) set(path string, pos float64) {
	k := posKey(path)
	writeFloat("pos", k, pos)
	writeFloat("posAt", k, float64(time.Now().UnixNano())/1e9)
}

func (posStore) clear(path string) {
	k := posKey(path)
	deleteValue("pos", k)
	deleteValue("posAt", k)
}
