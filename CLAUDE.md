# onVLC — handoff

Projeto pessoal do Lucas . Repositório privado: **github.com/llll94/onvlc** (branch `main`).
Commits como `Lucas Schunck <148644814+llll94@users.noreply.github.com>` (já configurado no repo).
O Lucas não programa: explicar em português simples, sem jargão; mostrar protótipo (Artifact) antes de codar ideias novas
de tela; nunca matar o onVLC dele aberto (pedir para fechar); não mexer no mouse dele; não ligar/usar a TV se ele estiver
assistindo (conferir antes com `tools\tvjs.py`).

Última sessão: 2026-10-30 (Claude Code, sessão `0f571832…`). Plano detalhado e histórico no Obsidian:
`C:\Users\lucas\Documents\Obsidian Vault\work\Claude Code\Plans\player-video-redesign.md`.
Rede de casa (IPs, MACs, Mi Box): `…\Claude Code\Docs\Rede de casa.md`.

---

## O que é

Um player de vídeo próprio com o motor do VLC, em três partes:

1. **onVLC do PC** (`main.py` + `ui\Overlay.qml`): janela sem borda, python-vlc por baixo e uma camada QML transparente
   por cima (barra com capítulos, miniatura na linha do tempo, menu lateral, legendas do OpenSubtitles, Assistir na TV).
   A **biblioteca** (mesma tela da TV) aparece dentro dele num WebEngineView quando abre sem vídeo, ou por Ctrl+B /
   menu Mídia › Biblioteca.
2. **Biblioteca** (`library.py`): processo separado, sem janela (`onVLC.exe --biblioteca`, ícone na bandeja), que lê a
   pasta **`C:\Users\lucas\Videos\onVLC`**, busca dados no TMDB e serve tudo na porta **8780** para a TV e para o PC.
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
| `main.py` | Player do PC (classe `Player`), slots chamados pelo QML; `--biblioteca` roda `library.main()`; `QtWebEngineQuick.initialize()` antes do app; `libraryPlay` abre o vídeo pedido pela página |
| `ui\Overlay.qml` | Toda a interface do PC; `libraryOpen`, `WebEngineView` em `http://127.0.0.1:8780/tv/?pc=1`; página pede ações navegando para `/pc/<cmd>` (play, close, move) e o QML intercepta |
| `library.py` | Biblioteca: varre a pasta a cada 20 s, `parse_name` (filme/SxxEyy), `probe` (ffprobe: duração, qualidade, áudios, legendas, capítulos; arquivo quebrado vira "com problema"), legendas externas (`find_subs`, idioma pelo nome ou pelo texto), TMDB pt-BR (logo pt-BR > en), quadro do filme, miniaturas, assistidos, favoritos, servidor HTTP |
| `tvfolder.py` | Painel "Pasta onVLC" do PC: lista com nome da TV e estado; "Mandar para a TV" = hardlink na pasta (mesmo disco) ou cópia; lixeira; `find_tool` (ffmpeg); `VIDEO_EXTS` |
| `tvstream.py` | **Assistir na TV** (do PC): servidor de vídeo 8765+ com Range (`send_file`, usado também pela biblioteca), adb, Wake-on-LAN, liga TV por HDMI-CEC, posição pelo `dumpsys media_session` |
| `thumbs.py` | Miniaturas da linha do tempo (200 quadros-chave, HDR com tonemap), usadas no PC e servidas à TV |
| `opensubs.py` | Legendas do OpenSubtitles (falta o Lucas criar conta/chave) |
| `tvui\index.html`, `app.js`, `style.css` | Página da biblioteca (1920×1080 escalada). Telas: Início/Filmes/Séries (abas trocam ao passar; cada aba montada uma vez e reaproveitada), página do filme/série, indisponível, procurando PC. Modo PC com `?pc=1` |
| `tvui\player.js` | Interface do player da TV (barra com capítulos e trecho guardado, miniatura ao procurar, painel áudio/legendas com rolagem, capítulos, pausado, próximo episódio, nova tentativa se travar) |
| `android\src\…\LibraryActivity.java` | Atividade da tela inicial: WebView transparente + `VideoPlayer` atrás; ponte JS `window.onvlc` (play, prepare, unprepare, toggle, seek, audio, spu, stop, warm, post) |
| `VideoPlayer.java` | libVLC: SurfaceView vídeo + legenda, `:network-caching=400`, `:prefetch-read-size=262144`, `:prefetch-buffer-size=4096`, `:start-paused` para preparar, legendas externas como slave |
| `CacheServer.java` | Servidor local 127.0.0.1:8090: `/stream` (blocos de 8 MB, 5 min à frente, 1 min atrás, 2 GB de vários filmes), **kit de abertura** (até 4 GB, fixado), `/ui/*` (repassa a página e a API para o PC e guarda cópia para quando o PC está desligado; imagens com cache longo), `/status` |
| `PcFinder.java` | Acha o PC: último IP salvo, senão varre x.x.x.1–254 na porta 8780 (`/api/hello`) |
| `Progress.java` | POST da posição para o PC |
| `PlayActivity.java` / `CacheService.java` | Caminho antigo do "Assistir na TV" do PC (ainda abre o app do VLC) / serviço em primeiro plano durante o filme |
| `tools\build.py` | Gera `dist\onVLC\onVLC.exe` (PyInstaller, ~579 MB com o WebEngine), atalhos, registro como app de vídeo |
| `tools\build_tvcache.py` | APK sem Gradle (javac → d8 → aapt2 → zipalign → apksigner), baixa o AAR do libVLC uma vez em `build\libvlc`, só `armeabi-v7a`, copia `tvui` para os assets; instala na Mi Box (`--no-install` para só gerar) |
| `tools\tvjs.py` | Roda JS na página aberta na TV pelo adb (depuração do WebView): `python tools\tvjs.py "expressão"` |
| `tools\make_icon.py`, `set_tmdb_key.py`, `migrate_folder.py` | Ícone; guardar chave do TMDB; migração única de `Vídeos\onVLC TV` → `Vídeos\onVLC` (já rodada) |

## API da biblioteca (porta 8780)

`GET /api/hello` · `GET /api/library` (filmes, séries, `continue`, `history`, `favorites`, `busy`, `tmdb`; cada vídeo tem
`id, path, file, size, dur, pos, bps, quality, audio, subs, extSubs, chapters, watched, broken…`) ·
`GET /api/thumbs/<id>` (começa as miniaturas) · `GET /img/<arquivo>`, `/img/thumb_<id>_<n>.jpg` · `GET /sub/<id>/<n>` ·
`GET /v/<id>/<tamanho>/<nome>` (vídeo com Range) · `GET /tv/*` (página; `/tv/api/*` e `/tv/img/*` também valem) ·
`POST /api/progress {id,pos,dur}` · `POST /api/watched {id|path}` · `POST /api/favorite {id,on,item}`.

## Dados

- `%LOCALAPPDATA%\onVLC\`: `onvlc.log` (player), `biblioteca.log`, `library\index.json`, `library\img\`,
  `library\assistidos.json`, `library\favoritos.json`, `thumbs\<md5>\`.
- Posição de cada vídeo: QSettings `PlayerLucas/Player`, `pos/<md5(caminho em minúsculas)>` e `posAt/…` (a mesma no PC e na TV).
- Chaves no Gerenciador de Credenciais, serviço `onVLC`: `tmdb_token` (TMDB v4, já guardado), `api_key`/senha do OpenSubtitles (ainda não).
- "Assistido" = chegou nos últimos 6% ou 2 min. Itens que saem da pasta continuam em assistidos/favoritos como "indisponível".

## Casa e Mi Box

- PC 192.168.1.69 (cabo, nunca dorme). Mi Box `192.168.1.31` (MiTV-AFMU0, Android 14, armeabi-v7a, MAC `AA-BB-CC-00-00-01`),
  adb `192.168.1.31:5555` autorizado. TV LG C1 4K `192.168.1.13` (porta 3000 só com ela ligada). Mi Box desenha em 1920×1080.
- `tvstream.MiBox().connect()` acorda pela rede (WOL repetido); `KEYCODE_WAKEUP` liga a TV via HDMI-CEC mas **traz a tela
  inicial do Google TV para frente** (o app fica congelado em segundo plano). Para abrir o app use só `am start -n com.lucas.tvcache/.LibraryActivity`.
- **Low Power Standby desligado** (`settings put global low_power_standby_enabled 0`): antes cortava o Wi-Fi 5 s depois
  de dormir e o WOL falhava. Testado: acorda em ~4 s com a TV desligada. Para instalar APK com ela dormindo, mandar WOL
  em loop enquanto instala.
- Firewall: liberado pelo Lucas para o programa (onVLC.exe e python), qualquer porta. Não mexer em firewall (o
  classificador bloqueia e o Lucas faz pelo `firewall.cpl`).
- Syncthing: pasta antiga `tv-videos` (`Vídeos\TV` → Mi Box `Movies/TV`) está **pausada** (não apagada).

## Como testar

- PC sem empacotar: `python main.py [arquivo]`. Biblioteca: `python -c "import library; library.start_detached()"` (dev usa
  `pythonw main.py --biblioteca`); para reiniciar, matar o `pythonw.exe` / processo `--biblioteca` e chamar de novo.
- TV: `python tools\build_tvcache.py` (gera e instala). Mudanças só em `tvui\` **não precisam de APK** (a página vem do PC;
  recarregar com `tools\tvjs.py "location.reload()"`).
- Capturar a tela da TV: `adb -s 192.168.1.31:5555 shell screencap -p /sdcard/s.png` + `pull`. Se aparecer o descanso
  de tela, mandar `input keyevent KEYCODE_SHIFT_LEFT`.
- Medir abertura: abrir com `openPlayer(it, ms)` via tvjs e acompanhar `PL.st.state/time`.

## Números medidos (Homem-Aranha 4K HDR, 20 GB, Mi Box)

Abertura direta ~5 s (2,9 s é o libVLC lendo o índice/Cues do MKV no processador da Mi Box; o buffer de 16 MB por pedido
foi cortado). Com a página do filme aberta (pré-preparo pausado): 6 s na página → **0,6 s**; 3 s → 2,1 s.
Wi-Fi PC→Mi Box ~250 Mbps. Miniaturas: 200 em ~35 s no 4K. Kit de abertura: 664 MB para 4 filmes.

## Estado em 2026-10-30 (fim da sessão)

- Biblioteca com: Devoradores de Estrelas (Project Hail Mary, 4K HDR, PT-BR embutida), Homem-Aranha: De Volta ao Lar
  (4K HDR, PT-BR em .srt), Homem-Aranha: Um Novo Dia (1080p, várias .srt). **Harriet** foi movido de propósito para
  `C:\Users\lucas\Videos\` (com o `.pt-BR.srt`) para testar o card "indisponível"; está em "assistidos" (30/10).
- "Continuar assistindo" do De Volta ao Lar veio dos testes de abertura (perguntei se pode tirar; sem resposta).
- O processo da biblioteca rodando agora é a versão de desenvolvimento (`pythonw main.py --biblioteca`); depois de
  reiniciar o PC, quem abre é o `onVLC.exe`.
- Ainda no código, para depuração: registro de teclas no app (`Log.i("onvlc", "tecla …")`) e `KEYLOG` no `player.js`;
  depuração do WebView ligada (necessária para o `tvjs.py`).

## Pendências e ideias

- Lucas: criar conta/chave do OpenSubtitles (legendas da internet no PC).
- "Assistir na TV" do PC (`PlayActivity`) ainda abre o app do VLC; dá para trocar pelo player próprio.
- Testar séries de verdade (só testadas com episódios falsos) e o próximo episódio automático.
- App do PC mais leve (579 MB por causa do WebEngine; antes ~400 MB).
- Reservar IP fixo da Mi Box no roteador.
- Tirar os registros de depuração de teclas quando não forem mais úteis.
- Decidir se apaga a pasta `tv-videos` do Syncthing de vez.
