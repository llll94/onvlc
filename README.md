# onVLC

Player de vídeo para Windows com o motor do VLC, que leva os seus filmes para a TV.

- **No PC:** um player leve, com a mesma biblioteca de filmes que aparece na TV (capas, sinopses, "continuar assistindo").
- **Na TV (Android TV / Google TV):** um app que toca os filmes direto do PC, pela rede de casa, com o mesmo motor do VLC
  (4K, HDR, legendas). O filme continua de onde parou em qualquer tela.
- **Configuração guiada:** depois de instalar no PC, um assistente acha a TV na rede, instala o app nela e testa imagem,
  som e velocidade. Ninguém digita endereço de IP.

Tudo grátis. Sem conta, sem nuvem: os filmes nunca saem da sua casa.

## Instalar

1. Baixe o `instalar-onVLC-x.y.z.exe` da página [Releases](../../releases).
2. Abra. O Windows pode mostrar **"O Windows protegeu seu computador"**: o instalador não tem uma assinatura digital
   paga. Clique em **Mais informações → Executar assim mesmo**.
3. Deixe marcada a opção de liberar a TV no firewall (o Windows pede confirmação).
4. No fim, o onVLC abre o assistente. Tenha o controle da TV por perto.

**Conferir o download (opcional):** no PowerShell, `Get-FileHash .\instalar-onVLC-x.y.z.exe` deve dar o mesmo valor
que está no `SHA256SUMS.txt` da release.

### O que precisa

- Windows 10 ou 11, 64 bits.
- Uma TV (ou caixinha) com **Android TV ou Google TV**, no mesmo Wi-Fi do PC.
- A rede do PC marcada como **Particular** no Windows (em rede "Pública" o firewall não deixa a TV falar com o PC; o
  assistente avisa).
- Na TV, a **depuração USB** precisa ser ligada uma vez (o assistente mostra o passo a passo). É por ela que o PC instala
  e atualiza o app da TV, sem loja.

## Como funciona (resumo)

- A **biblioteca** roda no PC em segundo plano e lê a pasta `Vídeos\onVLC`. A TV pede a lista e os filmes a ela.
- PC e TV combinam uma **chave** no assistente; a TV só aceita um PC que prove ter essa chave, e o PC só atende quem tem
  a chave. Detalhes em [SECURITY.md](SECURITY.md).
- Capas e sinopses vêm do [TMDB](https://www.themoviedb.org/) (com um token seu, grátis). Legendas, do
  [OpenSubtitles](https://www.opensubtitles.com/).

## Desinstalar

Configurações do Windows → Aplicativos → onVLC. O desinstalador pergunta se apaga também o que o onVLC guardou
(posições, capas, TV salva, senhas). Os filmes da pasta `Vídeos\onVLC` nunca são apagados. Na TV, desinstale o onVLC
pelas Configurações e, se quiser, revogue as autorizações de depuração USB.

## Para desenvolvedores

Veja [CLAUDE.md](CLAUDE.md) (arquitetura, API, como gerar). Em resumo: Python 3.12 + PySide6 (PC), Go 1.27 (biblioteca),
Java sem Gradle (TV), Inno Setup (instalador).

## Licença

[GPL-3.0-or-later](LICENSE). O onVLC distribui junto VLC, ffmpeg, adb e outros; veja [THIRD_PARTY.md](THIRD_PARTY.md).
onVLC não é afiliado ao VideoLAN nem ao projeto VLC.
