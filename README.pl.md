[Українська](README.md) · [English](README.en.md) · [Español](README.es.md) · [Português](README.pt.md) · [Deutsch](README.de.md) · [Français](README.fr.md) · **Polski** · [Türkçe](README.tr.md) · [简体中文](README.zh.md)

# TwitchDropFarm

![Architektura](docs/architecture.en.png)

Interaktywny schemat: [otwórz w przeglądarce](https://riasj1dar.github.io/TwitchDropFarm/architecture.en.html) (GitHub pokazuje kod HTML, nie stronę).


Farmienie **timed dropów** na Twitchu bez otwartej przeglądarki i bez streama na
ekranie. Program sam czyta ekwipunek, decyduje, co warto farmić, znajduje
odpowiedni kanał i dostarcza Twitchowi czas oglądania — a odebrane nagrody
pokazuje w oknie, w zasobniku systemowym i na Telegramie.

Jeden plik na platformę (Windows `.exe`, Linux, macOS), bez środowisk
uruchomieniowych obok: bez Node.js, bez Playwrighta, bez przeglądarki w pakiecie.
Logowanie przez Edge, Chrome lub Chromium. Autoaktualizacja tylko dla Windows `.exe`.

## Co potrafi

- **Ostrzega, gdy nie zdąży**: jeśli do końca kampanii zostało mniej czasu niż
  potrzebnych minut oglądania, powie o tym z wyprzedzeniem.
- **Wybiera sam.** Cztery tryby: według listy priorytetów, według najbliższego
  terminu, według najlepszego dopasowania (by zdążyć z jak największą liczbą
  kampanii) albo wyłącznie to, z czym konto jest powiązane i gdzie wydawany jest
  prawdziwy przedmiot.
- **Pilnuje do 198 kanałów** przez PubSub i przełącza się, gdy stream gaśnie.
- **Odbiera dropy automatycznie** i od razu przechodzi do następnego.
- **Okno** z czterema zakładkami: Kopanie, Kanały, Ekwipunek, Ustawienia.
- **Zasobnik systemowy**: minimalizacja, powiadomienia, start w tle.
- **Bot Telegrama**: stan, ekwipunek, kampanie, pauza/wznowienie, zmiana kanału,
  zarządzanie priorytetami, pełny restart — przyciskami albo poleceniami.
- **Przeżywa awarie**: zerwanie sieci, zniknięcie DNS, uśpienie komputera,
  przejściowe błędy Twitcha. W ostateczności restartuje sam siebie.
- **Zauważa zastój**: jeśli minuty przestają się naliczać (na przykład dlatego,
  że tym samym kontem ktoś ogląda Twitcha ręcznie), powie o tym, zamiast milczeć.
- **Języki interfejsu** (Ustawienia): domyślnie ukraiński, także English,
  Español, Português, Deutsch, Français, Polski, Türkçe, 简体中文. Bez rosyjskiego.
  Dziennik zostaje po ukraińsku. Bot Telegrama używa tego samego języka co okno.

## Wymagania

- Windows 10/11 — pełne wsparcie (okno, tacka, `.exe`, autostart, autoaktualizacja)
- Linux / macOS — GUI+tacka z binarki lub ze źródeł; autostart ręcznie ([docs/autostart-linux-macos.md](docs/autostart-linux-macos.md)); bez autoaktualizacji
- Python 3.10+ — źródła lub budowa binarki
- Edge, Chrome lub Chromium — tylko do pierwszego logowania

## Pobieranie (v1.2)

Release: [v1.2](https://github.com/RiasJ1Dar/TwitchDropFarm/releases/tag/v1.2).

| Platforma | Plik |
|---|---|
| Windows | [TwitchDropFarm.exe](https://github.com/RiasJ1Dar/TwitchDropFarm/releases/download/v1.2/TwitchDropFarm.exe) (autoaktualizacja) |
| Linux x86_64 | [TwitchDropFarm-linux-x86_64](https://github.com/RiasJ1Dar/TwitchDropFarm/releases/download/v1.2/TwitchDropFarm-linux-x86_64) |
| macOS | [TwitchDropFarm-macos](https://github.com/RiasJ1Dar/TwitchDropFarm/releases/download/v1.2/TwitchDropFarm-macos) |

`manifest.json` tylko do autoaktualizacji Windows. Linux/macOS bez autoaktualizacji. Autostart ręcznie: [docs/autostart-linux-macos.md](docs/autostart-linux-macos.md).

## Uruchamianie

Ze źródeł:

```bash
python -m venv env
env\Scripts\pip install -r requirements.txt
env\Scripts\python main.py
```

Zbudowany `.exe`:

```bash
dist\TwitchDropFarm.exe
```

Przy pierwszym uruchomieniu program otwiera stronę Twitcha z kodem
potwierdzającym. Po zalogowaniu token zostaje zapisany i nigdy więcej nie jest
wymagany.

### Argumenty

| Argument | Działanie |
|---|---|
| `--console` | bez okna, sama konsola — dla serwera albo autostartu |
| `--tray` | start zminimalizowany do zasobnika |
| `--log` | prowadzić dziennik, nawet gdy jest wyłączony w ustawieniach |
| `-v`, `-vv`, `-vvv` | więcej szczegółów w dziennikach (można powtarzać) |
| `--auth-only` | tylko uwierzytelnić się i wyjść |
| `--dump-inventory` | wypisać wszystkie kampanie i dropy, potem wyjść |
| `--export` | zapisać historię i ekwipunek jako CSV i HTML w folderze stanu, potem wyjść |
| `--probe-protocol` | sprawdzić, czy Twitch nadal przyjmuje zapytania programu, potem wyjść |
| `--test-telegram` | wysłać wiadomość testową i wyjść |
| `--version` | wersja |

## Ustawienia

`settings.json` leży w katalogu stanu (patrz niżej) i tworzy się sam przy
pierwszym uruchomieniu. Wzór:
[`settings.example.json`](settings.example.json).

| Klucz | Znaczenie |
|---|---|
| `farm_mode` | `0` — lista priorytetów, `1` — najbliższy termin, `2` — najlepsze dopasowanie, `3` — tylko powiązane kampanie |
| `language` | `uk` domyślnie; także `en`, `es`, `pt`, `de`, `fr`, `pl`, `tr`, `zh`; `auto` — język Windows. Bot idzie za oknem |
| `priority` | gry w kolejności preferencji |
| `exclude` | gry, których nie ruszać |
| `farm_cosmetics` | przyjmować kampanie dające wyłącznie odznaki i emotki |
| `verify_channel_drops` | sprawdzać przy każdym kanale, czy dropy są naprawdę włączone (wolniej, pewniej) |
| `start_in_tray` | start zminimalizowany |
| `tray_notifications` | powiadomienia wyskakujące |
| `dark_theme` | ciemny motyw okna |
| `drop_images` | pobierać obrazki nagród i pokazywać je na liście (pamięć podręczna ~6 MB) |
| `image_size` | rozmiar obrazka na liście, 16–96 |
| `inventory_view` | `list` — gęsta lista, `tiles` — kafelki z dużymi obrazkami |
| `browser_path` | ścieżka do przeglądarki, jeśli wykrywanie zawiodło |
| `proxy` | proxy dla zapytań |
| `watch_games` | obserwowane gry: powiadomienie, gdy pojawi się nowa kampania |
| `autostart` | uruchamiać razem z Windows (wpis wraca, jeśli zniknie z rejestru) |
| `check_updates` | raz na uruchomienie pytać GitHub, czy są nowsze pliki |
| `skip_hopeless` | pomijać kampanie, w których do końca nie da się zdobyć żadnego dropa |
| `progress_style` | `rainbow` — mieniący się pasek postępu, `state` — kolor według stanu |
| `theme_preset` | zestaw kolorów: puste — wbudowany, `ocean`, `forest`, `ember`, `grape`, `mono` |
| `keep_log` | prowadzić dziennik (domyślnie włączone) |
| `log_dir` | folder dziennika; puste — `Dokumenty\TwitchDropFarm` |
| `hint_links` | podpowiadać o kampaniach wymagających powiązania konta (dla gier z `priority` i `watch_games`) |
| `discord_webhook` | adres webhooka Discord dla ważnych zdarzeń (nagrody, przestój, utracone powiązanie, błędy); puste — wyłączone |

Tryb i priorytet wygodniej zmieniać w zakładce ustawień — resztę ręcznie w
pliku. Zmiany w pliku działają po restarcie.

### Telegram

1. Utwórz bota w [@BotFather](https://t.me/BotFather) i weź token.
2. Napisz swojemu botowi cokolwiek, żeby zobaczył twój `chat_id`.
3. W `settings.json`:

```json
"telegram": {
    "enabled": true,
    "bot_token": "TWOJ_TOKEN",
    "chat_ids": [TWOJ_CHAT_ID],
    "allow_control": true,
    "notify_critical": true,
    "notify_rewards": true,
    "notify_routine": false,
    "report_every_hours": 6
}
```

4. Sprawdź: `main.py --test-telegram`

`chat_ids` to biała lista. Wszystko, co przyjdzie skądinąd, jest ignorowane, więc
obcy, który znajdzie bota, nie przejmie kontroli nad minerem.

Polecenia: `/status`, `/inventory`, `/campaigns`, `/pause`, `/resume`,
`/switch <kanał>`, `/priority add|remove <gra>`, `/reload`, `/hide`, `/show`, `/reboot`,
`/watch add|remove <gra>`, `/report [dni]`, `/export`, `/update`, `/menu`, `/help`.
`/report` podsumowuje ostatnie 90 dni (od 1 do 365), `/update` instaluje znalezioną aktualizację.
Większość poleceń jest też dostępna jako przyciski w `/menu`.

## Gdzie leży stan

`%LOCALAPPDATA%\TwitchDropFarm\`

```
auth.json        token Twitcha
cookies.jar      ciasteczka
settings.json    ustawienia
history.jsonl    historia dropów (dla /report i eksportu)
seen-campaigns.json  już widziane kampanie (dla watch_games)
theme.json       motyw zapisany z okna
images           pamięć podręczna obrazów nagród
lock.file        zabezpieczenie przed dwiema kopiami naraz
browser_profile  profil przeglądarki do logowania
```

Dziennik trafia do `Dokumenty\TwitchDropFarm\log.txt` (albo do `log_dir`); gdy nie ma folderu
Dokumenty — do folderu stanu. Eksport (`--export`, `/export`) zapisuje `history.csv`,
`history.html`, `inventory.csv` i `inventory.html` w folderze stanu.

Katalog stanu jest jeden na użytkownika, a nie obok programu — inaczej każda nowa
kopia prosiłaby o logowanie od nowa. Aby było odwrotnie (pendrive, cudzy
komputer), połóż pusty plik `portable.txt` obok `.exe`: wtedy stan zamieszka tam.

## Budowanie

```bash
env\Scripts\python.exe -m PyInstaller build.spec --noconfirm
```

Trzy łatwe sposoby, żeby się sparzyć:

- **Zatrzymaj działający `.exe`** przed budowaniem, inaczej `PermissionError`.
- **Nie przerywaj budowania.** Przerwany PyInstaller zostawia obcięty `.exe`,
  który umiera z `DLL load failed while importing _tkinter`. Wygląda to na wadę
  kodu, ale nią nie jest.
- **Nie dodawaj `--clean`** bez powodu — wolniej i bez pożytku.

## Sprawdzenia

```bash
main.py --dump-inventory     wszystkie kampanie z żywego Twitcha
main.py --test-telegram      bot
tests\core_check.py          logika rdzenia (bez sieci)
tests\bot_check.py           testy bota (bez sieci)
tests\live_check.py          rdzeń wobec żywego Twitcha
```

## Jak to jest zbudowane

```
core/protocol   fakty o prywatnym API Twitcha — nie nasze decyzje
core/config     ścieżki, odstępy, limity
core/toolbox    niezależne narzędzia
core/api        sieć, ponowienia, odporność
core/identity   token i nagłówki
core/model      kampanie i dropy
core/channels   kanały i dostarczanie oglądania
core/pubsub     subskrypcje
core/miner      wyłącznie logika decyzji
auth/           logowanie: device flow i sterowanie przeglądarką przez CDP
gui/            okno i zasobnik
notify/         Telegram
```

Podział jest celowy: `protocol` opisuje to, co dyktuje Twitch (skróty persisted
queries GraphQL, format zdarzenia `minute-watched`, nazwy tematów), a `config` —
to, co zdecydowaliśmy my. Mieszanie ich oznacza, że nie wiadomo, co wolno
zmieniać.

Sterowanie przeglądarką to własny klient Chrome DevTools Protocol na bazie
`aiohttp`. Playwright i Selenium celowo nie są używane: oba ciągną za sobą
własne środowiska uruchomieniowe, a wymóg projektu to jeden samowystarczalny
`.exe`.

## Ograniczenia

- Tylko Windows. Architektura nie stoi na przeszkodzie portowaniu, ale ścieżki
  przeglądarek, zasobnik i autostart są napisane pod Windows.
- Twitch nie obiecuje, że jego prywatne API zostanie bez zmian. Jeśli skróty
  persisted queries się zmienią, naprawiać trzeba `core/protocol.py`.
- Jedno konto na proces.

## Zastrzeżenie

Program robi to samo, co robiłby stream otwarty w przeglądarce — tylko bez
człowieka przed ekranem. Automatyzacja oglądania może być sprzeczna z Warunkami
korzystania z Twitcha. Ryzyko ponosi użytkownik; autor nie bierze
odpowiedzialności za skutki dla konta.

## Licencja

MIT — patrz [LICENSE](LICENSE).
