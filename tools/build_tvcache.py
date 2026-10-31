"""Compila o app da Mi Box (pasta android\\: biblioteca onVLC + TV Cache) e instala.

Sem Gradle: javac → d8 → aapt2 → zipalign → apksigner, direto com o JDK 17 e o kit Android.
Uso: python tools\\build_tvcache.py [--no-install]
"""
import glob
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "android"
OUT = ROOT / "build" / "tvcache"
SDK = Path(os.environ.get("ANDROID_HOME") or Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk")
PLATFORM, BUILD_TOOLS = "android-34", "34.0.0"
BOX = "192.168.1.31:5555"
# o motor do VLC (só o player, sem o app): baixado uma vez do repositório oficial da VideoLAN
LIBVLC_VERSION = "3.7.7"
LIBVLC_AAR = f"https://repo1.maven.org/maven2/org/videolan/android/libvlc-all/{LIBVLC_VERSION}/libvlc-all-{LIBVLC_VERSION}.aar"
LIBVLC_DIR = ROOT / "build" / "libvlc"
ABI = "armeabi-v7a"  # a Mi Box (32 bits)
# chave própria do app (precisa ser sempre a mesma para atualizar por cima): fica fora do repositório,
# e a senha, no Gerenciador de Credenciais do Windows (serviço onVLC, "apk_keystore")
KEYSTORE = Path(os.environ["LOCALAPPDATA"]) / "onVLC" / "tvcache.keystore"


def libvlc():
    """classes.jar e as bibliotecas nativas da Mi Box, tiradas do pacote oficial."""
    LIBVLC_DIR.mkdir(parents=True, exist_ok=True)
    aar = LIBVLC_DIR / f"libvlc-all-{LIBVLC_VERSION}.aar"
    if not aar.exists():
        print("› baixando o motor do VLC", LIBVLC_VERSION)
        tmp = aar.with_suffix(".tmp")
        urllib.request.urlretrieve(LIBVLC_AAR, tmp)
        os.replace(tmp, aar)
    out = LIBVLC_DIR / LIBVLC_VERSION
    jar, libs = out / "classes.jar", out / "jni" / ABI
    if not jar.exists():
        with zipfile.ZipFile(aar) as z:
            for n in z.namelist():
                if n == "classes.jar" or (n.startswith(f"jni/{ABI}/") and n.endswith(".so")):
                    z.extract(n, out)
    return jar, sorted(libs.glob("*.so"))


def java_home():
    if os.environ.get("JAVA_HOME"):
        return Path(os.environ["JAVA_HOME"])
    hits = sorted(glob.glob(r"C:\Program Files\Microsoft\jdk-17*"))
    if not hits:
        sys.exit("JDK 17 não encontrado (winget install Microsoft.OpenJDK.17)")
    return Path(hits[-1])


def run(*cmd, **kw):
    print("›", Path(cmd[0]).name, *[str(c) for c in cmd[1:4]], "…" if len(cmd) > 4 else "")
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def main():
    jdk = java_home()
    env = dict(os.environ, JAVA_HOME=str(jdk), ANDROID_HOME=str(SDK))
    bt = SDK / "build-tools" / BUILD_TOOLS
    android_jar = SDK / "platforms" / PLATFORM / "android.jar"
    if not android_jar.exists() or not bt.exists():
        sdkm = SDK / "cmdline-tools" / "latest" / "bin" / "sdkmanager.bat"
        subprocess.run([str(sdkm), "--licenses"], input="y\n" * 20, text=True, env=env, check=True,
                       stdout=subprocess.DEVNULL)
        run(sdkm, f"platforms;{PLATFORM}", f"build-tools;{BUILD_TOOLS}", env=env)

    if OUT.exists():
        shutil.rmtree(OUT)
    classes, dex = OUT / "classes", OUT / "dex"
    classes.mkdir(parents=True)
    dex.mkdir()

    vlc_jar, vlc_libs = libvlc()
    sources = [str(p) for p in (SRC / "src").rglob("*.java")]
    run(jdk / "bin" / "javac.exe", "-source", "8", "-target", "8", "-Xlint:-options", "-encoding", "UTF-8",
        "-classpath", f"{android_jar}{os.pathsep}{vlc_jar}", "-d", classes, *sources)
    class_files = [str(p) for p in classes.rglob("*.class")]
    # o motor traz referências a bibliotecas do Android (androidx) só em partes que não usamos
    # (transmitir para outro aparelho, tela externa): o d8 avisa e segue
    run(bt / "d8.bat", "--release", "--min-api", "26", "--lib", android_jar, "--output", dex, *class_files, vlc_jar,
        env=env, stderr=subprocess.DEVNULL)

    res_zip = OUT / "res.zip"
    run(bt / "aapt2.exe", "compile", "--dir", SRC / "res", "-o", res_zip)
    # uma cópia da tela da biblioteca vai dentro do app, para a primeira vez sem PC
    assets = OUT / "assets"
    shutil.copytree(ROOT / "tvui", assets / "tvui")
    base = OUT / "base.apk"
    run(bt / "aapt2.exe", "link", "-o", base, "-I", android_jar, "--manifest", SRC / "AndroidManifest.xml",
        "--min-sdk-version", "26", "--target-sdk-version", "34", "-A", assets, res_zip)
    with zipfile.ZipFile(base, "a", zipfile.ZIP_DEFLATED) as z:
        for d in sorted(dex.glob("classes*.dex")):
            z.write(d, d.name)
        for so in vlc_libs:
            z.write(so, f"lib/{ABI}/{so.name}")

    aligned = OUT / "aligned.apk"
    run(bt / "zipalign.exe", "-f", "4", base, aligned)

    import keyring
    import secrets
    ks = KEYSTORE
    pw = keyring.get_password("onVLC", "apk_keystore")
    if not ks.exists():  # só na primeira vez: um app instalado com outra chave não aceita atualização
        pw = secrets.token_urlsafe(24)
        ks.parent.mkdir(parents=True, exist_ok=True)
        run(jdk / "bin" / "keytool.exe", "-genkeypair", "-keystore", ks, "-alias", "tvcache", "-storepass", pw,
            "-keypass", pw, "-dname", "CN=Lucas", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000")
        keyring.set_password("onVLC", "apk_keystore", pw)
    if not pw:
        sys.exit("Senha da chave do APK não está no Gerenciador de Credenciais (onVLC / apk_keystore)")
    apk = ROOT / "dist" / "tvcache.apk"
    apk.parent.mkdir(exist_ok=True)
    run(bt / "apksigner.bat", "sign", "--ks", ks, "--ks-pass", "env:ONVLC_KS", "--out", apk, aligned,
        env=dict(env, ONVLC_KS=pw))
    print("pronto:", apk)

    if "--no-install" not in sys.argv:
        sys.path.insert(0, str(ROOT))
        import tvstream
        box = tvstream.MiBox()
        if not box.connect(print):  # acorda a Mi Box pela rede se ela estiver dormindo
            sys.exit("A Mi Box não respondeu; o APK ficou em " + str(apk))
        run(box.adb, "-s", box.serial, "install", "-r", apk)


if __name__ == "__main__":
    main()
