"""O onVLC no Windows: "Abrir com" / app de vídeo e a tarefa que mantém a biblioteca aberta.

Usado pelo tools\\build.py (desenvolvimento) e pelo instalador, que roda `onVLC.exe --registrar` no fim da instalação
e `onVLC.exe --desregistrar` antes de desinstalar.
"""
import ctypes
import os
import subprocess
import tempfile
import time
import winreg
from pathlib import Path
from xml.sax.saxutils import escape

SYS32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
SCHTASKS = str(SYS32 / "schtasks.exe")  # caminhos completos: um .exe com o mesmo nome na pasta atual não vale
TASKKILL = str(SYS32 / "taskkill.exe")

NAME = "onVLC"
PROG_ID = "PlayerLucas.Video"  # mantido: é por ele que o Windows lembra a escolha de app padrão
CAPS = rf"Software\PlayerLucas\{NAME}\Capabilities"
TASK = "onVLC biblioteca"
LIB_EXE = "onvlc-biblioteca.exe"
VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".webm", ".m4v", ".wmv", ".flv", ".ts", ".m2ts", ".mpg", ".mpeg"}
NO_WINDOW = 0x08000000


def _set(path, name, value):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)


def _delete_value(path, name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)
    except FileNotFoundError:
        pass


def delete_tree(path):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_ALL_ACCESS) as k:
            while True:
                try:
                    delete_tree(path + "\\" + winreg.EnumKey(k, 0))
                except OSError:
                    break
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except FileNotFoundError:
        pass


def register_app(exe):
    """Aparece em "Abrir com" e em Configurações → Aplicativos padrão. Só o usuário escolhe o padrão."""
    cmd = f'"{exe}" "%1"'
    _set(rf"Software\Classes\{PROG_ID}", "", "Vídeo")
    _set(rf"Software\Classes\{PROG_ID}\DefaultIcon", "", f"{exe},0")
    _set(rf"Software\Classes\{PROG_ID}\shell\open\command", "", cmd)
    _set(rf"Software\Classes\Applications\{NAME}.exe", "FriendlyAppName", NAME)
    _set(rf"Software\Classes\Applications\{NAME}.exe\shell\open\command", "", cmd)
    _set(CAPS, "ApplicationName", NAME)
    _set(CAPS, "ApplicationDescription", "Player de vídeo com motor do VLC, que manda os filmes para a TV")
    _set(CAPS, "ApplicationIcon", f"{exe},0")
    for ext in sorted(VIDEO_EXTS):
        _set(CAPS + r"\FileAssociations", ext, PROG_ID)
        _set(rf"Software\Classes\{ext}\OpenWithProgids", PROG_ID, "")
        _set(rf"Software\Classes\Applications\{NAME}.exe\SupportedTypes", ext, "")
    _set(r"Software\RegisteredApplications", NAME, CAPS)
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED


def start_with_windows(lib_exe):
    """Mantém a biblioteca aberta enquanto o PC está ligado (a TV acha o PC sempre): uma tarefa do Windows abre ela
    no login, ao voltar da suspensão e a cada minuto se ela tiver fechado. Enquanto ela está aberta, a tarefa
    não abre outra (e, se alguém abrir, a segunda vê a porta ocupada e sai na hora). Devolve True se criou a tarefa."""
    lib_exe = Path(lib_exe)
    user = os.environ["USERDOMAIN"] + "\\" + os.environ["USERNAME"]
    resume = ("&lt;QueryList&gt;&lt;Query Id=\"0\" Path=\"System\"&gt;&lt;Select Path=\"System\"&gt;"
              "*[System[Provider[@Name='Microsoft-Windows-Power-Troubleshooter'] and EventID=1]]"
              "&lt;/Select&gt;&lt;/Query&gt;&lt;/QueryList&gt;")
    start = time.strftime("%Y-%m-%dT%H:%M:%S")  # a conferência de 1 em 1 min começa já (e continua para sempre)
    xml = f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Mantém a biblioteca do onVLC aberta para a TV achar o PC</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId></LogonTrigger>
    <TimeTrigger>
      <StartBoundary>{start}</StartBoundary><Enabled>true</Enabled>
      <Repetition><Interval>PT1M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
    </TimeTrigger>
    <EventTrigger><Enabled>true</Enabled><Subscription>{resume}</Subscription></EventTrigger>
  </Triggers>
  <Principals><Principal id="Author"><UserId>{escape(user)}</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit><StartWhenAvailable>true</StartWhenAvailable><Hidden>true</Hidden>
    <IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>
  </Settings>
  <Actions Context="Author"><Exec><Command>{escape(str(lib_exe))}</Command><WorkingDirectory>{escape(str(lib_exe.parent))}</WorkingDirectory></Exec></Actions>
</Task>
"""
    fd, tmp = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        Path(tmp).write_text(xml, encoding="utf-16")
        r = subprocess.run([SCHTASKS, "/Create", "/TN", TASK, "/XML", tmp, "/F"], capture_output=True, text=True,
                           creationflags=NO_WINDOW)
    finally:
        os.unlink(tmp)
    run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    if r.returncode == 0:
        _delete_value(run_key, TASK)  # a entrada antiga de "abrir com o Windows" sai: a tarefa faz o mesmo e mais
        return True
    _set(run_key, TASK, f'"{lib_exe}"')  # sem permissão para a tarefa: fica pelo menos abrindo no login
    return False


def run_task():
    subprocess.run([SCHTASKS, "/Run", "/TN", TASK], capture_output=True, creationflags=NO_WINDOW)


def resume_task():
    """Liga a tarefa de novo (depois do stop_task) e abre a biblioteca."""
    subprocess.run([SCHTASKS, "/Change", "/TN", TASK, "/ENABLE"], capture_output=True, creationflags=NO_WINDOW)
    run_task()


def stop_task():
    """Pausa a tarefa e fecha a biblioteca (antes de trocar ou apagar os arquivos)."""
    subprocess.run([SCHTASKS, "/Change", "/TN", TASK, "/DISABLE"], capture_output=True, creationflags=NO_WINDOW)
    subprocess.run([TASKKILL, "/IM", LIB_EXE, "/F"], capture_output=True, creationflags=NO_WINDOW)


def register_all(exe):
    """Tudo o que o instalador precisa depois de copiar os arquivos: "Abrir com", tarefa e a biblioteca rodando."""
    exe = Path(exe)
    register_app(exe)
    start_with_windows(exe.with_name(LIB_EXE))
    run_task()


def wipe_data():
    r""""Apagar meus dados" do desinstalador: o que o onVLC guardou neste PC. Nunca toca nos filmes (Vídeos\onVLC).
    Apaga %LOCALAPPDATA%\onVLC (posições da biblioteca, capas, miniaturas, TV salva, pareamento com a TV, chave da
    biblioteca, registros), as senhas no Gerenciador de Credenciais e as configurações do player no registro."""
    import shutil
    data = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "onVLC"
    if data.name == "onVLC" and data.is_dir():
        shutil.rmtree(data, ignore_errors=True)
    try:
        import keyring
        for name in ("tmdb_token", "password", "api_key"):
            try:
                keyring.delete_password("onVLC", name)
            except Exception:
                pass
    except ImportError:
        pass
    delete_tree(r"Software\PlayerLucas\Player")  # volume, recentes, posições do player


def unregister():
    """O contrário do register_all (o desinstalador chama). Filmes, posições e a pasta Vídeos\\onVLC ficam."""
    subprocess.run([SCHTASKS, "/Delete", "/TN", TASK, "/F"], capture_output=True, creationflags=NO_WINDOW)
    subprocess.run([TASKKILL, "/IM", LIB_EXE, "/F"], capture_output=True, creationflags=NO_WINDOW)
    _delete_value(r"Software\Microsoft\Windows\CurrentVersion\Run", TASK)
    _delete_value(r"Software\RegisteredApplications", NAME)
    for ext in VIDEO_EXTS:
        _delete_value(rf"Software\Classes\{ext}\OpenWithProgids", PROG_ID)
    delete_tree(rf"Software\Classes\{PROG_ID}")
    delete_tree(rf"Software\Classes\Applications\{NAME}.exe")
    delete_tree(rf"Software\PlayerLucas\{NAME}")
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
