[Українська](README.md) · [English](README.en.md) · [Español](README.es.md) · [Português](README.pt.md) · **Deutsch** · [Français](README.fr.md) · [Polski](README.pl.md) · [Türkçe](README.tr.md) · [简体中文](README.zh.md)

# TwitchDropFarm

**Timed Drops** auf Twitch farmen — ohne offenen Browser und ohne Stream auf dem
Bildschirm. Das Programm liest das Inventar selbst, entscheidet, was sich zu
farmen lohnt, sucht einen passenden Kanal und liefert Twitch die Zuschauzeit —
eingesammelte Belohnungen zeigt es im Fenster, im Infobereich und in Telegram.

Eine einzige `.exe`, keine Laufzeitumgebungen daneben: kein Node.js, kein
Playwright, kein mitgelieferter Browser. Für die Anmeldung wird der Browser
genutzt, der ohnehin im System steckt (Edge oder Chrome).

> **Zur Sprache.** Oberfläche, Protokolle und Quelltextkommentare sind auf
> Ukrainisch. Alles, was in diesem Dokument steht — Dateinamen, Einstellungs­
> schlüssel, Befehle — entspricht exakt dem Programm.

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

## Voraussetzungen

- Windows 10/11
- Python 3.10+ — nur zum Ausführen aus dem Quelltext oder zum Bauen der `.exe`
- Edge oder Chrome — nur für die erste Anmeldung

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
| `--log` | `log.txt` schreiben |
| `-v`, `-vv`, `-vvv` | mehr Details im Protokoll (wiederholbar) |
| `--auth-only` | nur anmelden und beenden |
| `--dump-inventory` | alle Kampagnen und Drops ausgeben und beenden |
| `--test-telegram` | Testnachricht senden und beenden |
| `--version` | Version |

## Einstellungen

`settings.json` liegt im Zustandsverzeichnis (siehe unten) und wird beim ersten
Start selbst angelegt. Vorlage:
[`settings.example.json`](settings.example.json).

| Schlüssel | Bedeutung |
|---|---|
| `farm_mode` | `0` — Prioritätenliste, `1` — nächster Ablauf, `2` — engste Passung, `3` — nur verknüpfte Kampagnen |
| `priority` | Spiele in bevorzugter Reihenfolge |
| `exclude` | Spiele, die unangetastet bleiben |
| `farm_cosmetics` | Kampagnen annehmen, die nur Abzeichen und Emotes geben |
| `verify_channel_drops` | bei jedem Kanal prüfen, ob Drops wirklich aktiv sind (langsamer, verlässlicher) |
| `start_in_tray` | minimiert starten |
| `tray_notifications` | Einblendungen |
| `dark_theme` | dunkles Fensterdesign |
| `drop_images` | Belohnungsbilder laden und in der Liste zeigen (Cache ~6 MB) |
| `browser_path` | Pfad zum Browser, falls die Erkennung fehlschlägt |
| `proxy` | Proxy für Anfragen |

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
`/menu`, `/help`. Alles außer den beiden mit Argumenten gibt es als Schaltfläche.

## Wo der Zustand liegt

`%LOCALAPPDATA%\TwitchDropFarm\`

```
auth.json        Twitch-Token
cookies.jar      Cookies
settings.json    Einstellungen
log.txt          Protokoll (mit --log)
lock.file        Schutz vor zwei Kopien gleichzeitig
browser_profile  Browserprofil für die Anmeldung
```

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
