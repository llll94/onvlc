"""Roda JavaScript na página da biblioteca aberta na TV (depuração do WebView pelo adb).

Uso: python tools\\tvjs.py "expressão"      → imprime o resultado
     python tools\\tvjs.py --key ArrowRight   → aperta uma tecla do "controle" na página
"""
import json
import sys
import urllib.request
from pathlib import Path

import websocket

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tvstream  # noqa: E402

PORT = 9223


def connect():
    box = tvstream.TVBox()
    box.connect()
    socks = box.shell("cat /proc/net/unix | grep -o 'webview_devtools_remote_[0-9]*' | sort -u")
    pid = box.shell("pidof com.lucas.tvcache")
    name = next((s for s in socks.split() if s.endswith("_" + pid)), None) or (socks.split() or [None])[-1]
    if not name:
        sys.exit("página da biblioteca não está aberta na TV")
    box.run("-s", box.serial, "forward", f"tcp:{PORT}", f"localabstract:{name}")
    pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=5))
    page = next(p for p in pages if "/ui/" in p.get("url", ""))
    return websocket.create_connection(page["webSocketDebuggerUrl"], timeout=15, suppress_origin=True)


def evaluate(ws, expr):
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True, "awaitPromise": True}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == 1:
            res = msg.get("result", {})
            if "exceptionDetails" in res:
                return "ERRO: " + json.dumps(res["exceptionDetails"].get("exception", {}).get("description", res["exceptionDetails"]))
            return res.get("result", {}).get("value")


def key(ws, k):
    expr = f"document.dispatchEvent(new KeyboardEvent('keydown', {{key: {json.dumps(k)}, bubbles: true}}))"
    return evaluate(ws, expr)


if __name__ == "__main__":
    ws = connect()
    if sys.argv[1] == "--key":
        for k in sys.argv[2:]:
            key(ws, k)
        print("ok")
    else:
        out = evaluate(ws, sys.argv[1])
        print(out if isinstance(out, str) else json.dumps(out, ensure_ascii=False))
