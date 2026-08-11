[Українська](README.md) · **English** · [Español](README.es.md) · [Português](README.pt.md) · [Deutsch](README.de.md) · [Français](README.fr.md) · [Polski](README.pl.md) · [Türkçe](README.tr.md) · [简体中文](README.zh.md)

# TwitchDropFarm

Farm **timed drops** on Twitch with no browser window open and no stream on your
screen. The program reads your inventory itself, decides what is worth farming,
finds a suitable channel and delivers watch time to Twitch — then shows claimed
rewards in its window, in the tray and in Telegram.

A single `.exe`, no runtimes alongside it: no Node.js, no Playwright, no bundled
browser. Sign-in uses the browser already installed on your system (Edge or
Chrome).

> **Note on language.** The interface, logs and source comments are in
> Ukrainian. Everything in this document — file names, settings keys, commands —
> matches the program exactly.

## What it does

- **Chooses on its own.** Four modes: by priority list, by nearest deadline, by
  tightest fit (to finish as many campaigns as possible), or only what your
  account is linked to and where a real in-game item is granted.
- **Keeps up to 198 channels under watch** over PubSub and switches when a
  stream goes down.
- **Claims drops automatically** and moves straight on to the next one.
- **A window** with four tabs: Mining, Channels, Inventory, Settings.
- **Tray**: minimise, notifications, start in background.
- **Telegram bot**: status, inventory, campaigns, pause/resume, channel
  switching, priority management, full restart — by buttons or commands.
- **Survives failures**: network loss, DNS disappearing, the computer sleeping,
  transient Twitch errors. In the worst case it restarts itself.
- **Notices stalls**: if minutes stop accruing (for example, because the same
  account is watching Twitch manually somewhere), it says so instead of staying
  silent.

## Requirements

- Windows 10/11
- Python 3.10+ — only to run from source or build the `.exe`
- Edge or Chrome — only for the first sign-in

## Running

From source:

```bash
python -m venv env
env\Scripts\pip install -r requirements.txt
env\Scripts\python main.py
```

The built `.exe`:

```bash
dist\TwitchDropFarm.exe
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
| `priority` | games in order of preference |
| `exclude` | games to leave alone |
| `farm_cosmetics` | accept campaigns that only grant badges and emotes |
| `verify_channel_drops` | check every channel for drops actually being enabled (slower, more reliable) |
| `start_in_tray` | start minimised |
| `tray_notifications` | pop-up notifications |
| `dark_theme` | dark window theme |
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
`/switch <channel>`, `/priority add|remove <game>`, `/reload`, `/reboot`,
`/menu`, `/help`. Everything except the two that take arguments is available as
buttons.

## Where the state lives

`%LOCALAPPDATA%\TwitchDropFarm\`

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
someone else's computer), put an empty `portable.txt` file next to the `.exe`:
the state will then live there.

## Building

```bash
env\Scripts\python.exe -m PyInstaller build.spec --noconfirm
```

Three easy ways to get burned:

- **Stop the running `.exe`** before building, otherwise `PermissionError`.
- **Do not interrupt the build.** An aborted PyInstaller leaves a truncated
  `.exe` that dies with `DLL load failed while importing _tkinter`. It looks
  like a code defect but is not one.
- **Do not add `--clean`** without a reason — slower, no benefit.

## Checks

```bash
main.py --dump-inventory     all campaigns from live Twitch
main.py --test-telegram      the bot
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
their own, and the project's requirement is a single self-contained `.exe`.

## Limitations

- Windows only. Nothing in the architecture prevents a port, but browser paths,
  the tray and autostart are written for Windows.
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
