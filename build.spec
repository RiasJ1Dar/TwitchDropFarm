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
a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[],
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
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="TwitchDropFarm",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    # False — програма має GUI; консольне вікно поверх нього не потрібне.
    # Режим --console пише в консоль батьківського процесу, якщо його запустили з неї.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
