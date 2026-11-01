# Segurança

## Como avisar de uma falha

Abra um aviso privado em **Security → Report a vulnerability** neste repositório (GitHub Private Vulnerability
Reporting). Não abra uma issue pública com detalhes de como explorar.

Responda com: o que acontece, como reproduzir, e qual parte (PC, biblioteca, app da TV, instalador).

## Versões que recebem correção

Só a última release.

## O que o onVLC protege, e como

O onVLC foi pensado para a **rede de casa**. Mesmo assim, ele não confia cegamente em nenhum aparelho dessa rede:

| Risco | Proteção |
|---|---|
| Outro aparelho do Wi-Fi mexer na biblioteca (apagar "continuar assistindo", forjar histórico) | Todo pedido à biblioteca exige uma chave aleatória criada no PC; só o PC e a TV configurada têm |
| Um aparelho fingir ser o PC para a TV | A TV manda um desafio (nonce) e só aceita quem responde com o HMAC certo da chave |
| Outro app instalado na TV mandar o onVLC trocar de PC, tocar ou parar | Esses pedidos só entram por uma tela que exige permissão de adb (`DUMP`); não existe link que abra o app |
| Uma página na internet, aberta no navegador do PC, falar com a biblioteca | A biblioteca confere o cabeçalho `Host` (contra DNS rebinding), exige a chave, e não aceita caminhos de rede (`\\servidor\…`) |
| A página do app da TV carregar algo de fora | O WebView só carrega o servidor local do próprio app (com um token aleatório na rota); CSP na página |
| Arquivos trocados no build | ffmpeg e libVLC conferidos por SHA-256; VLC e adb por assinatura digital; dependências Python com hash |
| Senhas guardadas no PC | Só no Gerenciador de Credenciais do Windows (cifrado); nunca em arquivo ou no registro |

## Limites conhecidos

- **Sem criptografia entre TV e PC (HTTP).** Quem consegue escutar a sua rede Wi-Fi vê os filmes e a chave passando.
  Use uma rede Wi-Fi com senha (WPA2/WPA3) e não use o onVLC em redes públicas.
- **A depuração USB da TV fica ligada.** É por ela que o PC instala e atualiza o app. Quem tiver a chave do adb do PC
  (`%USERPROFILE%\.android\adbkey`) e estiver na rede controla a TV. Para desligar, pelas Configurações da TV; o
  "Assistir na TV" deixa de funcionar até religar.
- **Instalação "só para mim" (padrão).** Os arquivos ficam numa pasta que o seu usuário pode alterar, então um programa
  malicioso rodando com o seu usuário consegue trocá-los. Escolha "para todos os usuários" no instalador para que só um
  administrador possa trocar.
- **Instalador sem assinatura digital.** O Windows mostra o aviso do SmartScreen. Confira o SHA-256 do download com o
  `SHA256SUMS.txt` da release.
- **Arquivos de vídeo de origem desconhecida** são lidos pelo ffmpeg e pelo VLC. Mantenha o onVLC atualizado.
