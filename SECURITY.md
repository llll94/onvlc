# Segurança

## Como avisar de uma falha

Use o aviso privado do GitHub: aba **Security → Report a vulnerability**
(https://github.com/llll94/onvlc/security/advisories/new). Não abra uma issue pública com detalhes de como explorar.

Conte o que acontece, como reproduzir, e qual parte é afetada (player do PC, biblioteca, app da TV, instalador).

## Versões que recebem correção

Só a última release.

## O que o onVLC protege, e como

O onVLC foi pensado para a **rede de casa**. Mesmo assim, ele não confia cegamente em nenhum aparelho dessa rede.

| Risco | Proteção |
|---|---|
| Outro aparelho do Wi-Fi mexer na biblioteca (apagar "continuar assistindo", forjar histórico, ver a lista e os vídeos) | A biblioteca exige uma chave aleatória, criada no PC na primeira vez, em quase todo pedido. Só o player do PC e a TV configurada têm a chave. Ficam abertas apenas a identificação (`/api/hello`), o código da página da TV e as imagens (veja os limites abaixo) |
| Um aparelho fingir ser o PC para a TV | A TV manda um desafio (nonce) e só aceita quem responde com o HMAC certo da chave |
| Outro app instalado na TV mandar o onVLC trocar de PC, tocar ou parar | Esses pedidos só entram por uma tela que exige permissão de adb (`DUMP`), e não existe link que mande no app |
| Uma página na internet, aberta no navegador do PC, falar com a biblioteca | A biblioteca confere o cabeçalho `Host` (contra DNS rebinding), exige a chave e não aceita caminhos de rede (`\\servidor\…`) |
| A página do app da TV carregar algo de fora | O WebView só carrega o servidor local do próprio app, com um token aleatório na rota; há CSP na página. A versão publicada não é depurável |
| Arquivos trocados na hora de gerar o instalador | ffmpeg e libVLC conferidos por SHA-256, VLC e adb por assinatura digital, dependências Python com hash |
| App da TV falso se passando pelo onVLC | O app é assinado com a chave do projeto. Certificado (SHA-256): `5a4de354aed556f11f200ce518e66b87fb4f86918ef2c47cc0ec6d48d074e0e4` |

### Onde ficam os segredos no PC

- **Senhas do TMDB e do OpenSubtitles:** no Gerenciador de Credenciais do Windows (cifradas pelo Windows). Nunca em
  arquivo nem no registro.
- **Chave da biblioteca** (`%LOCALAPPDATA%\onVLC\library\secret`) e **certificado do controle remoto da TV**
  (`%LOCALAPPDATA%\onVLC\remote\`): em arquivo, legíveis só pelo seu usuário, pelo sistema e por administradores.
  Um programa que rode com o seu usuário consegue ler esses arquivos.
- **Na TV:** a chave da biblioteca fica nos dados privados do app.

## Privacidade

- Os vídeos ficam no PC e só vão para a TV pela sua rede. Não há conta no onVLC, nem nuvem, nem estatísticas de uso.
- Para buscar capas e sinopses, a biblioteca manda ao **TMDB** o título e o ano tirados do nome do arquivo.
- Ao procurar legenda na internet, o player manda ao **OpenSubtitles** o nome do arquivo e um código calculado a partir
  do vídeo (o "hash" que o OpenSubtitles usa para achar a legenda certa).
- Na rede de casa, qualquer aparelho consegue ver o nome do PC e o MAC da placa de rede (`/api/hello`, usado pela TV
  para achar e ligar o PC).

## Limites conhecidos

- **Sem criptografia entre TV e PC (HTTP).** Quem consegue escutar a sua rede Wi-Fi vê os filmes e a chave passando.
  Use Wi-Fi com senha (WPA2/WPA3) e não use o onVLC em redes públicas.
- **Imagens abertas na rede de casa.** Capas, logos, fundos e as miniaturas da linha do tempo (quadros dos vídeos) são
  servidos sem a chave, para a página da TV carregar mais rápido. Os nomes desses arquivos não são fáceis de adivinhar,
  mas não são secretos.
- **A depuração USB da TV fica ligada.** É por ela que o PC instala e atualiza o app. Quem tiver a chave do adb deste PC
  (`%USERPROFILE%\.android\adbkey`), ou o certificado do controle remoto, e estiver na sua rede, consegue controlar a
  TV. Para desligar, use as Configurações da TV; o "Assistir na TV" deixa de funcionar até religar.
- **Instalação "só para mim" (padrão).** Os arquivos ficam numa pasta que o seu usuário pode alterar, então um programa
  malicioso rodando com o seu usuário consegue trocá-los. Escolha "para todos os usuários" no instalador para que só um
  administrador possa trocar.
- **Instalador sem assinatura digital.** O Windows mostra o aviso do SmartScreen. Confira o SHA-256 do download com o
  `SHA256SUMS.txt` da versão; cada versão também tem o link da análise no VirusTotal.
- **Desinstalar sem "apagar os dados"** mantém a chave da biblioteca, o certificado da TV e as senhas no PC. Para a TV
  esquecer este PC: Configurações → Opções do desenvolvedor → Revogar autorizações de depuração USB.
- **Arquivos de vídeo de origem desconhecida** são lidos pelo ffmpeg e pelo VLC. Mantenha o onVLC atualizado.
