"""Мови інтерфейсу. Набір як README на GitHub, без російської.

Типово — мова Windows, якщо вона в наборі; інакше українська. Китайська
ніколи не підставляється «бо так випало»: лише коли її обрали або Windows
саме zh.
"""
from __future__ import annotations

import locale

LANGS = ("uk", "en", "es", "pt", "de", "fr", "pl", "tr", "zh")

NAMES = {
    "uk": "Українська",
    "en": "English",
    "es": "Español",
    "pt": "Português",
    "de": "Deutsch",
    "fr": "Français",
    "pl": "Polski",
    "tr": "Türkçe",
    "zh": "简体中文",
}

_current = "uk"


def detect_os() -> str:
    for candidate in (locale.getlocale()[0], locale.getdefaultlocale()[0]):
        if not candidate:
            continue
        code = candidate.replace("-", "_").split("_")[0].lower()
        if code in LANGS:
            return code
    return "uk"


def resolve(stored: str) -> str:
    """Порожнє налаштування — авто. Невідомий код — українська."""
    code = (stored or "").strip().lower()
    if not code or code == "auto":
        return detect_os()
    return code if code in LANGS else "uk"


def set_language(code: str) -> str:
    global _current
    _current = resolve(code)
    return _current


def language() -> str:
    return _current


def t(key: str, **kwargs: object) -> str:
    table = CATALOG.get(_current) or CATALOG["uk"]
    text = table.get(key) or CATALOG["uk"].get(key) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


# Ключі стабільні англійською. Тексти — як у вікні.
_UK = {
    "tab_mining": "Майнінг",
    "tab_channels": "Канали",
    "tab_inventory": "Інвентар",
    "tab_settings": "Налаштування",
    "farming_now": "Зараз фармимо",
    "drop_unknown": "Дроп не визначено",
    "pause": "Призупинити",
    "resume": "Продовжити",
    "hide_tray": "Згорнути в трей",
    "quit_miner": "Вимкнути майнер",
    "log": "Журнал",
    "channels_hint": "Подвійний клік — перемкнутись на канал",
    "col_channel": "Канал",
    "col_game": "Гра",
    "col_viewers": "Глядачі",
    "col_status": "Стан",
    "priority": "Пріоритет ігор",
    "watch_games": "Спостерігати за іграми",
    "watch_hint": "Скажу, коли зʼявиться нова кампанія цієї гри. Фарм не переривається.",
    "language": "Мова",
    "language_auto": "Авто (мова Windows)",
    "language_restart": "Мова застосується після перезапуску програми.",
    "dark_theme": "Темна тема",
    "start_tray": "Запускати в треї",
    "tray_notes": "Сповіщення трею",
    "farm_cosmetics": "Фармити косметику",
    "verify_drops": "Перевіряти дропи каналу",
    "drop_images": "Картинки нагород",
    "check_updates": "Перевіряти оновлення",
    "autostart": "Запускати разом із Windows",
    "connect_bot": "Підключити бота…",
    "telegram": "Telegram",
    "export": "Експорт",
    "farm_going": "● Іде",
    "farm_stalled": "● Стоїть",
    "farm_uncounted": "● Не зараховується",
    "farm_paused": "● Пауза",
    "farm_idle": "● Чекає",
    "tray_show": "Показати вікно",
    "tray_hide": "Сховати вікно",
    "tray_quit": "Вийти",
    "tiles_off": "Картинки вимкнені — увімкніть у налаштуваннях",
}

CATALOG: dict[str, dict[str, str]] = {
    "uk": _UK,
    "en": {
        **{k: v for k, v in _UK.items()},
        "tab_mining": "Mining",
        "tab_channels": "Channels",
        "tab_inventory": "Inventory",
        "tab_settings": "Settings",
        "farming_now": "Farming now",
        "drop_unknown": "Drop not selected",
        "pause": "Pause",
        "resume": "Resume",
        "hide_tray": "Minimize to tray",
        "quit_miner": "Stop miner",
        "log": "Log",
        "channels_hint": "Double-click to switch channel",
        "col_channel": "Channel",
        "col_game": "Game",
        "col_viewers": "Viewers",
        "col_status": "Status",
        "priority": "Game priority",
        "watch_games": "Watch for games",
        "watch_hint": "I'll tell you when a new campaign for this game appears. Farming is not interrupted.",
        "language": "Language",
        "language_auto": "Auto (Windows language)",
        "language_restart": "The language will apply after you restart the program.",
        "dark_theme": "Dark theme",
        "start_tray": "Start in tray",
        "tray_notes": "Tray notifications",
        "farm_cosmetics": "Farm cosmetics",
        "verify_drops": "Verify channel drops",
        "drop_images": "Reward images",
        "check_updates": "Check for updates",
        "autostart": "Start with Windows",
        "connect_bot": "Connect bot…",
        "telegram": "Telegram",
        "export": "Export",
        "farm_going": "● Running",
        "farm_stalled": "● Stalled",
        "farm_uncounted": "● Not counting",
        "farm_paused": "● Paused",
        "farm_idle": "● Waiting",
        "tray_show": "Show window",
        "tray_hide": "Hide window",
        "tray_quit": "Quit",
        "tiles_off": "Images are off — enable them in Settings",
    },
    "es": {
        "tab_mining": "Minería", "tab_channels": "Canales", "tab_inventory": "Inventario",
        "tab_settings": "Ajustes", "farming_now": "Farming ahora", "drop_unknown": "Drop no definido",
        "pause": "Pausar", "resume": "Reanudar", "hide_tray": "Minimizar a la bandeja",
        "quit_miner": "Detener el minero", "log": "Registro",
        "channels_hint": "Doble clic para cambiar de canal",
        "col_channel": "Canal", "col_game": "Juego", "col_viewers": "Espectadores", "col_status": "Estado",
        "priority": "Prioridad de juegos", "watch_games": "Vigilar juegos",
        "watch_hint": "Avisaré cuando aparezca una campaña nueva de este juego. El farm no se interrumpe.",
        "language": "Idioma", "language_auto": "Auto (idioma de Windows)",
        "language_restart": "El idioma se aplicará al reiniciar el programa.",
        "dark_theme": "Tema oscuro", "start_tray": "Iniciar en la bandeja",
        "tray_notes": "Avisos de bandeja", "farm_cosmetics": "Farmear cosmética",
        "verify_drops": "Verificar drops del canal", "drop_images": "Imágenes de recompensas",
        "check_updates": "Buscar actualizaciones", "autostart": "Iniciar con Windows",
        "connect_bot": "Conectar bot…", "telegram": "Telegram", "export": "Exportar",
        "farm_going": "● En marcha", "farm_stalled": "● Parado", "farm_uncounted": "● No cuenta",
        "farm_paused": "● Pausa", "farm_idle": "● En espera",
        "tray_show": "Mostrar ventana", "tray_hide": "Ocultar ventana", "tray_quit": "Salir",
        "tiles_off": "Imágenes desactivadas — actívalas en Ajustes",
    },
    "pt": {
        "tab_mining": "Mineração", "tab_channels": "Canais", "tab_inventory": "Inventário",
        "tab_settings": "Definições", "farming_now": "A farmar agora", "drop_unknown": "Drop não definido",
        "pause": "Pausar", "resume": "Retomar", "hide_tray": "Minimizar para o tabuleiro",
        "quit_miner": "Parar o miner", "log": "Registo",
        "channels_hint": "Duplo clique para mudar de canal",
        "col_channel": "Canal", "col_game": "Jogo", "col_viewers": "Espetadores", "col_status": "Estado",
        "priority": "Prioridade de jogos", "watch_games": "Vigiar jogos",
        "watch_hint": "Aviso quando aparecer uma campanha nova deste jogo. O farm não para.",
        "language": "Idioma", "language_auto": "Auto (idioma do Windows)",
        "language_restart": "O idioma aplica-se depois de reiniciar o programa.",
        "dark_theme": "Tema escuro", "start_tray": "Iniciar no tabuleiro",
        "tray_notes": "Notificações do tabuleiro", "farm_cosmetics": "Farmar cosmética",
        "verify_drops": "Verificar drops do canal", "drop_images": "Imagens das recompensas",
        "check_updates": "Procurar atualizações", "autostart": "Iniciar com o Windows",
        "connect_bot": "Ligar bot…", "telegram": "Telegram", "export": "Exportar",
        "farm_going": "● A correr", "farm_stalled": "● Parado", "farm_uncounted": "● Não conta",
        "farm_paused": "● Pausa", "farm_idle": "● À espera",
        "tray_show": "Mostrar janela", "tray_hide": "Ocultar janela", "tray_quit": "Sair",
        "tiles_off": "Imagens desligadas — ative-as nas Definições",
    },
    "de": {
        "tab_mining": "Mining", "tab_channels": "Kanäle", "tab_inventory": "Inventar",
        "tab_settings": "Einstellungen", "farming_now": "Läuft gerade", "drop_unknown": "Drop nicht festgelegt",
        "pause": "Pause", "resume": "Fortsetzen", "hide_tray": "In den Infobereich",
        "quit_miner": "Miner beenden", "log": "Protokoll",
        "channels_hint": "Doppelklick wechselt den Kanal",
        "col_channel": "Kanal", "col_game": "Spiel", "col_viewers": "Zuschauer", "col_status": "Status",
        "priority": "Spielpriorität", "watch_games": "Spiele beobachten",
        "watch_hint": "Ich sage Bescheid, wenn eine neue Kampagne für dieses Spiel erscheint. Der Farm läuft weiter.",
        "language": "Sprache", "language_auto": "Auto (Windows-Sprache)",
        "language_restart": "Die Sprache gilt nach einem Neustart.",
        "dark_theme": "Dunkles Design", "start_tray": "Im Infobereich starten",
        "tray_notes": "Infobereich-Hinweise", "farm_cosmetics": "Kosmetik farmen",
        "verify_drops": "Kanal-Drops prüfen", "drop_images": "Belohnungsbilder",
        "check_updates": "Nach Updates suchen", "autostart": "Mit Windows starten",
        "connect_bot": "Bot verbinden…", "telegram": "Telegram", "export": "Export",
        "farm_going": "● Läuft", "farm_stalled": "● Steht", "farm_uncounted": "● Zählt nicht",
        "farm_paused": "● Pause", "farm_idle": "● Wartet",
        "tray_show": "Fenster zeigen", "tray_hide": "Fenster verbergen", "tray_quit": "Beenden",
        "tiles_off": "Bilder aus — in den Einstellungen einschalten",
    },
    "fr": {
        "tab_mining": "Minage", "tab_channels": "Chaînes", "tab_inventory": "Inventaire",
        "tab_settings": "Réglages", "farming_now": "Farm en cours", "drop_unknown": "Drop non défini",
        "pause": "Pause", "resume": "Reprendre", "hide_tray": "Réduire dans la barre",
        "quit_miner": "Arrêter le miner", "log": "Journal",
        "channels_hint": "Double-clic pour changer de chaîne",
        "col_channel": "Chaîne", "col_game": "Jeu", "col_viewers": "Spectateurs", "col_status": "État",
        "priority": "Priorité des jeux", "watch_games": "Surveiller des jeux",
        "watch_hint": "J’avertis quand une nouvelle campagne de ce jeu apparaît. Le farm continue.",
        "language": "Langue", "language_auto": "Auto (langue de Windows)",
        "language_restart": "La langue s’applique après redémarrage.",
        "dark_theme": "Thème sombre", "start_tray": "Démarrer dans la barre",
        "tray_notes": "Notifications barre", "farm_cosmetics": "Farmer les cosmétiques",
        "verify_drops": "Vérifier les drops de la chaîne", "drop_images": "Images des récompenses",
        "check_updates": "Vérifier les mises à jour", "autostart": "Démarrer avec Windows",
        "connect_bot": "Connecter le bot…", "telegram": "Telegram", "export": "Exporter",
        "farm_going": "● En cours", "farm_stalled": "● À l’arrêt", "farm_uncounted": "● Non compté",
        "farm_paused": "● Pause", "farm_idle": "● En attente",
        "tray_show": "Afficher la fenêtre", "tray_hide": "Masquer la fenêtre", "tray_quit": "Quitter",
        "tiles_off": "Images désactivées — activez-les dans Réglages",
    },
    "pl": {
        "tab_mining": "Mining", "tab_channels": "Kanały", "tab_inventory": "Ekwipunek",
        "tab_settings": "Ustawienia", "farming_now": "Teraz farmione", "drop_unknown": "Drop nieokreślony",
        "pause": "Wstrzymaj", "resume": "Wznów", "hide_tray": "Zwiń do zasobnika",
        "quit_miner": "Wyłącz miner", "log": "Dziennik",
        "channels_hint": "Podwójne kliknięcie przełącza kanał",
        "col_channel": "Kanał", "col_game": "Gra", "col_viewers": "Widzowie", "col_status": "Stan",
        "priority": "Priorytet gier", "watch_games": "Obserwuj gry",
        "watch_hint": "Powiem, gdy pojawi się nowa kampania tej gry. Farm nie przerywa się.",
        "language": "Język", "language_auto": "Auto (język Windows)",
        "language_restart": "Język zadziała po restarcie programu.",
        "dark_theme": "Ciemny motyw", "start_tray": "Start w zasobniku",
        "tray_notes": "Powiadomienia zasobnika", "farm_cosmetics": "Farmić kosmetyki",
        "verify_drops": "Sprawdzać dropy kanału", "drop_images": "Obrazki nagród",
        "check_updates": "Sprawdzać aktualizacje", "autostart": "Uruchamiać z Windows",
        "connect_bot": "Podłącz bota…", "telegram": "Telegram", "export": "Eksport",
        "farm_going": "● Działa", "farm_stalled": "● Stoi", "farm_uncounted": "● Nie liczy",
        "farm_paused": "● Pauza", "farm_idle": "● Czeka",
        "tray_show": "Pokaż okno", "tray_hide": "Ukryj okno", "tray_quit": "Wyjdź",
        "tiles_off": "Obrazki wyłączone — włącz w Ustawieniach",
    },
    "tr": {
        "tab_mining": "Madencilik", "tab_channels": "Kanallar", "tab_inventory": "Envanter",
        "tab_settings": "Ayarlar", "farming_now": "Şu an farm", "drop_unknown": "Drop seçilmedi",
        "pause": "Duraklat", "resume": "Sürdür", "hide_tray": "Tepsiye küçült",
        "quit_miner": "Mineri durdur", "log": "Günlük",
        "channels_hint": "Çift tık kanal değiştirir",
        "col_channel": "Kanal", "col_game": "Oyun", "col_viewers": "İzleyici", "col_status": "Durum",
        "priority": "Oyun önceliği", "watch_games": "Oyunları izle",
        "watch_hint": "Bu oyun için yeni kampanya çıkınca söylerim. Farm kesilmez.",
        "language": "Dil", "language_auto": "Otomatik (Windows dili)",
        "language_restart": "Dil program yeniden açılınca uygulanır.",
        "dark_theme": "Koyu tema", "start_tray": "Tepsiden başlat",
        "tray_notes": "Tepsi bildirimleri", "farm_cosmetics": "Kozmetik farmla",
        "verify_drops": "Kanal droplarını doğrula", "drop_images": "Ödül görselleri",
        "check_updates": "Güncellemeleri denetle", "autostart": "Windows ile başlat",
        "connect_bot": "Botu bağla…", "telegram": "Telegram", "export": "Dışa aktar",
        "farm_going": "● Çalışıyor", "farm_stalled": "● Durdu", "farm_uncounted": "● Sayılmıyor",
        "farm_paused": "● Duraklatıldı", "farm_idle": "● Bekliyor",
        "tray_show": "Pencereyi göster", "tray_hide": "Pencereyi gizle", "tray_quit": "Çık",
        "tiles_off": "Görseller kapalı — Ayarlar’dan açın",
    },
    "zh": {
        "tab_mining": "挖取", "tab_channels": "频道", "tab_inventory": "库存",
        "tab_settings": "设置", "farming_now": "正在挖取", "drop_unknown": "未选定掉落",
        "pause": "暂停", "resume": "继续", "hide_tray": "最小化到托盘",
        "quit_miner": "停止挖取", "log": "日志",
        "channels_hint": "双击切换频道",
        "col_channel": "频道", "col_game": "游戏", "col_viewers": "观众", "col_status": "状态",
        "priority": "游戏优先级", "watch_games": "关注游戏",
        "watch_hint": "该游戏出现新活动时通知你。挖取不会中断。",
        "language": "语言", "language_auto": "自动（Windows 语言）",
        "language_restart": "语言将在重启程序后生效。",
        "dark_theme": "深色主题", "start_tray": "启动到托盘",
        "tray_notes": "托盘通知", "farm_cosmetics": "挖取外观",
        "verify_drops": "校验频道掉落", "drop_images": "奖励图片",
        "check_updates": "检查更新", "autostart": "开机启动",
        "connect_bot": "连接机器人…", "telegram": "Telegram", "export": "导出",
        "farm_going": "● 进行中", "farm_stalled": "● 停滞", "farm_uncounted": "● 未计入",
        "farm_paused": "● 暂停", "farm_idle": "● 等待",
        "tray_show": "显示窗口", "tray_hide": "隐藏窗口", "tray_quit": "退出",
        "tiles_off": "图片已关闭 — 请在设置中打开",
    },
}

# неповні мови добирають українську, не китайську
for _code in LANGS:
    if _code == "uk":
        continue
    merged = dict(_UK)
    merged.update(CATALOG[_code])
    CATALOG[_code] = merged
