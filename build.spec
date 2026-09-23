# PyInstaller: один self-contained бінарник (Windows .exe / Linux / macOS).
#
# Нічого зовнішнього не пакуємо: браузер беремо системний (Edge/Chrome/Chromium),
# а Node.js не потрібен, бо замість Playwright використовується власний
# CDP-клієнт. Тому збірка виходить компактною — усе, що треба, це Python-рантайм
# і Tcl/Tk.
#
# Збірка:  python -m PyInstaller build.spec --noconfirm
#
# Саме через модуль, а не через pyinstaller.exe. Обгортки консольних скриптів
# у Windows містять абсолютний шлях до інтерпретатора, вшитий при
# встановленні, — і якщо venv колись копіювали з іншої теки, вони мовчки
# запускають чужий Python із чужими бібліотеками. `python -m` бере інтерпретатор
# за розташуванням і такої підміни не допускає.
import sys

from PyInstaller.utils.hooks import collect_data_files

# CustomTkinter тримає теми й описи віджетів у JSON поруч із пакетом і читає їх
# під час запуску. PyInstaller бачить лише імпорти, тому сам їх не візьме:
# локально все працює, а зібраний бінарник падає на старті, не знайшовши
# themes/blue.json. Рівно той клас дефекту, який ловиться тільки живим запуском
# збірки, — тому крок «перевірити бінарник» у релізному CI обов'язковий.
CTK_DATA = collect_data_files("customtkinter")

_HIDDEN = [
    # підтягуються динамічно, тому PyInstaller їх сам не бачить
    "gui.app",
    "gui.tray",
    "notify.telegram",
    "auth.flow",
    "auth.cdp",
    "auth.browser",
    "auth.device",
]
if sys.platform == "win32":
    _HIDDEN.append("pystray._win32")
elif sys.platform == "darwin":
    _HIDDEN.append("pystray._darwin")
else:
    # Linux: який бекенд є — залежить від середовища користувача
    _HIDDEN.extend(["pystray._appindicator", "pystray._gtk", "pystray._xorg"])

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[("core/locales", "core/locales"), *CTK_DATA],
    hiddenimports=_HIDDEN,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # важкі бібліотеки, які тягне Pillow, а нам вони ні до чого
        "numpy", "scipy", "pandas", "matplotlib",
        "PIL.ImageQt", "PyQt5", "PySide2", "tkinter.test",
        "test", "unittest", "pydoc_data",
        # AVIF: найбільший файл у всій збірці (_avif.pyd, 7,5 МБ), а Twitch
        # віддає нагороди в PNG/JPEG. Це окремий плагін формату — його
        # відсутність не ламає імпорт, лише знімає вміння читати AVIF.
        "PIL.AvifImagePlugin",
        # ⚠️ `PIL.ImageFont` і `PIL.ImageCms` тут БУЛИ — і це коштувало
        # зламаної збірки. Греп показував, що `draw.text` не викликається
        # ніде, і виглядало, ніби шрифти не потрібні. Але `ImageDraw`
        # імпортує `ImageFont` САМ, на рівні модуля
        # (`from . import Image, ImageColor, ImageFont, ImageText`), тож
        # `from PIL import ImageDraw` у gui/icon.py падав з
        # «cannot import name 'ImageFont'» — і не в тестах, а в зібраному
        # бінарнику при живому запуску. Тодішня перевірка через `--version`
        # цього не ловила: вона не доходить до gui/tray.py.
        # Не виключати. Два мегабайти не варті зламаного релізу.
    ],
    noarchive=False,
)

# Часові пояси й локалізацію Tcl програма не використовує: час рахується в
# Python через `datetime`, а переклад свій — `core/i18n.py`. Розмір тут
# дрібний, важлива кількість: у onefile усі ці файли розпаковуються в
# тимчасову теку на кожному запуску, а при /reboot — удруге.
# ⚠️ Роздільник нормалізуємо: у `a.datas` шляхи можуть бути по-віндовому
# (`_tcl_data\tzdata\...`), і фільтр із прямими слешами мовчки не збігається
# ні з чим.
JUNK = ("_tcl_data/tzdata", "_tcl_data/msgs", "_tk_data/msgs")
a.datas = [
    entry for entry in a.datas
    if not entry[0].replace("\", "/").startswith(JUNK)
]

pyz = PYZ(a.pure)

_exe_kwargs = dict(
    name="TwitchDropFarm",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # ⚠️ Було `upx=True` — і це була неправда. UPX на машині немає, а
    # PyInstaller у такому разі **мовчки** пропускає стиснення. Ставити UPX
    # не хочемо свідомо: пакувальники регулярно дають хибні спрацювання
    # антивірусів, а в цього проєкту вже є історія з ESET.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    # False — програма має GUI; консольне вікно поверх нього не потрібне.
    # Режим --console пише в консоль батьківського процесу, якщо його
    # запустили з неї.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
if sys.platform == "win32":
    # Той самий значок, що у вікна й трея. Манифест DPI — лише Windows.
    _exe_kwargs["icon"] = "icon.ico"
    _exe_kwargs["manifest"] = "tools/app.manifest"

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    **_exe_kwargs,
)
