[Українська](README.md) · **English** · [Español](README.es.md) · [Português](README.pt.md) · [Deutsch](README.de.md) · [Français](README.fr.md) · [Polski](README.pl.md) · [Türkçe](README.tr.md) · [简体中文](README.zh.md)

# TwitchDropFarm

![Architecture](docs/architecture.en.png)

Interactive diagram: [open in the browser](https://riasj1dar.github.io/TwitchDropFarm/architecture.en.html) (GitHub shows the HTML source, not a page).


Farm **timed drops** on Twitch with no browser window open and no stream on your
screen. The program reads your inventory itself, decides what is worth farming,
finds a suitable channel and delivers watch time to Twitch — then shows claimed
rewards in its window, in the tray and in Telegram.

One binary per platform (Windows `.exe`, Linux, macOS), no runtimes alongside
it: no Node.js, no Playwright, no bundled browser. Sign-in uses the browser
already installed on your system (Edge, Chrome, or Chromium). Auto-update is
Windows `.exe` only.

## What it does

- **Warns when a campaign is out of reach**: if less time is left than the watch minutes still needed, it says so in advance.
- **Chooses on its own.** Four modes: by priority list, by nearest deadline, by
  tightest fit (to finish as many campaigns as possible), or only what your
  account is linked to and where a real in-game item is granted.
- **Keeps up to 198 channels under watch** over PubSub and switches when a
  stream goes down.
- **Claims drops automatically** and moves straight on to the next one.
- **A window** with four tabs: Mining, Channels, Inventory, Settings.
- **Tray**: minimise, notifications; start with the OS — on Windows from the
  GUI, on Linux/macOS manually ([docs/autostart-linux-macos.md](docs/autostart-linux-macos.md)).
- **Telegram bot**: status, inventory, campaigns, pause/resume, channel
  switching, priority management, full restart — by buttons or commands.
- **Survives failures**: network loss, DNS disappearing, the computer sleeping,
  transient Twitch errors. In the worst case it restarts itself.
- **Notices stalls**: if minutes stop accruing (for example, because the same
  account is watching Twitch manually somewhere), it says so instead of staying
  silent.
- **UI languages** (Settings): Ukrainian by default, plus English, Español,
  Português, Deutsch, Français, Polski, Türkçe, 简体中文. No Russian. The log
  stays in Ukrainian. The Telegram bot uses the same language as the window.

## Requirements

- Windows 10/11 — full support (window, tray, `.exe`, autostart, auto-update)
- Linux / macOS — GUI+tray from the binary or from source; autostart manually
  (see [docs/autostart-linux-macos.md](docs/autostart-linux-macos.md)); no auto-update
- Python 3.10+ — to run from source or build a binary
- Edge, Chrome, or Chromium — only for the first sign-in

## Download (v1.2)

Release: [v1.2](https://github.com/RiasJ1Dar/TwitchDropFarm/releases/tag/v1.2).

| Platform | File |
|---|---|
| Windows | [TwitchDropFarm.exe](https://github.com/RiasJ1Dar/TwitchDropFarm/releases/download/v1.2/TwitchDropFarm.exe) (auto-update) |
| Linux x86_64 | [TwitchDropFarm-linux-x86_64](https://github.com/RiasJ1Dar/TwitchDropFarm/releases/download/v1.2/TwitchDropFarm-linux-x86_64) |
| macOS | [TwitchDropFarm-macos](https://github.com/RiasJ1Dar/TwitchDropFarm/releases/download/v1.2/TwitchDropFarm-macos) (CI arch, currently Apple Silicon) |

`manifest.json` in the release is for Windows auto-update only — do not download it
by hand. Linux/macOS have no auto-update. On macOS, open an unsigned binary via
Finder → Open the first time (Gatekeeper).

## Running

From source:

```bash
python -m venv env
env\Scripts\pip install -r requirements.txt
env\Scripts\python main.py
```

Built binary (local build or from the release):

```bash
# Windows
TwitchDropFarm.exe
# Linux
chmod +x TwitchDropFarm-linux-x86_64 && ./TwitchDropFarm-linux-x86_64
# macOS
chmod +x TwitchDropFarm-macos && ./TwitchDropFarm-macos
```

On first launch the program opens a Twitch page with a confirmation code. After
sign-in the token is stored and never asked for again.

### Arguments

| Argument | What it does |
|---|---|
| `--console` | no window, console only — for a server or autostart |
| `--tray` | start minimised to tray |
| `--log` | write `log.txt` |
| `-v`, `-vv`, `-vvv` | more detail in logs (repeatable) |
| `--auth-only` | authenticate and exit |
| `--dump-inventory` | print all campaigns and drops, then exit |
| `--test-telegram` | send a test message and exit |
| `--version` | version |

## Settings

`settings.json` lives in the state directory (see below) and is created
automatically on first launch. Sample:
[`settings.example.json`](settings.example.json).

| Key | Meaning |
|---|---|
| `farm_mode` | `0` — priority list, `1` — nearest deadline, `2` — tightest fit, `3` — linked campaigns only |
| `language` | `uk` by default; also `en`, `es`, `pt`, `de`, `fr`, `pl`, `tr`, `zh`; `auto` — Windows language. The bot follows the window |
| `priority` | games in order of preference |
| `exclude` | games to leave alone |
| `farm_cosmetics` | accept campaigns that only grant badges and emotes |
| `verify_channel_drops` | check every channel for drops actually being enabled (slower, more reliable) |
| `start_in_tray` | start minimised |
| `tray_notifications` | pop-up notifications |
| `dark_theme` | dark window theme |
| `drop_images` | download reward images and show them in the list (~6 MB cache) |
| `image_size` | image size in the list, 16–96 |
| `inventory_view` | `list` — dense list, `tiles` — cards with large images |
| `browser_path` | path to the browser if auto-detection failed |
| `proxy` | proxy for requests |

Mode and priority are easier to change on the Settings tab — the rest by hand in
the file. Changes to the file take effect after a restart.

### Telegram

1. Create a bot with [@BotFather](https://t.me/BotFather) and take the token.
2. Send your bot anything, so that it sees your `chat_id`.
3. In `settings.json`:

```json
"telegram": {
    "enabled": true,
    "bot_token": "YOUR_TOKEN_HERE",
    "chat_ids": [YOUR_CHAT_ID],
    "allow_control": true,
    "notify_critical": true,
    "notify_rewards": true,
    "notify_routine": false,
    "report_every_hours": 6
}
```

4. Verify: `main.py --test-telegram`

`chat_ids` is a whitelist. Anything arriving from elsewhere is ignored, so a
stranger who finds the bot cannot control the miner.

Commands: `/status`, `/inventory`, `/campaigns`, `/pause`, `/resume`,
`/switch <channel>`, `/priority add|remove <game>`, `/reload`, `/hide`, `/show`, `/reboot`,
`/menu`, `/help`. Everything except the two that take arguments is available as
buttons.

## Where the state lives

- Windows: `%LOCALAPPDATA%\TwitchDropFarm\`
- macOS: `~/Library/Application Support/TwitchDropFarm/`
- Linux: `$XDG_STATE_HOME/TwitchDropFarm/` or `~/.local/state/TwitchDropFarm/`

```
auth.json        Twitch token
cookies.jar      cookies
settings.json    settings
log.txt          log (with --log)
lock.file        guard against two copies at once
browser_profile  browser profile used for sign-in
```

The state directory is one per user rather than next to the program — otherwise
every new copy would ask you to sign in again. To do the opposite (a USB stick,
someone else's computer), put an empty `portable.txt` file next to the binary:
the state will then live there.

## Building

```bash
python -m PyInstaller build.spec --noconfirm
```

On Windows a venv path works too: `env\Scripts\python.exe -m PyInstaller …`.
Release CI builds three artifacts: `TwitchDropFarm.exe`,
`TwitchDropFarm-linux-x86_64`, `TwitchDropFarm-macos`.

Three easy ways to get burned:

- **Stop the running binary** before building, otherwise `PermissionError` (Windows).
- **Do not interrupt the build.** An aborted PyInstaller leaves a truncated
  file that dies with `DLL load failed while importing _tkinter`. It looks
  like a code defect but is not one.
- **Do not add `--clean`** without a reason — slower, no benefit.

## Checks

```bash
main.py --dump-inventory     all campaigns from live Twitch
main.py --test-telegram      the bot
tests\core_check.py          core logic (no network)
tests\bot_check.py           bot tests (no network)
tests\live_check.py          core against live Twitch
```

## How it is put together

```
core/protocol   facts about Twitch's private API — not our decisions
core/config     paths, intervals, limits
core/toolbox    standalone utilities
core/api        network, retries, resilience
core/identity   token and headers
core/model      campaigns and drops
core/channels   channels and watch delivery
core/pubsub     subscriptions
core/miner      decision logic only
auth/           sign-in: device flow and browser control over CDP
gui/            window and tray
notify/         Telegram
```

The split is deliberate: `protocol` describes what Twitch dictates (GraphQL
persisted-query hashes, the `minute-watched` event format, topic names), while
`config` holds what we decided. Mixing them means not knowing which of the two
you are allowed to change.

Browser control is a custom Chrome DevTools Protocol client on top of `aiohttp`.
Playwright and Selenium are deliberately not used: both drag in runtimes of
their own, and the project's requirement is one self-contained binary per platform.

## Limitations

- Autostart from the GUI is Windows-only. Linux/macOS: GUI+tray via the v1.2
  binary; manual autostart — [docs/autostart-linux-macos.md](docs/autostart-linux-macos.md);
  no auto-update.
- Snap Chromium on Linux often fails with our `--user-data-dir`. Prefer Google
  Chrome, non-snap Chromium, or an explicit `browser_path`.
- Twitch makes no promise that its private API stays put. If persisted-query
  hashes change, `core/protocol.py` is what needs fixing.
- One account per process.

## Disclaimer

The program does what an open stream in a browser would do — just without a
person in front of the screen. Automating viewing may conflict with Twitch's
Terms of Service. The risk is the user's; the author accepts no responsibility
for consequences to your account.

## Licence

MIT — see [LICENSE](LICENSE).
