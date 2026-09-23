# Автозапуск на Linux і macOS

Галочка «Запускати разом із системою» у вікні працює лише на Windows
(запис у `HKCU\...\Run`). На Linux і macOS увімкніть автозапуск вручну —
нижче готові шаблони. Усюди запускаємо з `--tray --log`, як і на Windows.

Шлях до інтерпретатора й скрипта підставте свої (або шлях до зібраного
бінарника, коли він з’явиться).

## Linux — systemd user unit

Файл `~/.config/systemd/user/twitch-drop-farm.service`:

```ini
[Unit]
Description=Twitch Drop Farm
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=/path/to/env/bin/python /path/to/TwitchDropFarm/main.py --tray --log
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
```

Увімкнути:

```bash
systemctl --user daemon-reload
systemctl --user enable --now twitch-drop-farm.service
```

Перевірити: `systemctl --user status twitch-drop-farm.service`

Вимкнути: `systemctl --user disable --now twitch-drop-farm.service`

На сервері без GUI краще `--console` замість `--tray` і `WantedBy=default.target`.

## macOS — LaunchAgent

Файл `~/Library/LaunchAgents/com.twitchdropfarm.agent.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.twitchdropfarm.agent</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/env/bin/python</string>
    <string>/path/to/TwitchDropFarm/main.py</string>
    <string>--tray</string>
    <string>--log</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <false/>
  <key>StandardOutPath</key>
  <string>/tmp/twitch-drop-farm.out.log</string>
  <key>StandardErrorPath</key>
  <string>/tmp/twitch-drop-farm.err.log</string>
</dict>
</plist>
```

Увімкнути:

```bash
launchctl load ~/Library/LaunchAgents/com.twitchdropfarm.agent.plist
```

Вимкнути:

```bash
launchctl unload ~/Library/LaunchAgents/com.twitchdropfarm.agent.plist
```

## Трей

Іконка в треї вже на `pystray` (без `pywin32` поза Windows). На Linux для
меню в панелі часто потрібен AppIndicator, наприклад:

- Debian/Ubuntu: `gir1.2-ayatanaappindicator3-0.1` або `gir1.2-appindicator3-0.1`
- Fedora: `libappindicator-gtk3`

`pywin32` у `requirements.txt` лишається лише для Windows (бекенд сповіщень
pystray) і на інші ОС не ставиться.
