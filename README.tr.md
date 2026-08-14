[Українська](README.md) · [English](README.en.md) · [Español](README.es.md) · [Português](README.pt.md) · [Deutsch](README.de.md) · [Français](README.fr.md) · [Polski](README.pl.md) · **Türkçe** · [简体中文](README.zh.md)

# TwitchDropFarm

Twitch'te **timed drop** toplama — açık bir tarayıcı olmadan, ekranda yayın
açmadan. Program envanteri kendi okur, neyi toplamaya değeceğine karar verir,
uygun bir kanal bulur ve Twitch'e izlenme süresi iletir; alınan ödülleri de
penceresinde, sistem tepsisinde ve Telegram'da gösterir.

Tek bir `.exe`, yanında hiçbir çalışma ortamı yok: ne Node.js, ne Playwright, ne
de paketlenmiş bir tarayıcı. Giriş için sistemde zaten kurulu olan tarayıcı
kullanılır (Edge veya Chrome).

> **Dil hakkında.** Arayüz, günlükler ve kaynak kodu yorumları Ukraynacadır. Bu
> belgedeki her şey — dosya adları, ayar anahtarları, komutlar — programla birebir
> aynıdır.

## Neler yapar

- **Yetişemeyeceğini önceden söyler**: kalan süre, hâlâ gereken izleme
  dakikalarından azsa bunu vaktinde bildirir.
- **Kendi seçer.** Dört kip: öncelik listesine göre, en yakın bitiş tarihine
  göre, en sıkı uyuma göre (mümkün olduğunca çok kampanyayı bitirmek için) veya
  yalnızca hesabın bağlı olduğu ve gerçek bir eşya verilen kampanyalar.
- **198 kanala kadar izler** — PubSub üzerinden — ve yayın kapandığında geçiş
  yapar.
- **Dropları otomatik alır** ve hemen bir sonrakine geçer.
- **Dört sekmeli bir pencere**: Toplama, Kanallar, Envanter, Ayarlar.
- **Sistem tepsisi**: küçültme, bildirimler, arka planda başlatma.
- **Telegram botu**: durum, envanter, kampanyalar, duraklat/devam et, kanal
  değiştirme, öncelik yönetimi, tam yeniden başlatma — düğmelerle veya komutlarla.
- **Aksaklıkları atlatır**: ağ kopması, DNS'in kaybolması, bilgisayarın uykuya
  geçmesi, Twitch'in geçici hataları. En kötü durumda kendini yeniden başlatır.
- **Takılmayı fark eder**: dakikalar birikmeyi bırakırsa (örneğin aynı hesapla
  başka bir yerde elle Twitch izleniyorsa) susmak yerine bunu söyler.

## Gereksinimler

- Windows 10/11
- Python 3.10+ — yalnızca kaynaktan çalıştırmak veya `.exe` derlemek için
- Edge veya Chrome — yalnızca ilk giriş için

## Çalıştırma

Kaynaktan:

```bash
python -m venv env
env\Scripts\pip install -r requirements.txt
env\Scripts\python main.py
```

Derlenmiş `.exe`:

```bash
dist\TwitchDropFarm.exe
```

İlk açılışta program, doğrulama kodunu içeren bir Twitch sayfası açar. Giriş
yapıldıktan sonra belirteç saklanır ve bir daha sorulmaz.

### Argümanlar

| Argüman | Ne yapar |
|---|---|
| `--console` | pencere yok, yalnızca konsol — sunucu veya otomatik başlatma için |
| `--tray` | tepsiye küçültülmüş olarak başla |
| `--log` | `log.txt` yaz |
| `-v`, `-vv`, `-vvv` | günlüklerde daha çok ayrıntı (tekrarlanabilir) |
| `--auth-only` | yalnızca kimlik doğrula ve çık |
| `--dump-inventory` | tüm kampanyaları ve dropları yaz, sonra çık |
| `--test-telegram` | deneme iletisi gönder ve çık |
| `--version` | sürüm |

## Ayarlar

`settings.json`, durum dizininde bulunur (aşağıya bakın) ve ilk açılışta kendi
oluşur. Örnek: [`settings.example.json`](settings.example.json).

| Anahtar | Anlamı |
|---|---|
| `farm_mode` | `0` — öncelik listesi, `1` — en yakın bitiş, `2` — en sıkı uyum, `3` — yalnızca bağlı kampanyalar |
| `priority` | oyunlar, tercih sırasına göre |
| `exclude` | dokunulmayacak oyunlar |
| `farm_cosmetics` | yalnızca rozet ve emote veren kampanyaları kabul et |
| `verify_channel_drops` | her kanalda dropların gerçekten açık olduğunu doğrula (daha yavaş, daha güvenilir) |
| `start_in_tray` | küçültülmüş başlat |
| `tray_notifications` | açılır bildirimler |
| `dark_theme` | koyu pencere teması |
| `drop_images` | ödül görsellerini indir ve listede göster (~2,5 MB önbellek) |
| `browser_path` | otomatik bulma başarısız olursa tarayıcı yolu |
| `proxy` | istekler için vekil sunucu |

Kip ve öncelik, ayarlar sekmesinden değiştirmek daha rahattır; gerisi dosyada
elle. Dosyadaki değişiklikler yeniden başlatmadan sonra geçerli olur.

### Telegram

1. [@BotFather](https://t.me/BotFather) üzerinden bir bot oluşturun ve belirteci
   alın.
2. Botunuza herhangi bir şey yazın ki `chat_id` değerinizi görsün.
3. `settings.json` içinde:

```json
"telegram": {
    "enabled": true,
    "bot_token": "BELIRTECINIZ",
    "chat_ids": [CHAT_ID_DEGERINIZ],
    "allow_control": true,
    "notify_critical": true,
    "notify_rewards": true,
    "notify_routine": false,
    "report_every_hours": 6
}
```

4. Doğrulayın: `main.py --test-telegram`

`chat_ids` bir beyaz listedir. Başka yerden geleni yok sayar; yani botu bulan bir
yabancı miner'ı yönetemez.

Komutlar: `/status`, `/inventory`, `/campaigns`, `/pause`, `/resume`,
`/switch <kanal>`, `/priority add|remove <oyun>`, `/reload`, `/hide`, `/show`, `/reboot`,
`/menu`, `/help`. Argüman alan ikisi dışında hepsi düğme olarak da vardır.

## Durum nerede tutulur

`%LOCALAPPDATA%\TwitchDropFarm\`

```
auth.json        Twitch belirteci
cookies.jar      çerezler
settings.json    ayarlar
log.txt          günlük (--log ile)
lock.file        aynı anda iki kopyaya karşı koruma
browser_profile  giriş için kullanılan tarayıcı profili
```

Durum dizini programın yanında değil, kullanıcı başına birdir — aksi hâlde her
yeni kopya yeniden giriş isterdi. Tersini istiyorsanız (USB bellek, başkasının
bilgisayarı) `.exe` yanına boş bir `portable.txt` dosyası koyun: durum orada
yaşar.

## Derleme

```bash
env\Scripts\python.exe -m PyInstaller build.spec --noconfirm
```

Kolayca yanılabileceğiniz üç nokta:

- Derlemeden önce **çalışan `.exe`'yi durdurun**, yoksa `PermissionError`.
- **Derlemeyi yarıda kesmeyin.** Yarıda kalan PyInstaller, `DLL load failed while
  importing _tkinter` ile ölen kırpılmış bir `.exe` bırakır. Kod hatası gibi
  görünür ama değildir.
- Gereksiz yere **`--clean` eklemeyin** — daha yavaş, faydasız.

## Denetimler

```bash
main.py --dump-inventory     canlı Twitch'ten tüm kampanyalar
main.py --test-telegram      bot
tests\core_check.py          çekirdek mantığı (ağsız)
tests\bot_check.py           bot testleri (ağsız)
tests\live_check.py          çekirdek, canlı Twitch'e karşı
```

## Nasıl kurgulanmış

```
core/protocol   Twitch'in özel API'si hakkındaki olgular — bizim kararlarımız değil
core/config     yollar, aralıklar, sınırlar
core/toolbox    bağımsız araçlar
core/api        ağ, yeniden denemeler, dayanıklılık
core/identity   belirteç ve başlıklar
core/model      kampanyalar ve droplar
core/channels   kanallar ve izlenme iletimi
core/pubsub     abonelikler
core/miner      yalnızca karar mantığı
auth/           giriş: device flow ve CDP üzerinden tarayıcı denetimi
gui/            pencere ve tepsi
notify/         Telegram
```

Ayrım bilinçlidir: `protocol`, Twitch'in dayattığını anlatır (GraphQL persisted
query özetleri, `minute-watched` olayının biçimi, konu adları); `config` ise bizim
kararlarımızı tutar. İkisini karıştırmak, hangisini değiştirebileceğinizi
bilmemek demektir.

Tarayıcı denetimi, `aiohttp` üzerine yazılmış kendi Chrome DevTools Protocol
istemcimizdir. Playwright ve Selenium bilerek kullanılmaz: ikisi de kendi çalışma
ortamlarını sürükler, oysa projenin şartı tek ve kendine yeten bir `.exe`.

## Sınırlar

- Yalnızca Windows. Mimaride taşımayı engelleyen bir şey yok, ama tarayıcı
  yolları, tepsi ve otomatik başlatma Windows için yazılmıştır.
- Twitch, özel API'sinin olduğu gibi kalacağına söz vermez. Persisted query
  özetleri değişirse onarılması gereken `core/protocol.py`'dir.
- Süreç başına tek hesap.

## Uyarı

Program, tarayıcıda açık bir yayının yapacağını yapar — sadece ekranın önünde bir
insan olmadan. İzlemeyi otomatikleştirmek Twitch'in Hizmet Şartları'yla
çelişebilir. Risk kullanıcıya aittir; yazar, hesabınıza gelebilecek sonuçlardan
sorumluluk kabul etmez.

## Lisans

MIT — bkz. [LICENSE](LICENSE).
