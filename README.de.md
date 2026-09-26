[Українська](README.md) · [English](README.en.md) · [Español](README.es.md) · [Português](README.pt.md) · **Deutsch** · [Français](README.fr.md) · [Polski](README.pl.md) · [Türkçe](README.tr.md) · [简体中文](README.zh.md)

# TwitchDropFarm

![Architektur](docs/architecture.en.png)

Interaktives Diagramm: [im Browser öffnen](https://riasj1dar.github.io/TwitchDropFarm/architecture.en.html) (GitHub zeigt den HTML-Quelltext, keine Seite).


**Timed Drops** auf Twitch farmen — ohne offenen Browser und ohne Stream auf dem
Bildschirm. Das Programm liest das Inventar selbst, entscheidet, was sich zu
farmen lohnt, sucht einen passenden Kanal und liefert Twitch die Zuschauzeit —
eingesammelte Belohnungen zeigt es im Fenster, im Infobereich und in Telegram.

Ein Binary pro Plattform (Windows `.exe`, Linux, macOS), keine Laufzeitumgebungen
daneben: kein Node.js, kein Playwright, kein mitgelieferter Browser. Anmeldung
über Edge, Chrome oder Chromium. Auto-Update nur für Windows `.exe`.

## Was es kann

- **Warnt, wenn es nicht mehr reicht**: bleibt weniger Zeit übrig als noch
  benötigte Zuschauminuten, sagt es das rechtzeitig.
- **Entscheidet selbst.** Vier Modi: nach Prioritätenliste, nach nächstem Ablauf,
  nach engster Passung (um möglichst viele Kampagnen zu schaffen) oder nur das,
  womit das Konto verknüpft ist und wo es einen echten Gegenstand gibt.
- **Behält bis zu 198 Kanäle im Blick** über PubSub und wechselt, wenn ein Stream
  endet.
- **Holt Drops automatisch ab** und geht sofort zum nächsten über.
- **Ein Fenster** mit vier Reitern: Mining, Kanäle, Inventar, Einstellungen.
- **Infobereich**: minimieren, Benachrichtigungen, Start im Hintergrund.
- **Telegram-Bot**: Status, Inventar, Kampagnen, Pause/Fortsetzen, Kanalwechsel,
  Prioritäten verwalten, vollständiger Neustart — per Schaltfläche oder Befehl.
- **Übersteht Störungen**: Netzabbruch, verschwundenes DNS, Ruhezustand des
  Rechners, vorübergehende Twitch-Fehler. Im äußersten Fall startet es sich
  selbst neu.
- **Bemerkt Stillstand**: wenn keine Minuten mehr dazukommen (etwa weil dasselbe
  Konto anderswo von Hand Twitch schaut), sagt es das, statt zu schweigen.
- **Oberflächensprachen** (Einstellungen): Ukrainisch als Vorgabe, außerdem
  English, Español, Português, Deutsch, Français, Polski, Türkçe, 简体中文.
  Kein Russisch. Das Protokoll bleibt ukrainisch. Der Telegram-Bot spricht
  dieselbe Sprache wie das Fenster.

## Voraussetzungen

- Windows 10/11 — volle Unterstützung (Fenster, Tray, `.exe`, Autostart, Auto-Update)
- Linux / macOS — GUI+Tray aus dem Binary oder Quelltext; Autostart manuell ([docs/autostart-linux-macos.md](docs/autostart-linux-macos.md)); kein Auto-Update
- Python 3.10+ — Quelltext oder Binary bauen
- Edge, Chrome oder Chromium — nur für die erste Anmeldung

## Download (v1.2)

Release: [v1.2](https://github.com/RiasJ1Dar/TwitchDropFarm/releases/tag/v1.2).

| Plattform | Datei |
|---|---|
| Windows | [TwitchDropFarm.exe](https://github.com/RiasJ1Dar/TwitchDropFarm/releases/download/v1.2/TwitchDropFarm.exe) (Auto-Update) |
| Linux x86_64 | [TwitchDropFarm-linux-x86_64](https://github.com/RiasJ1Dar/TwitchDropFarm/releases/download/v1.2/TwitchDropFarm-linux-x86_64) |
| macOS | [TwitchDropFarm-macos](https://github.com/RiasJ1Dar/TwitchDropFarm/releases/download/v1.2/TwitchDropFarm-macos) |

`manifest.json` nur für Windows-Auto-Update. Linux/macOS ohne Auto-Update. Autostart manuell: [docs/autostart-linux-macos.md](docs/autostart-linux-macos.md).

## Ausführen

Aus dem Quelltext:

```bash
python -m venv env
env\Scripts\pip install -r requirements.txt
env\Scripts\python main.py
```

Die gebaute `.exe`:

```bash
dist\TwitchDropFarm.exe
```

Beim ersten Start öffnet das Programm eine Twitch-Seite mit einem
Bestätigungscode. Nach der Anmeldung wird das Token gespeichert und nie wieder
abgefragt.

### Argumente

| Argument | Wirkung |
|---|---|
| `--console` | kein Fenster, nur Konsole — für Server oder Autostart |
| `--tray` | minimiert im Infobereich starten |
| `--log` | Protokoll schreiben, auch wenn es in den Einstellungen ausgeschaltet ist |
| `-v`, `-vv`, `-vvv` | mehr Details im Protokoll (wiederholbar) |
| `--auth-only` | nur anmelden und beenden |
| `--dump-inventory` | alle Kampagnen und Drops ausgeben und beenden |
| `--export` | Verlauf und Inventar als CSV und HTML in den Zustandsordner speichern und beenden |
| `--probe-protocol` | prüfen, ob Twitch die Anfragen des Programms noch annimmt, und beenden |
| `--test-telegram` | Testnachricht senden und beenden |
| `--version` | Version |

## Einstellungen

`settings.json` liegt im Zustandsverzeichnis (siehe unten) und wird beim ersten
Start selbst angelegt. Vorlage:
[`settings.example.json`](settings.example.json).

| Schlüssel | Bedeutung |
|---|---|
| `farm_mode` | `0` — Prioritätenliste, `1` — nächster Ablauf, `2` — engste Passung, `3` — nur verknüpfte Kampagnen |
| `language` | `uk` als Vorgabe; außerdem `en`, `es`, `pt`, `de`, `fr`, `pl`, `tr`, `zh`; `auto` — Windows-Sprache. Der Bot folgt dem Fenster |
| `priority` | Spiele in bevorzugter Reihenfolge |
| `exclude` | Spiele, die unangetastet bleiben |
| `farm_cosmetics` | Kampagnen annehmen, die nur Abzeichen und Emotes geben |
| `verify_channel_drops` | bei jedem Kanal prüfen, ob Drops wirklich aktiv sind (langsamer, verlässlicher) |
| `start_in_tray` | minimiert starten |
| `tray_notifications` | Einblendungen |
| `dark_theme` | dunkles Fensterdesign |
| `drop_images` | Belohnungsbilder laden und in der Liste zeigen (Cache ~6 MB) |
| `image_size` | Bildgröße in der Liste, 16–96 |
| `inventory_view` | `list` — dichte Liste, `tiles` — Karten mit großen Bildern |
| `browser_path` | Pfad zum Browser, falls die Erkennung fehlschlägt |
| `proxy` | Proxy für Anfragen |
| `watch_games` | beobachtete Spiele: Benachrichtigung, sobald eine neue Kampagne erscheint |
| `autostart` | mit Windows starten (der Eintrag wird wiederhergestellt, wenn er aus der Registry verschwindet) |
| `check_updates` | einmal pro Start bei GitHub nach neueren Dateien fragen |
| `skip_hopeless` | Kampagnen überspringen, in denen bis zum Ende kein Drop mehr zu schaffen ist |
| `progress_style` | `rainbow` — schillernder Fortschrittsbalken, `state` — Farbe nach Zustand |
| `theme_preset` | Farbschema: leer — eingebaut, `ocean`, `forest`, `ember`, `grape`, `mono` |
| `keep_log` | Protokoll führen (standardmäßig an) |
| `log_dir` | Protokollordner; leer — `Dokumente\TwitchDropFarm` |
| `hint_links` | auf Kampagnen hinweisen, für die das Konto verknüpft werden muss (für Spiele aus `priority` und `watch_games`) |
| `discord_webhook` | Discord-Webhook-Adresse für wichtige Ereignisse (Belohnungen, Stillstand, verlorene Verknüpfung, Fehler); leer — aus |

Modus und Priorität lassen sich bequemer im Einstellungsreiter ändern, den Rest
von Hand in der Datei. Änderungen an der Datei greifen nach einem Neustart.

### Telegram

1. Einen Bot bei [@BotFather](https://t.me/BotFather) anlegen und das Token holen.
2. Dem eigenen Bot irgendetwas schreiben, damit er die `chat_id` sieht.
3. In `settings.json`:

```json
"telegram": {
    "enabled": true,
    "bot_token": "TOKEN_HIER",
    "chat_ids": [DEINE_CHAT_ID],
    "allow_control": true,
    "notify_critical": true,
    "notify_rewards": true,
    "notify_routine": false,
    "report_every_hours": 6
}
```

4. Prüfen: `main.py --test-telegram`

`chat_ids` ist eine Positivliste. Alles, was von anderswo kommt, wird ignoriert —
ein Fremder, der den Bot findet, kann den Miner also nicht steuern.

Befehle: `/status`, `/inventory`, `/campaigns`, `/pause`, `/resume`,
`/switch <Kanal>`, `/priority add|remove <Spiel>`, `/reload`, `/hide`, `/show`, `/reboot`,
`/watch add|remove <Spiel>`, `/report [Tage]`, `/export`, `/update`, `/menu`, `/help`.
`/report` fasst die letzten 90 Tage zusammen (1 bis 365 möglich), `/update` installiert ein gefundenes Update.
Die meisten Befehle gibt es auch als Schaltflächen in `/menu`.

## Wo der Zustand liegt

`%LOCALAPPDATA%\TwitchDropFarm\`

```
auth.json        Twitch-Token
cookies.jar      Cookies
settings.json    Einstellungen
history.jsonl    Drop-Verlauf (für /report und Export)
seen-campaigns.json  bereits gesehene Kampagnen (für watch_games)
theme.json       aus dem Fenster gespeichertes Theme
images           Cache der Belohnungsbilder
lock.file        Schutz vor zwei Kopien gleichzeitig
browser_profile  Browserprofil für die Anmeldung
```

Das Protokoll landet in `Dokumente\TwitchDropFarm\log.txt` (oder in `log_dir`); gibt es keinen
Dokumente-Ordner, im Zustandsordner. Der Export (`--export`, `/export`) schreibt `history.csv`,
`history.html`, `inventory.csv` und `inventory.html` in den Zustandsordner.

Das Zustandsverzeichnis gibt es einmal pro Benutzer statt neben dem Programm —
sonst würde jede neue Kopie erneut nach der Anmeldung fragen. Für den
umgekehrten Fall (USB-Stick, fremder Rechner) eine leere Datei `portable.txt`
neben die `.exe` legen: dann liegt der Zustand dort.

## Bauen

```bash
env\Scripts\python.exe -m PyInstaller build.spec --noconfirm
```

Drei Stolperfallen:

- **Die laufende `.exe` beenden**, sonst `PermissionError`.
- **Den Bau nicht abbrechen.** Ein abgebrochener PyInstaller hinterlässt eine
  abgeschnittene `.exe`, die mit `DLL load failed while importing _tkinter`
  stirbt. Das sieht nach einem Codefehler aus, ist aber keiner.
- **`--clean` nicht ohne Grund** hinzufügen — langsamer, ohne Nutzen.

## Prüfungen

```bash
main.py --dump-inventory     alle Kampagnen vom echten Twitch
main.py --test-telegram      der Bot
tests\core_check.py          Kernlogik (ohne Netz)
tests\bot_check.py           Bot-Tests (ohne Netz)
tests\live_check.py          Kern gegen echtes Twitch
```

## Wie es aufgebaut ist

```
core/protocol   Fakten über Twitchs private API — keine Entscheidungen von uns
core/config     Pfade, Intervalle, Grenzen
core/toolbox    eigenständige Werkzeuge
core/api        Netz, Wiederholungen, Robustheit
core/identity   Token und Kopfzeilen
core/model      Kampagnen und Drops
core/channels   Kanäle und Auslieferung der Zuschauzeit
core/pubsub     Abonnements
core/miner      nur Entscheidungslogik
auth/           Anmeldung: Device Flow und Browsersteuerung über CDP
gui/            Fenster und Infobereich
notify/         Telegram
```

Die Trennung ist Absicht: `protocol` beschreibt, was Twitch vorgibt (Hashes der
GraphQL-Persisted-Queries, das Format des Ereignisses `minute-watched`,
Topic-Namen), `config` dagegen das, was wir entschieden haben. Beides zu
vermischen heißt, nicht mehr zu wissen, was davon man ändern darf.

Die Browsersteuerung ist ein eigener Client für das Chrome DevTools Protocol auf
Basis von `aiohttp`. Playwright und Selenium werden bewusst nicht verwendet:
beide schleppen eigene Laufzeitumgebungen mit, und die Vorgabe des Projekts ist
eine einzige, in sich geschlossene `.exe`.

## Grenzen

- Nur Windows. Die Architektur steht einer Portierung nicht im Weg, aber
  Browserpfade, Infobereich und Autostart sind für Windows geschrieben.
- Twitch verspricht nicht, dass die private API so bleibt. Ändern sich die
  Persisted-Query-Hashes, gehört `core/protocol.py` repariert.
- Ein Konto pro Prozess.

## Hinweis

Das Programm tut dasselbe wie ein im Browser geöffneter Stream — nur ohne
Menschen davor. Automatisiertes Zuschauen kann den Nutzungsbedingungen von
Twitch widersprechen. Das Risiko trägt die Nutzerin oder der Nutzer; der Autor
übernimmt keine Verantwortung für Folgen für das Konto.

## Lizenz

MIT — siehe [LICENSE](LICENSE).
