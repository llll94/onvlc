package main

import (
	"log"
	"path/filepath"
	"strings"
	"unsafe"

	"golang.org/x/sys/windows"
)

// watchFolder pede ao Windows para avisar de qualquer mudança na pasta (e subpastas): arquivo novo, renomeado,
// apagado, crescendo. Cada aviso manda a pasta afetada pelo canal. Sem polling: parado, não gasta nada.
func watchFolder(root string, out chan<- string) {
	p, _ := windows.UTF16PtrFromString(root)
	h, err := windows.CreateFile(p, windows.FILE_LIST_DIRECTORY,
		windows.FILE_SHARE_READ|windows.FILE_SHARE_WRITE|windows.FILE_SHARE_DELETE, nil,
		windows.OPEN_EXISTING, windows.FILE_FLAG_BACKUP_SEMANTICS, 0)
	if err != nil {
		log.Printf("não consegui vigiar a pasta (fica só a conferência a cada 10 min): %v", err)
		return
	}
	defer windows.CloseHandle(h)
	const filter = windows.FILE_NOTIFY_CHANGE_FILE_NAME | windows.FILE_NOTIFY_CHANGE_DIR_NAME |
		windows.FILE_NOTIFY_CHANGE_SIZE | windows.FILE_NOTIFY_CHANGE_LAST_WRITE
	buf := make([]byte, 64*1024)
	for {
		var n uint32
		if err := windows.ReadDirectoryChanges(h, &buf[0], uint32(len(buf)), true, filter, &n, nil, 0); err != nil {
			log.Printf("vigia da pasta parou: %v", err)
			return
		}
		if n == 0 { // muitos avisos de uma vez: o Windows descartou; confere tudo
			out <- ""
			continue
		}
		for off := uint32(0); ; {
			info := (*windows.FileNotifyInformation)(unsafe.Pointer(&buf[off]))
			name := windows.UTF16ToString(unsafe.Slice(&info.FileName, info.FileNameLength/2))
			full := filepath.Join(root, name)
			dir := filepath.Dir(full)
			// mudança dentro de Subs/Legendas vale para a pasta do vídeo
			if subDirs[strings.ToLower(filepath.Base(dir))] {
				dir = filepath.Dir(dir)
			}
			out <- strings.ToLower(dir)
			if info.NextEntryOffset == 0 {
				break
			}
			off += info.NextEntryOffset
		}
	}
}
