"""Gera dist\\onVLC\\onVLC.exe, cria atalhos na Área de Trabalho e no Menu Iniciar
e registra o app como player de vídeo do Windows.

Uso: python tools\\build.py
"""
import ctypes
import os
import shutil
import subprocess
import sys
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tvfolder import VIDEO_EXTS  # noqa: E402  (mesma lista do main.py, sem carregar o VLC)

NAME = "onVLC"
PROG_ID = "PlayerLucas.Video"  # mantido: é por ele que o Windows lembra a escolha de app padrão
CAPS = rf"Software\PlayerLucas\{NAME}\Capabilities"
DESKTOP = Path(os.environ["USERPROFILE"]) / "Desktop"
START_MENU = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def _set(path, name, value):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)


def _delete_tree(path):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    _delete_tree(path + "\\" + winreg.EnumKey(k, 0))
                except OSError:
                    break
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except FileNotFoundError:
        pass


def remove_old_name():
    """Tira o que ficou do tempo em que o app se chamava "VLC"."""
    for lnk in (DESKTOP / "VLC.lnk", START_MENU / "VLC.lnk"):
        lnk.unlink(missing_ok=True)
    _delete_tree(r"Software\Classes\Applications\VLC.exe")
    _delete_tree(r"Software\PlayerLucas\VLC")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\RegisteredApplications", 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, "VLC Lucas")
    except FileNotFoundError:
        pass
    shutil.rmtree(ROOT / "dist" / "VLC", ignore_errors=True)


def register_app(exe):
    """Aparece em "Abrir com" e em Configurações → Aplicativos padrão. Só o usuário escolhe o padrão."""
    cmd = f'"{exe}" "%1"'
    _set(rf"Software\Classes\{PROG_ID}", "", "Vídeo")
    _set(rf"Software\Classes\{PROG_ID}\DefaultIcon", "", f"{exe},0")
    _set(rf"Software\Classes\{PROG_ID}\shell\open\command", "", cmd)
    _set(rf"Software\Classes\Applications\{NAME}.exe", "FriendlyAppName", NAME)
    _set(rf"Software\Classes\Applications\{NAME}.exe\shell\open\command", "", cmd)
    _set(CAPS, "ApplicationName", NAME)
    _set(CAPS, "ApplicationDescription", "Player de vídeo do Lucas, com motor do VLC")
    _set(CAPS, "ApplicationIcon", f"{exe},0")
    for ext in sorted(VIDEO_EXTS):
        _set(CAPS + r"\FileAssociations", ext, PROG_ID)
        _set(rf"Software\Classes\{ext}\OpenWithProgids", PROG_ID, "")
        _set(rf"Software\Classes\Applications\{NAME}.exe\SupportedTypes", ext, "")
    _set(r"Software\RegisteredApplications", NAME, CAPS)
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
    print("registrado como app de vídeo:", NAME)


def build_library():
    """A biblioteca da TV (pasta server\\, em Go): um .exe só, que vai junto do onVLC.exe."""
    go = shutil.which("go") or str(Path(r"C:\Program Files") / "Go" / "bin" / "go.exe")
    if not Path(go).exists():
        sys.exit("Go não encontrado (winget install GoLang.Go)")
    shutil.copy2(ROOT / "ui" / "icon.ico", ROOT / "server" / "icon.ico")
    subprocess.run([go, "build", "-trimpath", "-ldflags", "-H windowsgui -s -w", "-o", "onvlc-biblioteca.exe", "."],
                   cwd=ROOT / "server", check=True)
    return ROOT / "server" / "onvlc-biblioteca.exe"


# o que a interface usa do Qt: o resto (3D, gráficos, mapas, multimídia, PDF, navegador…) entra junto no pacote
# porque o PyInstaller copia todos os módulos de tela do Qt; depois do build ele sai
QT_KEEP = {"Qt6Core", "Qt6Gui", "Qt6Widgets", "Qt6Network", "Qt6OpenGL", "Qt6Qml", "Qt6QmlCore", "Qt6QmlMeta",
           "Qt6QmlModels", "Qt6QmlWorkerScript", "Qt6QmlNetwork", "Qt6Quick", "Qt6QuickShapes", "Qt6Svg", "Qt6Concurrent",
           "Qt6QuickEffects", "Qt6ShaderTools"}
QML_KEEP = {"QtQml", "QtQuick"}
QTQUICK_KEEP = {"Shapes", "Window", "Effects"}  # import QtQuick + Shapes (ícones) + Effects (cantos dos cards)
PLUGINS_DROP = {"qmltooling", "networkinformation", "generic", "platforminputcontexts"}


def trim(internal):
    """Tira do pacote as partes do Qt que o onVLC não usa (de ~438 MB para ~100 MB)."""
    qt = internal / "PySide6"
    before = sum(f.stat().st_size for f in qt.rglob("*") if f.is_file())
    for f in qt.glob("Qt6*.dll"):
        if f.stem not in QT_KEEP:
            f.unlink()
    for name in ("opengl32sw.dll",):  # OpenGL por software: só para PC sem placa de vídeo
        (qt / name).unlink(missing_ok=True)
    shutil.rmtree(qt / "resources", ignore_errors=True)  # dados do navegador embutido (WebEngine)
    for d in (qt / "qml").iterdir():
        if d.is_dir() and d.name not in QML_KEEP:
            shutil.rmtree(d)
    for d in (qt / "qml" / "QtQuick").iterdir():
        if d.is_dir() and d.name not in QTQUICK_KEEP:
            shutil.rmtree(d)
    for d in (qt / "plugins").iterdir():
        if d.name in PLUGINS_DROP:
            shutil.rmtree(d)
    for f in (qt / "translations").glob("*"):
        if f.is_dir() or "pt_BR" not in f.name:
            shutil.rmtree(f) if f.is_dir() else f.unlink()
    after = sum(f.stat().st_size for f in qt.rglob("*") if f.is_file())
    print(f"Qt: {before / 2**20:.0f} MB -> {after / 2**20:.0f} MB")


def start_with_windows(lib_exe):
    """Só a biblioteca (leve, sem janela) abre quando o Windows liga: a TV acha o PC sempre, mesmo sem o player aberto."""
    _set(r"Software\Microsoft\Windows\CurrentVersion\Run", "onVLC biblioteca", f'"{lib_exe}"')
    print("biblioteca abre com o Windows:", lib_exe)


def main():
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)
    lib_exe = build_library()
    subprocess.run([
        sys.executable, "-m", "PyInstaller", str(ROOT / "main.py"),
        "--name", NAME, "--noconfirm", "--windowed", "--onedir",
        "--icon", str(ROOT / "ui" / "icon.ico"),
        "--add-data", f"{ROOT / 'ui'}{os.pathsep}ui",
        "--add-data", f"{ROOT / 'tvui'}{os.pathsep}tvui",
        "--exclude-module", "PySide6.QtWebEngineCore", "--exclude-module", "PySide6.QtWebEngineQuick",
        "--exclude-module", "setuptools",
        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"), "--specpath", str(ROOT / "build"),
    ], check=True)

    exe = ROOT / "dist" / NAME / f"{NAME}.exe"
    trim(exe.parent / "_internal")
    shutil.copy2(lib_exe, exe.with_name(lib_exe.name))
    for folder in (DESKTOP, START_MENU):
        lnk = folder / f"{NAME}.lnk"
        ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
              f"$s.TargetPath='{exe}';$s.WorkingDirectory='{exe.parent}';$s.IconLocation='{exe},0';$s.Save()")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
        print("atalho:", lnk)
    register_app(exe)
    start_with_windows(exe.with_name(lib_exe.name))
    remove_old_name()
    print("pronto:", exe)


if __name__ == "__main__":
    main()
