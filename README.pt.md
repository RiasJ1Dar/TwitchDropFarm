[Українська](README.md) · [English](README.en.md) · [Español](README.es.md) · **Português** · [Deutsch](README.de.md) · [Français](README.fr.md) · [Polski](README.pl.md) · [Türkçe](README.tr.md) · [简体中文](README.zh.md)

# TwitchDropFarm

![Arquitetura](docs/architecture.en.png)

Diagrama interativo: [abrir no navegador](https://riasj1dar.github.io/TwitchDropFarm/architecture.en.html) (no GitHub aparece o código, não a página).


Farme **timed drops** na Twitch sem navegador aberto e sem uma live na tela. O
programa lê o seu inventário sozinho, decide o que vale a pena farmar, encontra
um canal adequado e entrega tempo de exibição à Twitch — e mostra as recompensas
resgatadas na janela, na bandeja do sistema e no Telegram.

Um binário por plataforma (Windows `.exe`, Linux, macOS), sem runtimes ao lado:
sem Node.js, sem Playwright, sem navegador embutido. O login usa Edge, Chrome ou
Chromium. Atualização automática só no `.exe` Windows.

## O que ele faz

- **Avisa quando não vai dar tempo**: se resta menos tempo do que os minutos de
  exibição ainda necessários, ele diz com antecedência.
- **Escolhe sozinho.** Quatro modos: por lista de prioridade, pelo prazo mais
  próximo, pelo melhor encaixe (para concluir o máximo de campanhas) ou apenas
  aquilo a que a conta está vinculada e onde é entregue um item de verdade.
- **Mantém até 198 canais sob observação** via PubSub e troca quando uma live
  cai.
- **Resgata os drops automaticamente** e parte logo para o próximo.
- **Uma janela** com quatro abas: Mineração, Canais, Inventário, Configurações.
- **Bandeja do sistema**: minimizar, notificações, iniciar em segundo plano.
- **Bot do Telegram**: status, inventário, campanhas, pausar/retomar, troca de
  canal, gestão de prioridades, reinício completo — por botões ou comandos.
- **Sobrevive a falhas**: queda de rede, DNS que some, computador dormindo,
  erros transitórios da Twitch. No pior caso, reinicia a si mesmo.
- **Percebe travamentos**: se os minutos param de acumular (por exemplo, porque
  a mesma conta está assistindo à Twitch manualmente em outro lugar), ele avisa
  em vez de ficar calado.
- **Idiomas da interface** (Definições): ucraniano por omissão, mais English,
  Español, Português, Deutsch, Français, Polski, Türkçe, 简体中文. Sem russo.
  O registo fica em ucraniano. O bot do Telegram usa a mesma língua que a
  janela.

## Requisitos

- Windows 10/11 — suporte completo (janela, bandeja, `.exe`, inicialização automática, atualização automática)
- Linux / macOS — GUI+bandeja a partir do binário ou do código; inicialização automática manual ([docs/autostart-linux-macos.md](docs/autostart-linux-macos.md)); sem atualização automática
- Python 3.10+ — código-fonte ou construir o binário
- Edge, Chrome ou Chromium — apenas para o primeiro login

## Download (v1.2)

Release: [v1.2](https://github.com/RiasJ1Dar/TwitchDropFarm/releases/tag/v1.2).

| Plataforma | Arquivo |
|---|---|
| Windows | [TwitchDropFarm.exe](https://github.com/RiasJ1Dar/TwitchDropFarm/releases/download/v1.2/TwitchDropFarm.exe) (atualização automática) |
| Linux x86_64 | [TwitchDropFarm-linux-x86_64](https://github.com/RiasJ1Dar/TwitchDropFarm/releases/download/v1.2/TwitchDropFarm-linux-x86_64) |
| macOS | [TwitchDropFarm-macos](https://github.com/RiasJ1Dar/TwitchDropFarm/releases/download/v1.2/TwitchDropFarm-macos) |

`manifest.json` só para atualização automática no Windows. Linux/macOS sem atualização automática. Inicialização automática manual: [docs/autostart-linux-macos.md](docs/autostart-linux-macos.md).

## Execução

A partir do código:

```bash
python -m venv env
env\Scripts\pip install -r requirements.txt
env\Scripts\python main.py
```

O `.exe` compilado:

```bash
dist\TwitchDropFarm.exe
```

Na primeira execução o programa abre uma página da Twitch com um código de
confirmação. Após o login, o token é salvo e nunca mais é pedido.

### Argumentos

| Argumento | O que faz |
|---|---|
| `--console` | sem janela, só console — para servidor ou inicialização automática |
| `--tray` | iniciar minimizado na bandeja |
| `--log` | gravar o registro mesmo que esteja desligado nas configurações |
| `-v`, `-vv`, `-vvv` | mais detalhes nos registros (pode repetir) |
| `--auth-only` | autenticar e sair |
| `--dump-inventory` | mostrar todas as campanhas e drops, e sair |
| `--export` | salvar o histórico e o inventário em CSV e HTML na pasta de estado, e sair |
| `--probe-protocol` | verificar se a Twitch ainda aceita as consultas do programa, e sair |
| `--test-telegram` | enviar mensagem de teste e sair |
| `--version` | versão |

## Configuração

`settings.json` fica no diretório de estado (veja abaixo) e é criado sozinho na
primeira execução. Modelo:
[`settings.example.json`](settings.example.json).

| Chave | Significado |
|---|---|
| `farm_mode` | `0` — lista de prioridade, `1` — prazo mais próximo, `2` — melhor encaixe, `3` — apenas campanhas vinculadas |
| `language` | `uk` por omissão; também `en`, `es`, `pt`, `de`, `fr`, `pl`, `tr`, `zh`; `auto` — idioma do Windows. O bot segue a janela |
| `priority` | jogos em ordem de preferência |
| `exclude` | jogos a não tocar |
| `farm_cosmetics` | aceitar campanhas que só dão emblemas e emotes |
| `verify_channel_drops` | verificar em cada canal se os drops estão realmente ativos (mais lento, mais confiável) |
| `start_in_tray` | iniciar minimizado |
| `tray_notifications` | notificações pop-up |
| `dark_theme` | tema escuro da janela |
| `drop_images` | baixar as imagens das recompensas e mostrá-las na lista (cache ~6 MB) |
| `image_size` | tamanho da imagem na lista, 16–96 |
| `inventory_view` | `list` — lista densa, `tiles` — cartões com imagens grandes |
| `browser_path` | caminho do navegador se a detecção automática falhar |
| `proxy` | proxy para as requisições |
| `watch_games` | jogos observados: aviso quando surge uma campanha nova |
| `autostart` | iniciar com o Windows (a entrada é restaurada se sumir do registro) |
| `check_updates` | perguntar ao GitHub, uma vez por início, se há arquivos mais novos |
| `skip_hopeless` | pular campanhas em que não dá mais tempo de concluir nenhum drop |
| `progress_style` | `rainbow` — barra de progresso furta-cor, `state` — cor conforme o estado |
| `theme_preset` | conjunto de cores: vazio — o embutido, `ocean`, `forest`, `ember`, `grape`, `mono` |
| `keep_log` | manter registro (ligado por padrão) |
| `log_dir` | pasta do registro; vazio — `Documentos\TwitchDropFarm` |
| `hint_links` | avisar sobre campanhas que exigem vincular a conta (para jogos de `priority` e `watch_games`) |
| `discord_webhook` | URL do webhook do Discord para eventos importantes (recompensas, travamento, vínculo perdido, erros); vazio — desligado |

Modo e prioridade são mais fáceis de mudar na aba de configurações; o resto, na
mão, no arquivo. Mudanças no arquivo valem após reiniciar.

### Telegram

1. Crie um bot no [@BotFather](https://t.me/BotFather) e pegue o token.
2. Envie qualquer coisa ao seu bot para que ele veja o seu `chat_id`.
3. No `settings.json`:

```json
"telegram": {
    "enabled": true,
    "bot_token": "SEU_TOKEN_AQUI",
    "chat_ids": [SEU_CHAT_ID],
    "allow_control": true,
    "notify_critical": true,
    "notify_rewards": true,
    "notify_routine": false,
    "report_every_hours": 6
}
```

4. Verifique: `main.py --test-telegram`

`chat_ids` é uma lista branca. Tudo o que chegar de outro lugar é ignorado, então
um estranho que encontre o bot não conseguirá controlar o miner.

Comandos: `/status`, `/inventory`, `/campaigns`, `/pause`, `/resume`,
`/switch <canal>`, `/priority add|remove <jogo>`, `/reload`, `/hide`, `/show`, `/reboot`,
`/watch add|remove <jogo>`, `/report [dias]`, `/export`, `/update`, `/menu`, `/help`.
`/report` resume os últimos 90 dias (de 1 a 365), `/update` instala uma atualização encontrada.
A maioria dos comandos também está disponível como botões em `/menu`.

## Onde fica o estado

`%LOCALAPPDATA%\TwitchDropFarm\`

```
auth.json        token da Twitch
cookies.jar      cookies
settings.json    configuração
history.jsonl    histórico de drops (para /report e a exportação)
seen-campaigns.json  campanhas já vistas (para watch_games)
theme.json       tema salvo a partir da janela
images           cache das imagens de recompensas
lock.file        proteção contra duas cópias ao mesmo tempo
browser_profile  perfil do navegador usado no login
```

O registro vai para `Documentos\TwitchDropFarm\log.txt` (ou para `log_dir`); sem pasta de
Documentos, para a pasta de estado. A exportação (`--export`, `/export`) grava `history.csv`,
`history.html`, `inventory.csv` e `inventory.html` na pasta de estado.

O diretório de estado é um por usuário, e não ao lado do programa — caso
contrário, cada cópia nova pediria login de novo. Para o oposto (pendrive,
computador alheio), coloque um arquivo vazio `portable.txt` ao lado do `.exe`:
o estado passa a viver ali.

## Compilação

```bash
env\Scripts\python.exe -m PyInstaller build.spec --noconfirm
```

Três jeitos fáceis de se queimar:

- **Pare o `.exe` em execução** antes de compilar, senão dá `PermissionError`.
- **Não interrompa a compilação.** Um PyInstaller abortado deixa um `.exe`
  truncado que morre com `DLL load failed while importing _tkinter`. Parece
  defeito de código, mas não é.
- **Não adicione `--clean`** sem motivo — mais lento e sem ganho.

## Verificações

```bash
main.py --dump-inventory     todas as campanhas da Twitch real
main.py --test-telegram      o bot
tests\core_check.py          lógica do núcleo (sem rede)
tests\bot_check.py           testes do bot (sem rede)
tests\live_check.py          o núcleo contra a Twitch real
```

## Como é montado

```
core/protocol   fatos sobre a API privada da Twitch — não são decisões nossas
core/config     caminhos, intervalos, limites
core/toolbox    utilitários independentes
core/api        rede, novas tentativas, resiliência
core/identity   token e cabeçalhos
core/model      campanhas e drops
core/channels   canais e entrega do tempo assistido
core/pubsub     assinaturas
core/miner      apenas lógica de decisão
auth/           login: device flow e controle do navegador via CDP
gui/            janela e bandeja
notify/         Telegram
```

A divisão é proposital: `protocol` descreve o que a Twitch dita (hashes de
persisted queries do GraphQL, o formato do evento `minute-watched`, nomes dos
tópicos), enquanto `config` guarda o que nós decidimos. Misturar os dois é não
saber qual deles pode ser alterado.

O controle do navegador é um cliente próprio do Chrome DevTools Protocol sobre
`aiohttp`. Playwright e Selenium não são usados de propósito: ambos trazem
runtimes próprios, e o requisito do projeto é um único `.exe` autossuficiente.

## Limitações

- Somente Windows. Nada na arquitetura impede portar, mas os caminhos do
  navegador, a bandeja e a inicialização automática são escritos para Windows.
- A Twitch não promete que sua API privada fique parada. Se os hashes das
  persisted queries mudarem, o que precisa de conserto é `core/protocol.py`.
- Uma conta por processo.

## Aviso

O programa faz o mesmo que uma live aberta no navegador faria — só que sem uma
pessoa na frente da tela. Automatizar a exibição pode conflitar com os Termos de
Serviço da Twitch. O risco é do usuário; o autor não assume responsabilidade
pelas consequências para a sua conta.

## Licença

MIT — veja [LICENSE](LICENSE).
