# Programas de terceiros distribuídos com o onVLC

O onVLC é licenciado sob a GPL-3.0-or-later (arquivo LICENSE). Os componentes abaixo vão junto no instalador ou no app
da TV, cada um com a sua licença. O texto de cada licença acompanha o componente (pastas `vlc\` → `COPYING.txt`,
`ffmpeg\` → `LICENSE`, `adb\` → `NOTICE.txt`, `licencas\` → uma pasta por pacote Python, a licença do Python e os textos da GPL-2.0, GPL-3.0,
LGPL-2.1 e LGPL-3.0, que valem para o Qt, a libvlc e os pacotes que não trazem o texto).

| Componente | Onde | Licença | Código-fonte |
|---|---|---|---|
| VLC media player (libvlc, libvlccore e plugins) 3.0.x | PC: pasta `vlc\` | LGPL-2.1-or-later (libvlc) e GPL-2.0-or-later (plugins) | https://code.videolan.org/videolan/vlc |
| libVLC para Android (libvlc-all) 3.7.7 | App da TV | LGPL-2.1-or-later / GPL-2.0-or-later | https://code.videolan.org/videolan/vlc-android |
| FFmpeg 9.0.2 ("essentials", build de Gyan Doshi) | PC: pasta `ffmpeg\` | GPL-3.0-or-later (build com bibliotecas GPL) | https://ffmpeg.org/releases/ e https://github.com/GyanD/codexffmpeg |
| Android Debug Bridge (adb) 37.0.1, platform-tools | PC: pasta `adb\` | Apache-2.0 | https://android.googlesource.com/platform/packages/modules/adb |
| Qt 6 / PySide6 6.12 | PC: `_internal\` | LGPL-3.0 | https://code.qt.io/ |
| Python 3.12 | PC: `_internal\` | PSF License | https://www.python.org/ |
| python-vlc | PC | LGPL-2.1-or-later | https://github.com/oaubert/python-vlc |
| androidtvremote2 | PC | Apache-2.0 | https://github.com/tronikos/androidtvremote2 |
| python-zeroconf | PC | LGPL-2.1-or-later | https://github.com/python-zeroconf/python-zeroconf |
| keyring | PC | MIT | https://github.com/jaraco/keyring |
| cryptography | PC | Apache-2.0 / BSD-3-Clause | https://github.com/pyca/cryptography |
| protobuf | PC | BSD-3-Clause | https://github.com/protocolbuffers/protobuf |
| aiofiles | PC | Apache-2.0 | https://github.com/Tinche/aiofiles |
| fyne.io/systray | Biblioteca (Go) | Apache-2.0 | https://github.com/fyne-io/systray |
| golang.org/x/sys, golang.org/x/net | Biblioteca (Go) | BSD-3-Clause | https://go.googlesource.com/ |
| Go (runtime) | Biblioteca | BSD-3-Clause | https://go.dev/ |
| Inno Setup (só gera o instalador) | — | Licença do Inno Setup | https://jrsoftware.org/isinfo.php |

Dados de filmes e séries: [TMDB](https://www.themoviedb.org/). Este produto usa a API do TMDB, mas não é endossado nem
certificado pelo TMDB. Legendas: [OpenSubtitles](https://www.opensubtitles.com/).

"VLC" é marca do VideoLAN. O onVLC não é afiliado ao VideoLAN.
