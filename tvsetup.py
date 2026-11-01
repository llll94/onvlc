"""Assistente "Configurar a TV": acha a TV na rede, pareia como controle remoto, instala e abre o onVLC nela, testa, e
liga capas (TMDB) e legendas (OpenSubtitles). A tela é ui/Setup.qml; aqui fica o trabalho, sempre fora da linha da
tela (cada etapa numa thread; voltar ou trocar de etapa cancela o que estava rodando).

Etapas (iguais ao protótipo): 0 início · 1 achar a TV · 2 código · 3 instalar · 4 conectar · 5 teste · 6 capas ·
7 legendas · 8 pronto.
"""
import base64
import json
import logging
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, Property, QSettings, Signal, Slot

import library
import opensubs
import tvdevices
import tvstream

try:
    import keyring
except ImportError:
    keyring = None

log = logging.getLogger("onvlc.setup")
NO_WINDOW = 0x08000000
LIB = f"http://127.0.0.1:{library.PORT}"
ROOT = Path(__file__).resolve().parent
TMDB_TEST = "https://api.themoviedb.org/3/configuration"
OLD_PKG = "com.lucas.tvcache"  # nome antigo do app da TV (assinado com a chave que vazou): é tirado ao instalar o novo


def bundled_apk():
    """O app da TV que vai dentro do onVLC (pasta tv\\ ao lado do .exe) e a versão dele."""
    folder = Path(sys.executable).parent / "tv" if getattr(sys, "frozen", False) else ROOT / "dist"
    apk = next((p for p in (folder / "onVLC-tv.apk", folder / "tvcache.apk") if p.is_file()), None)
    try:
        code = int(json.loads((folder / "tv.json").read_text(encoding="utf-8"))["versionCode"])
    except (OSError, ValueError, KeyError):
        code = 0
    return apk, code


local_ip_to = tvstream.local_ip_to


def lib_json(path, data=None, timeout=4):
    """Pedido à biblioteca deste PC, com a chave (library.request)."""
    with library.request(path, data, timeout=timeout) as r:
        body = r.read()
        return json.loads(body) if body else {}


# PowerShell sempre pelo caminho completo: um powershell.exe na pasta atual (a do vídeo aberto) não vale
PS_EXE = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe")


def _ps(cmd, timeout=20):
    r = subprocess.run([PS_EXE, "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True, text=True,
                       timeout=timeout, creationflags=NO_WINDOW, encoding="utf-8", errors="replace")
    return r.stdout.strip()


def _ipv4(s):
    """Só um IPv4 em texto entra em comando: qualquer outra coisa vira erro antes."""
    if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", s or ""):
        raise ValueError("endereço inválido")
    return s


def wol_state(pc_ip):
    """A placa de rede por onde a TV chega neste PC: nome, índice, se acorda pela rede, se é Wi-Fi e o perfil
    da rede no Windows ("Public" = a TV não consegue chegar; o firewall só libera em rede Particular)."""
    out = _ps(f"""$a = Get-NetIPAddress -IPAddress '{_ipv4(pc_ip)}' -ErrorAction SilentlyContinue | Get-NetAdapter
if ($a) {{
  $p = Get-NetAdapterAdvancedProperty -Name $a.Name -RegistryKeyword '*WakeOnMagicPacket' -ErrorAction SilentlyContinue
  $w = Get-NetAdapterPowerManagement -Name $a.Name -ErrorAction SilentlyContinue
  $c = Get-NetConnectionProfile -InterfaceIndex $a.ifIndex -ErrorAction SilentlyContinue
  @{{ name = $a.Name; ifIndex = $a.ifIndex; magic = "$($p.RegistryValue)"; wake = "$($w.WakeOnMagicPacket)";
     wifi = ($a.NdisPhysicalMedium -eq 9); profile = "$($c.NetworkCategory)" }} | ConvertTo-Json -Compress
}}""")
    try:
        d = json.loads(out)
    except ValueError:
        return None
    d["ok"] = d.get("magic") in ("1", "") and d.get("wake") in ("Enabled", "")
    return d


def net_profile(pc_ip):
    """"Public", "Private", "DomainAuthenticated" ou "" (não deu para saber)."""
    w = wol_state(pc_ip)
    return (w or {}).get("profile", "")


def run_elevated(script, timeout=120):
    """Roda um script do PowerShell como administrador (o Windows pede confirmação). O script vai em base64
    (-EncodedCommand): nada do conteúdo é interpretado pela linha de comando, então não há como "escapar" dele."""
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    _ps(f"Start-Process -FilePath '{PS_EXE}' -Verb RunAs -Wait -WindowStyle Hidden "
        f"-ArgumentList '-NoProfile','-NonInteractive','-EncodedCommand','{enc}'", timeout=timeout)


def tv_app_version(box):
    """Versão do app na TV para quem está usando ela (0 = não tem). A TV pode ter mais de um usuário (a Mi Box tem
    o dono e um "new_user"): desinstalar pelas Configurações tira só do usuário atual, e o dumpsys continua mostrando
    a versão dos outros. Por isso confere antes se o app existe para o usuário atual."""
    if tvstream.APP_PKG not in box.shell(f"pm list packages --user current {tvstream.APP_PKG}"):
        return 0
    m = re.search(r"versionCode=(\d+)", box.shell(f"dumpsys package {tvstream.APP_PKG} | grep -m1 versionCode="))
    return int(m.group(1)) if m else 0


def tv_app_outdated(box):
    """A TV não tem o app, ou tem um mais velho que o que veio com este onVLC (o PC faz o papel da loja)."""
    apk, code = bundled_apk()
    have = tv_app_version(box)
    return not have or bool(apk and code > have)


def install_apk(box, progress=lambda p: None):
    """Manda o app para a TV (o progresso é o tamanho que já chegou lá) e instala. Devolve (deu certo, mensagem).
    Precisa do adb já conectado e autorizado."""
    apk, _ = bundled_apk()
    if not apk:
        return False, "O app da TV não veio junto com este onVLC."
    size = apk.stat().st_size
    tmp = "/data/local/tmp/onvlc-tv.apk"
    p = subprocess.Popen([box.adb, "-s", box.serial, "push", str(apk), tmp], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
    while p.poll() is None:
        m = re.search(r"\d+", box.shell(f"stat -c %s {tmp} 2>/dev/null", timeout=5))
        if m:
            progress(min(99, int(m.group(0)) * 100 // size))
        time.sleep(0.4)
    if p.returncode != 0:
        return False, "O app não chegou na TV. A rede caiu? Tente de novo."
    progress(100)
    # --user current: instala para quem está usando a TV (o exec-in não devolveria o "Success")
    out = box.shell(f"pm install -r --user current {tmp}", timeout=180)
    box.shell(f"rm -f {tmp}")
    log.info("instalar na TV: %s", out.strip()[:300])
    if "Success" in out:
        # o app antigo (outro nome de pacote, até nov/2026) sai: senão ficam dois onVLC na tela inicial
        if OLD_PKG in box.shell(f"pm list packages --user current {OLD_PKG}"):
            box.shell(f"pm uninstall --user current {OLD_PKG}")
        return True, "onVLC instalado na TV."
    if "UPDATE_INCOMPATIBLE" in out:
        return False, "Já existe outro onVLC na TV, assinado com outra chave. Desinstale o de lá e tente de novo."
    return False, "A TV não aceitou a instalação: " + out.strip()[-160:]


class Setup(QObject):
    changed = Signal()
    _upd = Signal(object)  # mudanças vindas das threads de trabalho

    def __init__(self, player):
        super().__init__()
        self.player = player
        self.settings = QSettings("PlayerLucas", "Player")
        self._st = {}
        self._gen = 0
        self.tv = None  # a TV escolhida (dict de tvdevices)
        self.remote = None
        self._upd.connect(self._apply)
        self.reset()

    # ---------- estado para a tela ----------
    def _get(self):
        return self._st

    st = Property("QVariantMap", _get, notify=changed)

    def _apply(self, msg):
        kind, kw = msg
        if kind == "go":
            self.go(kw)
            return
        for k, v in kw.items():
            if kind == "merge" and isinstance(v, dict) and isinstance(self._st.get(k), dict):
                self._st[k] = {**self._st[k], **v}
            else:
                self._st[k] = v
        self.changed.emit()

    def set(self, **kw):
        """Junta nos dicts que já existem (install={"p": 40} só muda o p)."""
        self._upd.emit(("merge", kw))

    def put(self, **kw):
        """Troca inteiro."""
        self._upd.emit(("put", kw))

    def _goto(self, step):
        self._upd.emit(("go", step))

    def _bg(self, fn):
        gen = self._gen

        def run():
            try:
                fn(lambda: gen == self._gen)
            except Exception as e:
                log.exception("assistente")
                if gen == self._gen:
                    self.set(error=f"Algo deu errado: {e}")
        threading.Thread(target=run, daemon=True).start()

    def reset(self):
        tv = tvdevices.default()
        has_tmdb = bool(self._key("tmdb_token"))
        self._st = {
            "step": 0, "error": "",
            "hasTv": bool(tv), "tvName": (tv or {}).get("name", ""), "pcName": socket.gethostname(),
            "autoOpen": not tv and self.settings.value("setupDismissed", "false") != "true",
            "tvs": [], "scanned": False, "picking": "",
            "code": {}, "install": {}, "conn": {}, "test": {},
            "tmdb": {"has": has_tmdb},
            "os": {"has": bool(self._key("password")), "available": bool(self._os_app_key()),
                   "user": self.settings.value("osUser", "")},
        }
        self.changed.emit()

    @staticmethod
    def _key(name):
        try:
            return (keyring.get_password("onVLC", name) if keyring else "") or ""
        except Exception:
            return ""

    def _os_app_key(self):
        """A chave de app do OpenSubtitles: a do onVLC (quando existir) ou a que o Lucas guardou."""
        return getattr(opensubs, "APP_KEY", "") or self._key("api_key")

    # ---------- navegação ----------
    @Slot(int)
    def go(self, step):
        """Abre uma etapa. Cancela o trabalho da etapa anterior e começa o desta, se ainda não foi feito."""
        self._gen += 1
        self._st.update(step=step, error="")
        self.changed.emit()
        if step == 1:
            self._bg(self._scan)
        elif step == 3 and self._st["install"].get("phase") != "done":
            self._bg(self._install)
        elif step == 4 and self._st["conn"].get("phase") != "ok":
            self._bg(self._connect)
        elif step in (6, 7):
            self.reset_keys()

    def reset_keys(self):
        self.set(tmdb={"has": bool(self._key("tmdb_token"))},
                 os={"has": bool(self._key("password")), "available": bool(self._os_app_key())})

    @Slot(str)
    def start(self, where):
        """Abre o assistente: "" = do começo; "tv", "capas", "legendas" = direto na etapa."""
        self.reset()
        self.go({"tv": 1, "capas": 6, "legendas": 7}.get(where, 0))

    @Slot()
    def dismiss(self):
        self.settings.setValue("setupDismissed", "true")
        self._gen += 1

    @Slot()
    def finish(self):
        self.settings.setValue("setupDone", "true")
        self._gen += 1

    @Slot()
    def openFolder(self):
        library.LIB_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(library.LIB_DIR)

    # ---------- 1. achar a TV ----------
    def _scan(self, alive):
        while alive():
            tvs = tvdevices.discover(3.5)
            if not alive():
                return
            saved = {t.get("bt") for t in tvdevices.load()}
            for d in tvs:
                d["saved"] = d["bt"] in saved
            self.set(tvs=tvs, scanned=True)
            time.sleep(2)

    @Slot(str)
    def pick(self, ip):
        d = next((t for t in self._st["tvs"] if t["ip"] == ip), None)
        if not d:
            return
        self.tv = {k: v for k, v in d.items() if k != "saved"}
        self._gen += 1  # para a busca
        self.put(picking=ip, code={}, install={}, conn={}, test={})
        self._bg(self._pair)

    def _pair(self, alive):
        from androidtvremote2 import CannotConnect
        self.remote = tvdevices.Remote(self.tv["ip"])
        try:
            self.remote.connect()
        except tvdevices.NeedsPairing:
            self.remote.start_pairing()
            self.set(picking="")
            self._goto(2)
            return
        except (CannotConnect, OSError, TimeoutError):
            self.set(picking="", error="Não consegui falar com essa TV. Ela está ligada (não só em espera)?")
            return
        self._paired()  # já conhecia este PC: pula o código
        self.set(picking="")
        self._goto(3)

    def _paired(self):
        info = self.remote.info()
        tv = tvdevices.save({**self.tv, "maker": info["maker"] or self.tv.get("maker", ""),
                             "model": info["model"] or self.tv.get("model", "")})
        self.tv = tv
        self.set(hasTv=True, tvName=tv.get("name", ""), code={"done": True})

    # ---------- 2. código ----------
    @Slot(str)
    def sendCode(self, code):
        self.set(code={"checking": True, "error": ""})

        def work(alive):
            from androidtvremote2 import InvalidAuth
            try:
                self.remote.finish_pairing(code)
            except InvalidAuth:
                self.remote.start_pairing()
                self.set(code={"checking": False, "error": "O código não confere. A TV mostrou um código novo."})
                return
            except Exception as e:
                log.warning("pareamento: %s", e)
                self.remote.start_pairing()
                self.set(code={"checking": False, "error": "A TV não respondeu. Ela mostrou um código novo: tente de novo."})
                return
            self._paired()
            self.set(code={"checking": False})
            self._goto(3)
        self._bg(work)

    # ---------- 3. instalar ----------
    def _box(self):
        return tvstream.TVBox(self.tv or tvdevices.default())

    def _install(self, alive):
        if not self.tv:
            self.tv = tvdevices.default()
        box = self._box()
        if box._port_open():
            self._authorize(box, alive)
        else:
            self.put(install={"phase": "dev_settings"})  # depuração desligada: o passo a passo começa

    def _ensure_remote(self):
        if self.remote is None or self.remote.ip != self.tv["ip"]:
            self.remote = tvdevices.Remote(self.tv["ip"])
            self.remote.connect()

    @Slot()
    def devSettings(self):
        def work(alive):
            self._ensure_remote()
            self.remote.key("SETTINGS")
            time.sleep(1.5)
            # no Google TV o botão abre um painel rápido com a engrenagem em foco: mais um OK abre as Configurações
            if "tv.settings" not in (self.remote.info()["app"] or ""):
                self.remote.key("DPAD_CENTER")
            self.set(install={"phase": "dev_build"})
        self._bg(work)

    @Slot()
    def devPress(self):
        def work(alive):
            self._ensure_remote()
            for i in range(1, 8):
                if not alive():
                    return
                self.remote.key("DPAD_CENTER")
                self.set(install={"press": i})
                time.sleep(0.4)
            time.sleep(1.2)
            self.set(install={"phase": "dev_usb", "press": 0})
            box = self._box()
            while alive() and not box._port_open():
                time.sleep(1)
            if alive():
                self._authorize(box, alive)
        self._bg(work)

    @Slot()
    def devSkip(self):
        """"Já liguei a depuração": volta a esperar a TV liberar."""
        self.set(install={"phase": "dev_usb"})

        def work(alive):
            box = self._box()
            while alive() and not box._port_open():
                time.sleep(1)
            if alive():
                self._authorize(box, alive)
        self._bg(work)

    def _authorize(self, box, alive):
        """adb connect: na primeira vez a TV pergunta se confia neste PC."""
        self.put(install={"phase": "allow"})
        t0 = last_ask = time.time()
        box.run("disconnect", box.serial, timeout=5)  # conexão nova = a TV pergunta agora (não numa conexão velha)
        while alive() and time.time() - t0 < 180:
            box.run("connect", box.serial, timeout=10)
            state = box.run("-s", box.serial, "get-state", timeout=5)
            if state == "device":
                self._push_apk(box, alive)
                return
            # a pergunta só aparece quando a conexão abre: sem resposta em 12 s (tela apagada, outra janela na
            # frente, a pessoa não viu), acorda a tela pelo controle virtual e abre a conexão de novo
            if time.time() - last_ask > 12:
                last_ask = time.time()
                try:
                    self._ensure_remote()
                    self.remote.key("WAKEUP")
                except Exception:
                    pass
                box.run("disconnect", box.serial, timeout=5)
                self.set(install={"asked": int(time.time() - t0)})
            time.sleep(2)
        if alive():
            self.put(install={"phase": "error", "msg": "A TV não deu permissão. Confira se apareceu a pergunta "
                                                       "\"Permitir depuração USB?\" e tente de novo."})

    def _push_apk(self, box, alive):
        if not tv_app_outdated(box):
            self.put(install={"phase": "done", "msg": "O onVLC já estava instalado na TV."})
            return
        if not bundled_apk()[0]:
            self.put(install={"phase": "error", "msg": "O app da TV não veio junto com este onVLC."})
            return
        self.put(install={"phase": "sending", "p": 0})
        ok, msg = install_apk(box, lambda p: self.set(install={"p": p}))
        self.put(install={"phase": "done" if ok else "error", "msg": msg})

    @Slot()
    def retryInstall(self):
        self.put(install={})
        self.go(3)

    # ---------- 4. conectar ----------
    def _connect(self, alive):
        self.put(conn={"phase": "opening"})
        box = self._box()
        pc_ip = local_ip_to(box.ip)
        if not box.connect():
            self.put(conn={"phase": "error", "msg": "A TV não respondeu. Ela está ligada?"})
            return
        t0 = time.time()
        # a PairActivity (só o adb abre) recebe onde o PC está e a chave da biblioteca; a TV guarda os dois
        box.shell(f"am start -n {tvstream.PAIR_ACTIVITY} {box.pc_args()}")
        if not box.app_in_front(timeout=10):
            self.put(conn={"phase": "error", "msg": "O onVLC não abriu na TV."})
            return
        # a confirmação de verdade: a TV pediu alguma coisa para a biblioteca deste PC depois de abrir
        while alive() and time.time() - t0 < 30:
            try:
                seen = lib_json("/api/clients").get(box.ip, 0)
            except urllib.error.HTTPError:
                seen = t0  # biblioteca antiga, sem essa lista: vale o app aberto
            except OSError:
                seen = 0
            if seen >= t0 - 1:
                self.put(conn={"phase": "ok", "pcIp": pc_ip})
                return
            time.sleep(1)
        if alive():
            msg = "O app abriu, mas não achou este PC. A biblioteca do onVLC está aberta? O firewall do Windows pode estar bloqueando."
            if net_profile(pc_ip) == "Public":
                msg = ("O app abriu, mas não achou este PC: esta rede está marcada como \"Pública\" no Windows, e aí o "
                       "firewall não deixa a TV falar com o PC. Em Configurações → Rede e Internet → propriedades da "
                       "rede, marque \"Rede particular\" e tente de novo.")
            self.put(conn={"phase": "error", "msg": msg})

    @Slot()
    def retryConnect(self):
        self.go(4)

    # ---------- 5. teste ----------
    ROWS = ["Conexão com o PC", "Velocidade da rede", "Vídeo", "Imagem", "Som", "Ligar o PC pela TV"]

    @Slot()
    def runTest(self):
        rows = [{"t": t, "s": "wait", "v": ""} for t in self.ROWS]
        self.put(test={"phase": "running", "rows": rows, "seen": None})

        def row(i, s, v=""):
            rows[i] = {**rows[i], "s": s, "v": v}
            self.set(test={"rows": list(rows)})

        def work(alive):
            box = self._box()
            pc_ip = local_ip_to(box.ip)
            # 1. a TV alcança o PC?
            row(0, "now")
            box.connect()
            out = box.shell(f"ping -c 3 -W 2 {_ipv4(pc_ip)}", timeout=15)
            m = re.search(r"= [\d.]+/([\d.]+)/", out)
            row(0, "ok" if m else "bad", f"{float(m.group(1)):.0f} ms de resposta" if m else "a TV não alcança o PC")
            # 2–5. o app da TV toca o vídeo de teste e conta o que conseguiu
            row(1, "now")
            t0 = time.time()
            box.shell(f"am start -n {tvstream.PAIR_ACTIVITY} {box.pc_args()} --ez selftest true")
            res = None
            video_at = None
            while alive() and time.time() - t0 < 180:
                try:
                    r = lib_json("/api/selftest/result")
                    if r.get("at", 0) >= t0 - 1:
                        if r.get("speed") is not None and rows[1]["s"] == "now":
                            sp = r["speed"]
                            row(1, "ok" if sp >= 40 else "warn" if sp >= 15 else "bad",
                                f"{sp:.0f} Mb/s · " + ("dá para 4K com folga" if sp >= 80 else "dá para 4K" if sp >= 40
                                                       else "bom para 1080p; 4K pode travar" if sp >= 15 else "pode travar"))
                            row(2, "now")
                            video_at = time.time()
                        if video_at and time.time() - video_at > 8 and not rows[2]["v"]:
                            row(2, "now", "na primeira vez a TV prepara o player: pode levar até 1 minuto")
                        if r.get("done"):
                            res = r
                            break
                except OSError:
                    pass
                time.sleep(1)
            if not alive():
                return
            if not res:
                for i in (1, 2, 3, 4):
                    if rows[i]["s"] in ("wait", "now"):
                        row(i, "warn", "o app da TV não respondeu ao teste")
            else:
                stalls, played = res.get("stalls", 0), res.get("played", 0)
                if played < 1:
                    row(2, "bad", "o vídeo não começou na TV")
                elif played < 8:
                    row(2, "warn", f"tocou só {played:.0f} s")
                else:
                    row(2, "ok" if not stalls else "warn",
                        "tocou 10 s sem travar" if not stalls else f"tocou, mas travou {stalls} vez(es)")
                yes = lambda k: "✓" if res.get(k) else "✗"  # noqa: E731
                row(3, "ok", f"4K {yes('uhd')} · HDR10 {yes('hdr10')} · Dolby Vision {yes('dv')}")
                ch = res.get("channels", 2)
                row(4, "ok", {2: "estéreo", 6: "5.1", 8: "7.1"}.get(ch, f"{ch} canais") + " pela TV")
            # 6. o PC acorda pela rede?
            row(5, "now")
            w = wol_state(pc_ip)
            if not w:
                row(5, "warn", "não consegui ler a placa de rede")
            elif not w["ok"]:
                row(5, "warn", "a placa de rede deste PC está configurada para não acordar pela rede")
                self.set(test={"wolFix": str(int(w.get("ifIndex") or 0)) if w.get("ifIndex") else ""})
            else:
                row(5, "ok", "ligado: o botão \"Ligar o PC\" da TV vai funcionar"
                    + (" (no Wi-Fi às vezes falha)" if w.get("wifi") else ""))
            self.set(test={"phase": "done"})
        self._bg(work)

    @Slot()
    def fixWol(self):
        """Liga "acordar pela rede" na placa (o Windows pede confirmação de administrador)."""
        rows = list(self._st["test"].get("rows", []))
        try:
            idx = int(self._st["test"].get("wolFix", ""))  # o índice da placa: um número, nunca um nome
        except ValueError:
            return
        if len(rows) < 6:
            return

        def work(alive):
            run_elevated(f"$a = Get-NetAdapter -InterfaceIndex {idx} -ErrorAction Stop\n"
                         "Set-NetAdapterAdvancedProperty -Name $a.Name -RegistryKeyword '*WakeOnMagicPacket' -RegistryValue 1 -ErrorAction SilentlyContinue\n"
                         "Enable-NetAdapterPowerManagement -Name $a.Name -WakeOnMagicPacket -ErrorAction SilentlyContinue")
            pc_ip = local_ip_to(self._box().ip)
            w = wol_state(pc_ip)
            rows[5] = {**rows[5], "s": "ok" if w and w["ok"] else "warn",
                       "v": "ligado: o botão \"Ligar o PC\" da TV vai funcionar" if w and w["ok"]
                       else "não mudou (precisa confirmar como administrador)"}
            self.set(test={"rows": rows, "wolFix": "" if w and w["ok"] else str(idx)})
        self._bg(work)

    @Slot(bool)
    def seen(self, ok):
        self.set(test={"seen": ok})

    # ---------- 6. capas (TMDB) ----------
    @Slot(str)
    def tmdbCheck(self, token):
        token = token.strip()
        if re.fullmatch(r"[0-9a-fA-F]{32}", token):
            self.set(tmdb={"phase": "error", "msg": "Essa é a \"Chave da API\" (a curta). Cole o \"Token de leitura "
                                                    "da API\", o texto longo que fica logo abaixo dela."})
            return
        self.set(tmdb={"phase": "checking", "msg": ""})

        def work(alive):
            req = urllib.request.Request(TMDB_TEST, headers={"Authorization": "Bearer " + token, "accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=10):
                    pass
            except urllib.error.HTTPError as e:
                self.set(tmdb={"phase": "error", "msg": "O TMDB não aceitou esse token." if e.code in (401, 403)
                               else f"O TMDB respondeu com erro {e.code}."})
                return
            except OSError:
                self.set(tmdb={"phase": "error", "msg": "Sem conexão com o TMDB. Confira a internet."})
                return
            keyring.set_password("onVLC", "tmdb_token", token)
            try:
                lib_json("/api/reload-keys", {})
            except OSError:
                log.warning("biblioteca não recarregou a chave do TMDB (vale na próxima vez que abrir)")
            self.set(tmdb={"phase": "ok", "has": True, "msg": ""})
        self._bg(work)

    # ---------- 7. legendas (OpenSubtitles) ----------
    @Slot(str, str)
    def osLogin(self, user, password):
        key = self._os_app_key()
        if not key:
            self.set(os={"phase": "error", "msg": "As legendas automáticas ainda não estão disponíveis nesta versão."})
            return
        self.set(os={"phase": "checking", "msg": ""})

        def work(alive):
            c = opensubs.Client(key, user.strip(), password)
            try:
                c._req("POST", "/login", {"username": c.user, "password": c.password})
            except opensubs.OSError_ as e:
                msg = "Usuário ou senha errados." if "errados" in str(e) else str(e)
                self.set(os={"phase": "error", "msg": msg})
                return
            keyring.set_password("onVLC", "password", password)
            QSettings("PlayerLucas", "Player").setValue("osUser", user.strip())
            self.player.os_client = None
            self.set(os={"phase": "ok", "has": True, "user": user.strip(), "msg": ""})
        self._bg(work)
