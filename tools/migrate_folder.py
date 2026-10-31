"""Uma vez só (2026-10-30): a pasta da biblioteca passou de "Vídeos\\onVLC TV" para "Vídeos\\onVLC".

Renomeia a pasta e leva junto o que dependia do caminho: o índice da biblioteca e as posições
salvas (QSettings PlayerLucas/Player, "pos/<md5 do caminho>"). Antes completa os assistidos com os dados
do filme, para a página de "indisponível" ter sinopse e logo. Rodar com a biblioteca parada.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import library  # noqa: E402
from PySide6.QtCore import QSettings  # noqa: E402

OLD = Path.home() / "Videos" / "onVLC TV"
NEW = Path.home() / "Videos" / "onVLC"


def main():
    if library.running():
        sys.exit("pare a biblioteca antes (ela está respondendo na porta 8780)")
    index = json.loads(library.INDEX.read_text(encoding="utf-8"))
    files, shows = index["files"], index["shows"]

    # assistidos com os dados completos
    hist = library.Library.history(None)
    for h in hist:
        r = files.get(h["id"])
        if r:
            h.update({k: v for k, v in library.snapshot(r, shows.get(r.get("showKey") or "", {})).items() if not h.get(k)})
    library.HISTORY.write_text(json.dumps(hist, ensure_ascii=False, indent=1), encoding="utf-8")
    print("assistidos completados:", [h["title"] for h in hist])

    if OLD.exists():
        if NEW.exists():
            sys.exit(f"{NEW} já existe; junte as pastas à mão")
        OLD.rename(NEW)
        print("pasta renomeada:", NEW)
    s = QSettings("PlayerLucas", "Player")
    moved = 0
    for r in files.values():
        old_path = r["path"]
        if not old_path.lower().startswith(str(OLD).lower()):
            continue
        new_path = str(NEW) + old_path[len(str(OLD)):]
        for prefix in ("pos/", "posAt/"):
            v = s.value(prefix + library.pos_key(old_path))
            if v is not None:
                s.setValue(prefix + library.pos_key(new_path), v)
                s.remove(prefix + library.pos_key(old_path))
                moved += 1
        r["path"] = new_path
        for x in r.get("extSubs") or []:
            if x["path"].lower().startswith(str(OLD).lower()):
                x["path"] = str(NEW) + x["path"][len(str(OLD)):]
    s.sync()
    library.INDEX.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    print(f"índice atualizado ({len(files)} vídeos), {moved} posições levadas junto")


if __name__ == "__main__":
    main()
