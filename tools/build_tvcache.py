"""Compila o app TV Cache (pasta android\\) e instala na Mi Box.

Sem Gradle: javac → d8 → aapt2 → zipalign → apksigner, direto com o JDK 17 e o kit Android.
Uso: python tools\\build_tvcache.py [--no-install]
"""
import glob
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "android"
OUT = ROOT / "build" / "tvcache"
SDK = Path(os.environ.get("ANDROID_HOME") or Path(os.environ["LOCALAPPDATA"]) / "Android" / "Sdk")
PLATFORM, BUILD_TOOLS = "android-34", "34.0.0"
BOX = "192.168.1.31:5555"


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

    sources = [str(p) for p in (SRC / "src").rglob("*.java")]
    run(jdk / "bin" / "javac.exe", "-source", "8", "-target", "8", "-Xlint:-options", "-encoding", "UTF-8",
        "-classpath", android_jar, "-d", classes, *sources)
    class_files = [str(p) for p in classes.rglob("*.class")]
    run(bt / "d8.bat", "--release", "--min-api", "26", "--lib", android_jar, "--output", dex, *class_files, env=env)

    base = OUT / "base.apk"
    run(bt / "aapt2.exe", "link", "-o", base, "-I", android_jar, "--manifest", SRC / "AndroidManifest.xml",
        "--min-sdk-version", "26", "--target-sdk-version", "34")
    with zipfile.ZipFile(base, "a") as z:
        z.write(dex / "classes.dex", "classes.dex")

    aligned = OUT / "aligned.apk"
    run(bt / "zipalign.exe", "-f", "4", base, aligned)

    ks = SRC / "tvcache.keystore"  # chave própria do app; precisa ser a mesma para atualizar por cima
    if not ks.exists():
        run(jdk / "bin" / "keytool.exe", "-genkeypair", "-keystore", ks, "-alias", "tvcache", "-storepass", "android",
            "-keypass", "android", "-dname", "CN=Lucas", "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000")
    apk = ROOT / "dist" / "tvcache.apk"
    apk.parent.mkdir(exist_ok=True)
    run(bt / "apksigner.bat", "sign", "--ks", ks, "--ks-pass", "pass:<senha>", "--out", apk, aligned, env=env)
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
