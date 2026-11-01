"""Gera dist\\onVLC\\ (onVLC.exe + biblioteca + VLC + ffmpeg + adb + app da TV), cria atalhos na Área de Trabalho e
no Menu Iniciar e registra o app como player de vídeo do Windows.

Tudo o que o onVLC usa vai dentro da pasta: quem instala (tools\\build_installer.py) não precisa ter nada no PC.

Uso: python tools\\build.py
"""
import hashlib
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import winsetup  # noqa: E402

NAME = winsetup.NAME
DESKTOP = Path(os.environ["USERPROFILE"]) / "Desktop"
START_MENU = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
VLC_SRC = Path(r"C:\Program Files\VideoLAN\VLC")  # o motor sai do VLC instalado neste PC
# o que o player não usa do VLC: a interface própria dele, controles remotos, gravar/transmitir, visualizações
VLC_PLUGINS_DROP = {"gui", "control", "stream_out", "access_output", "mux", "visualization", "video_splitter"}
FFMPEG_VER = "9.0.2"
FFMPEG_ZIP = f"https://github.com/GyanD/codexffmpeg/releases/download/{FFMPEG_VER}/ffmpeg-{FFMPEG_VER}-essentials_build.zip"
FFMPEG_SHA256 = "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"


def remove_old_name():
    """Tira o que ficou do tempo em que o app se chamava "VLC"."""
    import winreg
    for lnk in (DESKTOP / "VLC.lnk", START_MENU / "VLC.lnk"):
        lnk.unlink(missing_ok=True)
    winsetup.delete_tree(r"Software\Classes\Applications\VLC.exe")
    winsetup.delete_tree(r"Software\PlayerLucas\VLC")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\RegisteredApplications", 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, "VLC Lucas")
    except FileNotFoundError:
        pass
    shutil.rmtree(ROOT / "dist" / "VLC", ignore_errors=True)


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


def copy_vlc(dest):
    """O motor do VLC (libvlc + plugins) vai dentro do onVLC: quem instala não precisa ter o VLC."""
    if not (VLC_SRC / "libvlc.dll").is_file():
        sys.exit(f"VLC não encontrado em {VLC_SRC} (winget install VideoLAN.VLC)")
    dest.mkdir(exist_ok=True)
    for name in ("libvlc.dll", "libvlccore.dll", "COPYING.txt"):
        shutil.copy2(VLC_SRC / name, dest / name)
    shutil.copytree(VLC_SRC / "plugins", dest / "plugins", dirs_exist_ok=True,
                    ignore=lambda d, names: [n for n in names if Path(d) == VLC_SRC / "plugins" and n in VLC_PLUGINS_DROP]
                    + [n for n in names if n == "plugins.dat"])
    # a lista de plugins pronta (sem ela o VLC lê todos a cada abertura): feita para esta pasta
    subprocess.run([str(VLC_SRC / "vlc-cache-gen.exe"), str(dest / "plugins")], check=True, capture_output=True)
    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    print(f"VLC: {dest} ({size / 2**20:.0f} MB)")


def copy_ffmpeg(dest):
    """ffmpeg/ffprobe (a biblioteca lê os vídeos e faz as miniaturas): versão "essentials", baixada uma vez e conferida."""
    cache = ROOT / "build" / "ffmpeg" / f"ffmpeg-{FFMPEG_VER}-essentials_build.zip"
    if not cache.is_file():
        cache.parent.mkdir(parents=True, exist_ok=True)
        print("› baixando o ffmpeg", FFMPEG_VER)
        urllib.request.urlretrieve(FFMPEG_ZIP, cache)
    if hashlib.sha256(cache.read_bytes()).hexdigest() != FFMPEG_SHA256:
        cache.unlink()
        sys.exit("o ffmpeg baixado não confere (SHA-256): rode de novo")
    dest.mkdir(exist_ok=True)
    with zipfile.ZipFile(cache) as z:
        for n in z.namelist():
            base = n.rsplit("/", 1)[-1]
            if base in ("ffmpeg.exe", "ffprobe.exe", "LICENSE"):
                (dest / base).write_bytes(z.read(n))
    print("ffmpeg:", dest)


def copy_adb(dest):
    """O adb (instalar e abrir o app na TV) vai dentro do onVLC: quem instala não precisa ter nada. Copiado do
    instalado neste PC (winget install Google.PlatformTools), para os dois serem da mesma versão e não brigarem."""
    import tvstream
    src = Path(tvstream.find_adb())
    if not src.is_file():
        sys.exit("adb não encontrado: winget install Google.PlatformTools")
    dest.mkdir(exist_ok=True)
    for name in ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll"):
        shutil.copy2(src.with_name(name), dest / name)
    print("adb:", dest)


def copy_tv_app(dest):
    """O app da TV (gerado por build_tvcache.py) vai dentro do onVLC: o assistente instala ele na TV pela rede."""
    apk, meta = ROOT / "dist" / "tvcache.apk", ROOT / "dist" / "tv.json"
    if not apk.is_file():
        print("aviso: sem dist\\tvcache.apk (rode tools\\build_tvcache.py); o assistente não vai conseguir instalar")
        return
    dest.mkdir(exist_ok=True)
    shutil.copy2(apk, dest / "onVLC-tv.apk")
    if meta.is_file():
        shutil.copy2(meta, dest / "tv.json")
    print("app da TV:", dest)


def stop_running():
    """Antes de trocar os arquivos de dist\\onVLC: pausa a tarefa (senão ela reabre a biblioteca no meio do build e o
    Windows não deixa apagar a pasta) e fecha a biblioteca e o player (autorizado pelo Lucas)."""
    winsetup.stop_task()
    subprocess.run(["taskkill", "/IM", f"{NAME}.exe", "/F"], capture_output=True)
    time.sleep(1)


def main():
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)
    lib_exe = build_library()
    stop_running()
    subprocess.run([
        sys.executable, "-m", "PyInstaller", str(ROOT / "main.py"),
        "--name", NAME, "--noconfirm", "--windowed", "--onedir",
        "--icon", str(ROOT / "ui" / "icon.ico"),
        "--add-data", f"{ROOT / 'ui'}{os.pathsep}ui",
        "--add-data", f"{ROOT / 'tvui'}{os.pathsep}tvui",
        "--exclude-module", "PySide6.QtWebEngineCore", "--exclude-module", "PySide6.QtWebEngineQuick",
        "--exclude-module", "setuptools",
        "--collect-submodules", "androidtvremote2", "--collect-submodules", "zeroconf",  # TV: achar e parear
        "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"), "--specpath", str(ROOT / "build"),
    ], check=True)

    exe = ROOT / "dist" / NAME / f"{NAME}.exe"
    trim(exe.parent / "_internal")
    shutil.copy2(lib_exe, exe.with_name(lib_exe.name))
    copy_vlc(exe.parent / "vlc")
    copy_ffmpeg(exe.parent / "ffmpeg")
    copy_adb(exe.parent / "adb")
    copy_tv_app(exe.parent / "tv")
    size = sum(f.stat().st_size for f in exe.parent.rglob("*") if f.is_file())
    print(f"pacote: {size / 2**20:.0f} MB")
    if "--sem-registrar" in sys.argv:  # só gerar (para o instalador), sem mexer nos atalhos e na tarefa deste PC
        winsetup.resume_task()  # a biblioteca deste PC volta como estava
        print("pronto:", exe)
        return
    # ícone dos atalhos fora de dist\ (que o build apaga): se o Windows olhar o atalho no meio do build, não guarda
    # um ícone em branco no cache
    ico = Path(os.environ["LOCALAPPDATA"]) / "onVLC" / f"{NAME}.ico"
    ico.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "ui" / "icon.ico", ico)
    for folder in (DESKTOP, START_MENU):
        lnk = folder / f"{NAME}.lnk"
        ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
              f"$s.TargetPath='{exe}';$s.WorkingDirectory='{exe.parent}';$s.IconLocation='{ico},0';$s.Save()")
        subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
        print("atalho:", lnk)
    subprocess.run(["ie4uinit.exe", "-show"], capture_output=True)  # Windows relê os ícones
    winsetup.register_all(exe)  # "Abrir com", tarefa da biblioteca (ligada de novo) e a biblioteca já rodando
    print("registrado como app de vídeo; biblioteca na tarefa do Windows:", exe.with_name(lib_exe.name))
    remove_old_name()
    print("pronto:", exe)


if __name__ == "__main__":
    main()
