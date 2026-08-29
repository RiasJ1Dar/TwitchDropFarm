# PyInstaller: один self-contained .exe.
#
# Нічого зовнішнього не пакуємо: браузер беремо системний (Edge/Chrome), а Node.js
# не потрібен, бо замість Playwright використовується власний CDP-клієнт. Тому
# збірка виходить компактною — усе, що треба, це Python-рантайм і Tcl/Tk.
#
# Збірка:  env\Scripts\python.exe -m PyInstaller build.spec --noconfirm
#
# Саме через модуль, а не через env\Scripts\pyinstaller.exe. Обгортки консольних
# скриптів у Windows містять абсолютний шлях до інтерпретатора, вшитий при
# встановленні, — і якщо venv колись копіювали з іншої теки, вони мовчки
# запускають чужий Python із чужими бібліотеками. `python -m` бере інтерпретатор
# за розташуванням і такої підміни не допускає.
from PyInstaller.utils.hooks import collect_data_files

# CustomTkinter тримає теми й описи віджетів у JSON поруч із пакетом і читає їх
# під час запуску. PyInstaller бачить лише імпорти, тому сам їх не візьме:
# локально все працює, а зібраний .exe падає на старті, не знайшовши
# themes/blue.json. Рівно той клас дефекту, який ловиться тільки живим запуском
# збірки, — тому крок «перевірити .exe» у релізному CI обов'язковий.
CTK_DATA = collect_data_files("customtkinter")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[("core/locales", "core/locales"), *CTK_DATA],
    hiddenimports=[
        # підтягуються динамічно, тому PyInstaller їх сам не бачить
        "gui.app",
        "gui.tray",
        "notify.telegram",
        "auth.flow",
        "auth.cdp",
        "auth.browser",
        "auth.device",
        "pystray._win32",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # важкі бібліотеки, які тягне Pillow, а нам вони ні до чого
        "numpy", "scipy", "pandas", "matplotlib",
        "PIL.ImageQt", "PyQt5", "PySide2", "tkinter.test",
        "test", "unittest", "pydoc_data",
        # Плагіни Pillow, яких проєкт не торкається. З PIL використовуються
        # рівно три речі: `Image`, `ImageTk` і `ImageDraw` (значок у
        # gui/icon.py). Нагороди Twitch віддає в PNG/JPEG.
        #   _avif.pyd       7,5 МБ — найбільший файл у всій збірці
        #   _imagingft.pyd  2,1 МБ — шрифти; `draw.text` не викликається ніде
        #   _imagingcms.pyd 0,3 МБ — керування кольором
        "PIL.AvifImagePlugin", "PIL.ImageFont", "PIL.ImageCms",
    ],
    noarchive=False,
)

# Часові пояси й локалізацію Tcl програма не використовує: час рахується в
# Python через `datetime`, а переклад свій — `core/i18n.py`. Розмір тут
# дрібний, важлива кількість: у onefile усі ці файли розпаковуються в
# %TEMP%\_MEIxxxx на кожному запуску, а при /reboot — удруге.
# ⚠️ Роздільник нормалізуємо: у `a.datas` шляхи записані по-віндовому
# (`_tcl_data\tzdata\...`), і фільтр із прямими слешами мовчки не збігається
# ні з чим. Перша редакція цієї правки саме так і «спрацювала» — розмір упав
# від виключень Pillow, а часові пояси лишились усі до одного.
JUNK = ("_tcl_data/tzdata", "_tcl_data/msgs", "_tk_data/msgs")
a.datas = [
    entry for entry in a.datas
    if not entry[0].replace("\\", "/").startswith(JUNK)
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="TwitchDropFarm",
    # Той самий значок, що у вікна й трея. Файл робить tools/make_icon.py із
    # gui/icon.py — тому вони не можуть розійтись. PyInstaller читає його як
    # готовий файл на етапі збірки й запікає всередину, тож правило «один
    # самодостатній .exe» не порушується.
    icon="icon.ico",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # ⚠️ Було `upx=True` — і це була неправда. UPX на машині немає, а
    # PyInstaller у такому разі **мовчки** пропускає стиснення: 27,6 МБ
    # виходили без нього. Рядок вводив в оману, ніби по розміру вже все
    # зроблено. Ставити UPX не хочемо свідомо: пакувальники регулярно дають
    # хибні спрацювання антивірусів, а в цього проєкту вже є історія з ESET.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # False — програма має GUI; консольне вікно поверх нього не потрібне.
    # Режим --console пише в консоль батьківського процесу, якщо його запустили з неї.
    console=False,
    disable_windowed_traceback=False,
    manifest="tools/app.manifest",
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
