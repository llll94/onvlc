# onVLC — handoff

Projeto pessoal do Lucas . Repositório privado: **github.com/llll94/onvlc** (branch `main`).
Commits como `Lucas Schunck <148644814+llll94@users.noreply.github.com>` (já configurado no repo).
O Lucas não programa: explicar em português simples, sem jargão; mostrar protótipo (Artifact) antes de codar ideias novas
de tela; nunca matar o onVLC dele aberto (pedir para fechar); não mexer no mouse dele; não ligar/usar a TV se ele estiver
assistindo (conferir antes com `tools\tvjs.py`).

Última sessão: 2026-10-31 (refatoração em fases: plano `…\Claude Code\Plans\onvlc-refatoracao.md`). Histórico anterior:
`C:\Users\lucas\Documents\Obsidian Vault\work\Claude Code\Plans\player-video-redesign.md`.
O Lucas autorizou abrir e testar na TV sem chamá-lo (ele deixa o volume baixo); **não abrir janelas nem capturar a tela
do PC** (ele pode estar jogando): para ver telas QML, renderizar fora da tela.
Rede de casa (IPs, MACs, Mi Box): `…\Claude Code\Docs\Rede de casa.md`.

---

## O que é

Um player de vídeo próprio com o motor do VLC, em três partes:

1. **onVLC do PC** (`main.py` + `ui\Overlay.qml`): janela sem borda, python-vlc por baixo e uma camada QML transparente
   por cima (linha do tempo com miniatura, menu lateral, legendas do OpenSubtitles, Assistir na TV).
   A **biblioteca** (mesmo desenho da tela da TV, em QML próprio: `ui\Library.qml`) aparece dentro dele quando abre sem
   vídeo, ou por Ctrl+B / menu Mídia › Biblioteca. Sem navegador embutido desde 2026-10-31 (pacote 587 → 111 MB).
2. **Biblioteca** (`server\`, **em Go**, `onvlc-biblioteca.exe` ~8 MB, ~19 MB de RAM): processo separado, sem janela
   (ícone na bandeja), que lê a pasta **`C:\Users\lucas\Videos\onVLC`** (vigiada pelo Windows, `ReadDirectoryChangesW`,
   + conferência a cada 10 min), busca dados no TMDB e serve tudo na porta **8780** para a TV e para o PC.
   Substituiu o `library.py` em 2026-10-31 com os mesmos arquivos de dados e as mesmas rotas.
   O onVLC abre esse processo ao iniciar; ele continua rodando depois de fechar a janela, até reiniciar o PC
   (o Lucas **não** quis que abrisse com o Windows).
3. **App da Mi Box** (`android\`, pacote `com.lucas.tvcache`, nome "onVLC"): tela inicial da TV com a biblioteca (página
   `tvui\` num WebView) e **player próprio com libVLC 3.7.7** (sem usar o app do VLC). Inclui o cache em blocos (TV Cache).

Ícone: **"cone no ar"** (círculo laranja `#ff8a1f`, cone do VLC branco com ondas saindo da ponta), gerado por
`tools\make_icon.py` (ico/png do PC, ícone e banner da Mi Box, `tvui\icon.svg`).

---

## Arquivos

| Arquivo | O que faz |
|---|---|
| `main.py` | Player do PC (classe `Player`), slots chamados pelo QML; abre a biblioteca (`library.start_detached()`) numa thread antes da janela. Estado por **avisos do VLC** (`_on_vlc`: Playing/Paused/EndReached/LengthChanged/ES*), não por polling: o `tick` (250 ms) só lê a posição com vídeo tocando e janela à vista e emite `posChanged` (propriedade `pos`), sem mandar o estado inteiro; `_changed()` manda o estado quando algo muda. `libraryOpenVideo(path, pos)`. Avisos do Qt/QML vão para o `onvlc.log` |
| `ui\Overlay.qml` | Toda a interface do PC; `libraryOpen` + `Loader` do `Library.qml` (só existe aberta); a linha do tempo usa `player.pos`; menu montado uma vez (`menuList`) |
| `ui\Library.qml` | Biblioteca do PC: canvas 1920×1080 escalado com as medidas do `tvui\style.css`; lê `/api/library` com ETag a cada 30 s (só aberta); 3 fileiras, destaque ao passar o mouse (sem rolar a fileira), rodinha troca de fileira, setas ‹ › / Shift+rodinha andam na fileira, página do filme/série/indisponível com "‹ Voltar" (e botão voltar do mouse/Esc); imagens decodificadas no tamanho do card; arrastar move a janela. Para ver sem abrir janela: `QT_QPA_PLATFORM=offscreen` + `QT_QPA_FONTDIR=C:\Windows\Fonts` + `grabWindow()` (a fonte variável do Windows sai errada nesse modo; usar "Segoe UI" só na prévia) |
| `server\*.go` | Biblioteca em Go: `names.go` (nome do arquivo, legendas, idioma), `probe.go` (ffprobe/ffmpeg), `tmdb.go` (TMDB pt-BR, nova tentativa com espera crescente, chave lida do Gerenciador de Credenciais), `library.go` (índice, varredura, regras `isEnd`/`resumeAt`, assistidos), `listing.go` (`/api/library`), `positions.go` (posições no registro, mesmo lugar do player), `thumbs.go`, `watch.go` (vigia da pasta), `http.go` (rotas), `main.go` (porta exclusiva, bandeja). `ONVLC_PORT` muda a porta (testes) |
| `library.py` | Lado do player: `LIB_DIR`, `is_sample`, `running()`, `start_detached()` (abre o `onvlc-biblioteca.exe`) |
| `tvfolder.py` | Painel "Pasta onVLC" do PC: lista com nome da TV e estado; "Mandar para a TV" = hardlink na pasta (mesmo disco) ou cópia; lixeira; `find_tool` (ffmpeg); `VIDEO_EXTS` |
| `tvstream.py` | **Assistir na TV** (do PC): adb, Wake-on-LAN, liga TV por HDMI-CEC, `open_library(id, pos)` / `stop_library()` = `am start` no `LibraryActivity` com extras `play`/`pos` ou `stop` |
| `thumbs.py` | Miniaturas da linha do tempo (200 quadros-chave, HDR com tonemap), usadas no PC e servidas à TV |
| `opensubs.py` | Legendas do OpenSubtitles (falta o Lucas criar conta/chave) |
| `tvui\index.html`, `app.js`, `style.css` | Página da biblioteca (1920×1080 escalada; sem filtros/máscaras/sombras animadas no CSS; capas `loading=lazy`; a cada 30 s pede `api/state` e só baixa a lista inteira quando muda; destaque não recria o logo). Tela inicial sem abas, só 3 linhas: Continuar assistindo / Mídias (tudo, mais novo primeiro) / Vocês já assistiram (montada uma vez e reaproveitada); sem favoritos. Página do filme/série, indisponível, procurando PC. Modo PC com `?pc=1` |
| `tvui\player.js` | Interface do player da TV (barra única com trecho guardado, miniatura ao procurar, painel áudio/legendas com rolagem, pausado, próximo episódio, nova tentativa se travar) |
| `android\src\…\LibraryActivity.java` | Atividade da tela inicial: WebView transparente + `VideoPlayer` atrás; ponte JS `window.onvlc` (play, prepare, unprepare, toggle, seek, audio, spu, stop, warm, post) |
| `VideoPlayer.java` | libVLC (iniciado em segundo plano quando o app abre): SurfaceView vídeo + legenda, `:network-caching=400`, `:prefetch-read-size=262144`, `:prefetch-buffer-size=4096`, `:start-paused` para preparar, legendas externas como slave; busca rápida (quadro-chave); avisa a página só quando o "carregando" começa/termina; lista de faixas só refeita nos eventos ES* |
| `CacheServer.java` | Servidor local 127.0.0.1:8090: `/stream` (blocos de 8 MB, 5 min à frente, 1 min atrás, 2 GB de vários filmes), **kit de abertura** (até 4 GB, fixado), `/ui/*` (repassa a página e a API para o PC e guarda cópia para quando o PC está desligado; imagens com cache longo), `/status` |
| `PcFinder.java` | Acha o PC: último IP salvo, senão varre x.x.x.1–254 na porta 8780 (`/api/hello`) |
| `Progress.java` | POST da posição para o PC |
| `CacheService.java` | Serviço em primeiro plano durante o filme |
| `tools\build.py` | Compila a biblioteca em Go e gera `dist\onVLC\onVLC.exe` (PyInstaller) com o `onvlc-biblioteca.exe` ao lado; `trim()` apaga do pacote o Qt que não é usado (3D, gráficos, mapas, multimídia, PDF, WebEngine, traduções fora pt-BR, `opengl32sw`): **~111 MB**; atalhos, registro como app de vídeo |
| `tools\build_tvcache.py` | APK sem Gradle (javac → d8 → aapt2 → zipalign -p → apksigner), baixa o AAR do libVLC uma vez em `build\libvlc` (SHA-256 conferido), só `armeabi-v7a`, `.so` sem compressão + `extractNativeLibs=false` (APK 40,6 MB, nada extraído ao instalar), chave em `%LOCALAPPDATA%\onVLC`; instala na Mi Box (`--no-install` para só gerar; com a Mi Box dormindo, mandar WOL em loop durante o `adb install`) |
| `tools\tvjs.py` | Roda JS na página aberta na TV pelo adb (depuração do WebView): `python tools\tvjs.py "expressão"` |
| `tools\make_icon.py`, `set_tmdb_key.py` | Ícone; guardar chave do TMDB |

## API da biblioteca (porta 8780)

`GET /api/hello` (version 2 = Go) · `GET /api/library` (filmes, séries, `continue`, `history`, `busy`, `tmdb`, `version`; ETag/304, gzip;
cada vídeo tem `id, path, file, size, dur, pos, bps, quality, audio, subs, extSubs, watched, broken…`) ·
`GET /api/state` (pequeno: `version`, `busy`, `continue`, `pos` por id, `history` recente) ·
`GET /api/media?path=` (só 127.0.0.1: posição, `resume`, legendas de qualquer arquivo) ·
`GET /api/thumbs/<id>` (começa as miniaturas) · `GET /img/<arquivo>`, `/img/thumb_<id>_<n>.jpg` · `GET /sub/<id>/<n>` ·
`GET /v/<id>/<tamanho>/<nome>` (vídeo com Range) · `GET /tv/*` (página; `/tv/api/*` e `/tv/img/*` também valem) ·
`POST /api/progress {id,pos,dur}` · `POST /api/watched {id|path}`.

## Dados

- `%LOCALAPPDATA%\onVLC\`: `onvlc.log` (player), `biblioteca.log`, `library\index.json`, `library\img\`,
  `library\assistidos.json`, `thumbs\<md5>\`, `tvcache.keystore` (chave do APK; senha em credenciais `onVLC`/`apk_keystore`).
- Posição de cada vídeo: QSettings `PlayerLucas/Player`, `pos/<md5(caminho em minúsculas)>` e `posAt/…` (a mesma no PC e na TV).
- Chaves no Gerenciador de Credenciais, serviço `onVLC`: `tmdb_token` (TMDB v4, já guardado), `api_key`/senha do OpenSubtitles (ainda não).
- "Assistido" = chegou nos últimos 6% ou 2 min. Itens que saem da pasta continuam em assistidos como "indisponível".

## Casa e Mi Box

- PC 192.168.1.69 (cabo, nunca dorme). Mi Box `192.168.1.31` (MiTV-AFMU0, Android 14, armeabi-v7a, MAC `AA-BB-CC-00-00-01`),
  adb `192.168.1.31:5555` autorizado. TV LG C1 4K `192.168.1.13` (porta 3000 só com ela ligada). Mi Box desenha em 1920×1080.
- `tvstream.MiBox().connect()` acorda pela rede (WOL repetido); `KEYCODE_WAKEUP` liga a TV via HDMI-CEC mas **traz a tela
  inicial do Google TV para frente** (o app fica congelado em segundo plano). Para abrir o app use só `am start -n com.lucas.tvcache/.LibraryActivity`.
- **Low Power Standby desligado** (`settings put global low_power_standby_enabled 0`): antes cortava o Wi-Fi 5 s depois
  de dormir e o WOL falhava. Testado: acorda em ~4 s com a TV desligada. Para instalar APK com ela dormindo, mandar WOL
  em loop enquanto instala.
- Firewall: liberado pelo Lucas para o programa (onVLC.exe, python e `server\onvlc-biblioteca.exe`), qualquer porta.
  A regra é por caminho: o `dist\onVLC\onvlc-biblioteca.exe` pede liberação na primeira vez que abrir. Não mexer em firewall (o
  classificador bloqueia e o Lucas faz pelo `firewall.cpl`).
- Syncthing: pasta antiga `tv-videos` (`Vídeos\TV` → Mi Box `Movies/TV`) está **pausada** (não apagada).

## Como testar

- PC sem empacotar: `python main.py [arquivo]`. Biblioteca: `cd server && go build -ldflags "-H windowsgui -s -w" -o onvlc-biblioteca.exe .`
  (Go em `C:\Program Files\Go\bin`), depois `python -c "import library; library.start_detached()"`; para reiniciar,
  `taskkill /IM onvlc-biblioteca.exe /F`. Teste lado a lado: `ONVLC_PORT=8781` + `LOCALAPPDATA` apontando para uma cópia dos dados.
- TV: `python tools\build_tvcache.py` (gera e instala). Mudanças só em `tvui\` **não precisam de APK** (a página vem do PC;
  recarregar com `tools\tvjs.py "location.reload()"`).
- Capturar a tela da TV: `adb -s 192.168.1.31:5555 shell screencap -p /sdcard/s.png` + `pull`. Se aparecer o descanso
  de tela, mandar `input keyevent KEYCODE_SHIFT_LEFT`.
- Medir abertura: abrir com `openPlayer(it, ms)` via tvjs e acompanhar `PL.st.state/time`.

## Números medidos (Homem-Aranha 4K HDR, 20 GB, Mi Box)

2026-10-31, depois da refatoração: abertura direta **4,4 s** (antes 5,4); pulo de +10 s volta a tocar em **0,3 s**;
app abre na TV em 1,5 s; biblioteca (Go) ~19 MB de RAM; app do PC 111 MB (antes 587).

Abertura direta ~5 s (2,9 s é o libVLC lendo o índice/Cues do MKV no processador da Mi Box; o buffer de 16 MB por pedido
foi cortado). Com a página do filme aberta (pré-preparo pausado): 6 s na página → **0,6 s**; 3 s → 2,1 s.
Wi-Fi PC→Mi Box ~250 Mbps. Miniaturas: 200 em ~35 s no 4K. Kit de abertura: 664 MB para 4 filmes.

## Estado em 2026-10-31 (fim da sessão)

- Fases 0, 1 e 2 do plano `onvlc-refatoracao.md` no GitHub; **Fase 3 commitada só localmente** (falta o "ok" do Lucas
  para o push). APK novo instalado na Mi Box; `.exe` novo em `dist\onVLC`.
- Não medido: abertura **preparada** do 4K depois da Fase 3 (o teste por `tvjs.py` voltou vazio duas vezes; conferir).
- Não feito da Fase 3: servir o bloco do cache enquanto ainda baixa (ganho ~0,3 s em pulo para trecho não guardado);
  `CacheService` continua `dataSync`.
- No "Continuar assistindo" só ficou Devoradores de Estrelas (10:24), que não veio dos testes.

## Estado em 2026-10-30

- Biblioteca com: Devoradores de Estrelas (Project Hail Mary, 4K HDR, PT-BR embutida), Homem-Aranha: De Volta ao Lar
  (4K HDR, PT-BR em .srt), Homem-Aranha: Um Novo Dia (1080p, várias .srt). **Harriet** foi movido de propósito para
  `C:\Users\lucas\Videos\` (com o `.pt-BR.srt`) para testar o card "indisponível"; está em "assistidos" (30/10).
- O processo da biblioteca rodando agora é a versão de desenvolvimento (`pythonw main.py --biblioteca`); depois de
  reiniciar o PC, quem abre é o `onVLC.exe`.
- Depuração do WebView ligada (necessária para o `tvjs.py`). O registro de teclas e o `KEYLOG` saíram em 2026-10-31.

## Pendências e ideias

- Lucas: criar conta/chave do OpenSubtitles (legendas da internet no PC).
- Testar séries de verdade (só testadas com episódios falsos) e o próximo episódio automático.
- Cantos das imagens dos cards no PC ficam retos (o QML não corta pelo arredondado sem efeito extra).
- Reservar IP fixo da Mi Box no roteador.
- Decidir se apaga a pasta `tv-videos` do Syncthing de vez.
- 2026-10-31: capítulos tirados do PC e da TV (o Lucas achou ruins); aviso "Mouse embaixo…" do PC tirado; favoritos tirados só da tela
  (`/api/favorite` e `favoritos.json` continuam na biblioteca, sem uso). Falta testar e gerar o APK.
