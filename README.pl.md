[Українська](README.md) · [English](README.en.md) · [Español](README.es.md) · [Português](README.pt.md) · [Deutsch](README.de.md) · [Français](README.fr.md) · **Polski** · [Türkçe](README.tr.md) · [简体中文](README.zh.md)

# TwitchDropFarm

Farmienie **timed dropów** na Twitchu bez otwartej przeglądarki i bez streama na
ekranie. Program sam czyta ekwipunek, decyduje, co warto farmić, znajduje
odpowiedni kanał i dostarcza Twitchowi czas oglądania — a odebrane nagrody
pokazuje w oknie, w zasobniku systemowym i na Telegramie.

Jeden `.exe`, żadnych środowisk uruchomieniowych obok: bez Node.js, bez
Playwrighta, bez dołączonej przeglądarki. Do logowania używana jest przeglądarka
już zainstalowana w systemie (Edge albo Chrome).

> **O języku.** Interfejs, dzienniki i komentarze w kodzie są po ukraińsku.
> Wszystko, co podano w tym dokumencie — nazwy plików, klucze ustawień,
> polecenia — odpowiada programowi dokładnie.

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

## Wymagania

- Windows 10/11
- Python 3.10+ — tylko do uruchomienia ze źródeł albo zbudowania `.exe`
- Edge albo Chrome — tylko do pierwszego logowania

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
| `--log` | zapisywanie `log.txt` |
| `-v`, `-vv`, `-vvv` | więcej szczegółów w dziennikach (można powtarzać) |
| `--auth-only` | tylko uwierzytelnić się i wyjść |
| `--dump-inventory` | wypisać wszystkie kampanie i dropy, potem wyjść |
| `--test-telegram` | wysłać wiadomość testową i wyjść |
| `--version` | wersja |

## Ustawienia

`settings.json` leży w katalogu stanu (patrz niżej) i tworzy się sam przy
pierwszym uruchomieniu. Wzór:
[`settings.example.json`](settings.example.json).

| Klucz | Znaczenie |
|---|---|
| `farm_mode` | `0` — lista priorytetów, `1` — najbliższy termin, `2` — najlepsze dopasowanie, `3` — tylko powiązane kampanie |
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
`/menu`, `/help`. Wszystko poza dwoma przyjmującymi argumenty jest dostępne jako
przycisk.

## Gdzie leży stan

`%LOCALAPPDATA%\TwitchDropFarm\`

```
auth.json        token Twitcha
cookies.jar      ciasteczka
settings.json    ustawienia
log.txt          dziennik (przy --log)
lock.file        zabezpieczenie przed dwiema kopiami naraz
browser_profile  profil przeglądarki do logowania
```

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
