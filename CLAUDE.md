# onVLC — guia para quem mexe no código

Player de vídeo para Windows com o motor do VLC, uma biblioteca de filmes que roda no PC e um app para Android TV
que toca os filmes do PC pela rede de casa. Código em português (nomes, comentários, mensagens).

Notas do mantenedor (rede de casa, aparelhos, histórico de sessões) ficam fora do repositório.

---

## As três partes

1. **onVLC do PC** (`main.py` + `ui\Overlay.qml`): janela sem borda, python-vlc por baixo e uma camada QML transparente
   por cima (linha do tempo com miniatura, menu lateral, legendas do OpenSubtitles, Assistir na TV). A **biblioteca**
   (`ui\Library.qml`, o mesmo desenho da tela da TV) aparece quando abre sem vídeo ou por Ctrl+B. O assistente
   **Configurar a TV** (`ui\Setup.qml` + `tvsetup.py`) acha a TV, pareia, instala o app nela e testa.
2. **Biblioteca** (`server\`, em Go, `onvlc-biblioteca.exe`): processo separado, sem janela (ícone na bandeja), que lê a
   pasta `Vídeos\onVLC` (vigiada pelo Windows) e busca capas no TMDB. Serve a TV e o PC na porta **8780**. Uma tarefa
   do Windows ("onVLC biblioteca") mantém ela aberta: no login, ao voltar da suspensão e a cada 1 min se tiver fechado.
3. **App da TV** (`android\`, pacote **`app.onvlc.tv`**): tela inicial com a biblioteca (página `tvui\` num WebView) e
   player próprio com libVLC. Cache em blocos (`CacheServer`) e "kits" de abertura para o filme começar rápido.

---

## Segurança (leia antes de mexer em rede, TV ou instalador)

Revisão completa em 01/11/2026; o modelo de hoje:

- **Chave da biblioteca.** Na primeira execução a biblioteca cria um segredo aleatório em
  `%LOCALAPPDATA%\onVLC\library\secret` (permissões do perfil: o usuário, o sistema e administradores). Todo pedido exige `X-onVLC-Key: <segredo>`, menos
  `/api/hello`, `/tv/*` e `/img/*` (`server\auth.go`). O player lê o arquivo (`library.key()`); a TV recebe o segredo
  pelo assistente.
- **A TV reconhece o PC.** `/api/hello?nonce=N` devolve `auth = HMAC-SHA256(segredo, "hello:"+N)`; a TV
  (`PcFinder.hello`) só aceita um PC que acerte. Sem segredo guardado, a TV não aceita PC nenhum e pede para configurar.
- **Só o adb manda na TV.** Pedidos do PC (onde ele está + chave, tocar, parar, teste) entram pela `PairActivity`, que
  exige a permissão `DUMP` (só o shell do adb tem). Ela repassa à `LibraryActivity` com um token do processo; a
  `LibraryActivity` (exportada por causa do lançador) ignora extras sem esse token. Não existe link `onvlc://`.
  `PcFinder.use` só aceita IPv4 privado da mesma rede da TV.
- **Host e origem.** A biblioteca recusa `Host` que não seja o IP por onde o pedido entrou, `localhost` ou o nome do PC
  (contra DNS rebinding) e qualquer origem fora de loopback/rede privada. Rotas "só do PC" (`/api/media`,
  `/api/thumbs?path=`, `/api/clients`, `GET /api/selftest/result`, `/api/reload-keys`) exigem também `127.0.0.1`.
  Caminhos vindos do player: só absolutos com letra de unidade (`localPath`), nunca `\\servidor\...`.
- **WebView da TV.** Só carrega `http://127.0.0.1:8090/<TOKEN>/...` (o `CacheServer`, com um token aleatório por
  processo nas rotas); navegação e recursos de fora são bloqueados (`shouldOverrideUrlLoading` /
  `shouldInterceptRequest`); CSP na `tvui\index.html`; depuração do WebView só na build `--debug`.
- **Windows.** Programas do sistema sempre por caminho completo (`System32`; o `git` das ferramentas de build vem do PATH); PowerShell elevado só com
  `-EncodedCommand` e índice da placa (`tvsetup.run_elevated`); senhas (TMDB, OpenSubtitles) só no Gerenciador de
  Credenciais, sem fallback em texto puro (a chave da biblioteca e o certificado da TV são arquivos do perfil); regra do firewall só TCP 8780, `remoteip=localsubnet`, perfil Particular.
- **Assinatura do app da TV.** Chave **fora do repositório** (`%LOCALAPPDATA%\onVLC-dev\onvlc-tv.keystore`, senha no
  Gerenciador de Credenciais `onVLC`/`apk_keystore_v2`), certificado SHA-256
  `5a4de354aed556f11f200ce518e66b87fb4f86918ef2c47cc0ec6d48d074e0e4`. A chave anterior (pacote `com.lucas.tvcache`) vazou
  e foi aposentada; o assistente desinstala o app antigo ao instalar o novo. **Perder a chave atual = as TVs não
  aceitam mais atualização.** Guardar cópia fora do PC.
- **Build.** ffmpeg e o AAR do libVLC conferidos por SHA-256; VLC e adb conferidos por assinatura digital (VideoLAN,
  Google); dependências Python com hash (`requirements.txt`, `--require-hashes`).
- **Aberto sem chave, de propósito:** `/api/hello` (nome do PC, MAC), a página `/tv/*` e as imagens `/img/*` (capas e
  miniaturas da linha do tempo; o nome da miniatura de arquivo fora da biblioteca é um HMAC do caminho).
- Ainda **sem TLS** entre TV e PC (o segredo viaja em claro na rede de casa) e **sem assinatura de código** no instalador
  (SmartScreen avisa). Ver `SECURITY.md` (inclui o que vai para o TMDB e o OpenSubtitles).

---

## Arquivos

| Arquivo | O que faz |
|---|---|
| `main.py` | Player do PC (classe `Player`); estado por avisos do VLC; legendas e miniaturas vêm da biblioteca (`/api/media`, `/api/thumbs?path=`); `--registrar` / `--desregistrar` / `--apagar-dados` (o instalador chama, sem janela) |
| `ui\Overlay.qml`, `ui\Library.qml`, `ui\Setup.qml` | Interface do player, biblioteca do PC (pede `/api/library` com `X-onVLC-Key` via `player.libraryKey()`), assistente |
| `library.py` | Lado do player: `LIB_DIR`, `key()`, `request()`/`get_json()` (pedidos com a chave), `start_detached()` |
| `tvsetup.py` | Assistente: achar (`tvdevices.discover`), código (controle remoto), instalar (`adb push` + `pm install -r --user current`, com progresso; tira o pacote antigo), conectar (`PairActivity` com `--es pc … --es key …`), teste (ping, `--ez selftest true`, Wake-on-LAN, aviso de rede Pública), TMDB, OpenSubtitles |
| `tvdevices.py` | TVs salvas em `%LOCALAPPDATA%\onVLC\tvs.json`; descoberta por mDNS + ARP; `Remote` = protocolo do controle remoto do Google TV (`androidtvremote2`), certificado em `%LOCALAPPDATA%\onVLC\remote\` |
| `tvstream.py` | `TVBox`: adb (o de dentro do pacote), Wake-on-LAN, HDMI-CEC, `open_library`/`stop_library` pela `PairActivity`; `close()` fecha o servidor do adb |
| `winsetup.py` | "Abrir com", tarefa da biblioteca (XML escapado), `unregister`, `wipe_data` |
| `tvfolder.py`, `thumbs.py`, `opensubs.py` | Painel "Pasta onVLC"; miniaturas locais (biblioteca fora); OpenSubtitles (link de download só `https`) |
| `server\auth.go` | Segredo, HMAC, `hostOK`, `privateSource`, `localPath`, log rotativo |
| `server\http.go`, `setup.go` | Rotas (ver abaixo); assistente (`/api/clients`, `/api/reload-keys`, `/api/selftest`, `/api/speed`, `/api/selftest/result`) |
| `server\library.go`, `listing.go`, `names.go`, `probe.go`, `thumbs.go`, `tmdb.go`, `positions.go`, `watch.go`, `main.go` | Índice e varredura, listagem (caminhos só para o PC), legendas (só arquivos comuns), ffprobe (`-i`), miniaturas, TMDB (token atômico, nomes de imagem validados), posições no registro, vigia da pasta, porta exclusiva + limite de 64 conexões |
| `tvui\` | Página da biblioteca e do player da TV (texto sempre por `esc()`) |
| `android\src\app\onvlc\tv\` | `LibraryActivity` (WebView + ponte `window.onvlc`), `PairActivity`, `PcFinder`, `CacheServer`, `VideoPlayer`, `SelfTest`, `Progress`, `CacheService` (`mediaPlayback`) |
| `tools\build.py` | Gera `dist\onVLC\` com tudo dentro: biblioteca, `vlc\` (do VLC instalado ou de `build\vlc`), `ffmpeg\`, `adb\` (com `NOTICE.txt`), `tv\` (APK), `licencas\` (licença de cada pacote Python + textos GPL/LGPL de `licenses\`), `LICENSE`, `THIRD_PARTY.md`; `--sem-registrar` só gera |
| `tools\build_tvcache.py` | APK sem Gradle (javac → d8 → aapt2 → zipalign → apksigner); `--debug` liga a depuração do WebView; `--no-install` só gera; licenças em `assets/licencas/` |
| `tools\build_installer.py` + `tools\onVLC.iss` | `dist\instalar-onVLC.exe` (Inno Setup): por usuário por padrão ou "para todos" (Arquivos de Programas); firewall estreito; desinstalar pergunta se apaga os dados |
| `tools\release.py` | Release no GitHub (rascunho por padrão) com instalador, APK e `SHA256SUMS.txt`; confere o certificado do APK |
| `tools\tvjs.py` | Roda JS na página da TV (só com a build `--debug`) |

## API da biblioteca (porta 8780)

Todas exigem `X-onVLC-Key`, menos as marcadas *aberta*.

`GET /api/hello[?nonce=]` *aberta* (nome do PC, MAC, `auth`) · `GET /api/library` (sem `path`/`folder` para quem não é o
PC) · `GET /api/state` · `GET /api/media?path=` (PC) · `GET /api/thumbs/<id>` · `GET /api/thumbs?path=&dur=` (PC) ·
`GET /img/<arquivo>` *aberta* · `GET /sub/<id>/<n>` (só arquivo comum, até 10 MB) · `GET /v/<id>/<tamanho>/<nome>` (Range) ·
`GET /tv/*` *aberta* (`/tv/api/*`, `/tv/img/*`, `/tv/sub/*` também) · `POST /api/progress {id,pos,dur}` ·
`POST /api/watched {id|path}` · assistente: `GET /api/clients` (PC), `POST /api/reload-keys` (PC), `GET /api/selftest`,
`GET /api/speed?mb=`, `POST /api/selftest/result` (campos fixos), `GET /api/selftest/result` (PC).

## Dados

- `%LOCALAPPDATA%\onVLC\`: `onvlc.log`, `biblioteca.log` (1 MB, trocado em execução), `library\` (`index.json`,
  `assistidos.json`, `img\`, `secret`, `selftest.mp4`), `thumbs\`, `tvs.json`, `remote\`.
- Posições: registro `HKCU\Software\PlayerLucas\Player` (`pos/<md5>`, `posAt/…`), as mesmas no PC e na TV.
- Credenciais (serviço `onVLC`): `tmdb_token`, `api_key`/`password` (OpenSubtitles).
- "Assistido" = chegou nos últimos 6% ou 2 min.

## Como gerar e testar

- `python -m pip install --require-hashes -r requirements.txt` (Python 3.12). Go 1.27. JDK 17 + Android SDK (build-tools 34).
- Pacote do PC: `python tools\build.py` (pausa a tarefa, fecha biblioteca e player, refaz atalhos e registro).
  Instalador: `python tools\build_installer.py`. Release: `python tools\release.py [--testar | --publicar]`.
- App da TV: `python tools\build_tvcache.py [--debug] [--no-install]`.
- Biblioteca lado a lado para testes: `ONVLC_PORT=8781 LOCALAPPDATA=<pasta> ONVLC_LIBRARY=<pasta> onvlc-biblioteca.exe`.
- Instalador em silêncio: pelo **PowerShell** (`/VERYSILENT /DIR=... /TASKS=""`); no Git Bash as opções `/X` viram caminhos.
- Telas QML sem abrir janela: `QT_QPA_PLATFORM=offscreen` + `QT_QPA_FONTDIR=C:\Windows\Fonts` + `grabWindow()`.
- Antes de qualquer teste na TV: conferir se ela já está em uso (porta 5555 aberta / app na frente) e não mexer se estiver.

## Pendências

- TLS (ou ao menos cifrar) entre TV e PC.
- Legendas automáticas no Go (hoje o OpenSubtitles é só no player do PC); chave de app sem embutir no binário.
- Testar o passo a passo "Build 7 vezes" numa TV com a depuração desligada.
