package app.onvlc.tv;

import android.content.Context;
import android.content.Intent;
import android.net.Uri;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.RandomAccessFile;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.URL;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Locale;
import java.util.Map;

/**
 * Servidor HTTP local (127.0.0.1:8090) entre o VLC da TV e o PC.
 *
 * O arquivo é dividido em blocos de 8 MB guardados no disco. Em volta do ponto que está tocando
 * baixa nesta ordem: o bloco pedido pelo VLC, os seguintes até 5 min à frente, depois 1 min atrás.
 * O que já baixou fica guardado até 2 GB, de todos os filmes juntos; passando disso, sai o bloco usado
 * há mais tempo (nunca um da janela atual).
 *
 * Kit de abertura (warm): para cada filme da biblioteca, em ordem de prioridade, guarda à parte (até 4 GB,
 * sem contar nos 2 GB) os blocos que o player pede ao abrir (começo, fim com o índice e os que ele pediu
 * na primeira vez), o primeiro minuto e 1 minuto a partir de onde parou. Assim o filme abre sem esperar a rede.
 *
 * Um bloco ainda chegando do PC já é entregue ao player conforme chega (sem esperar os 8 MB inteiros): pular para
 * um trecho que não estava guardado volta a tocar assim que os primeiros KB chegam.
 *
 * Bloco que sumiu do disco (o Android limpa o cache quando falta espaço) ou ficou cortado (queda de energia)
 * é descartado e baixado de novo. Com menos de 1 GB livre na Mi Box, o cache encolhe em vez de crescer.
 *
 * Rotas (todas debaixo de /<TOKEN>/, um valor aleatório por processo: outro app na TV não adivinha o endereço):
 * /stream/<nome> (o vídeo, com Range), /status (JSON da janela), /stop,
 * /ui/<arquivo> (a biblioteca da TV: repassa para o PC, com a chave, e guarda uma cópia para quando ele estiver
 * desligado; imagens e legendas vão para o cache do sistema, que o Android limpa quando falta espaço).
 */
final class CacheServer {
    static final int PORT = 8090;
    static final String TOKEN;  // prefixo das rotas: só este app sabe

    static {
        byte[] b = new byte[16];
        new java.security.SecureRandom().nextBytes(b);
        TOKEN = PcFinder.hex(b);
    }

    /** Endereço local de uma rota ("/ui/index.html" → http://127.0.0.1:8090/<token>/ui/index.html). */
    static String local(String route) {
        return "http://127.0.0.1:" + PORT + "/" + TOKEN + route;
    }

    /** O endereço é deste servidor local (com o token)? O WebView só carrega esses. */
    static boolean isLocal(String url) {
        return url != null && url.startsWith("http://127.0.0.1:" + PORT + "/" + TOKEN + "/");
    }

    private final java.util.concurrent.ExecutorService conns = java.util.concurrent.Executors.newFixedThreadPool(16);
    static final long BLOCK = 8L << 20;
    static final long LIMIT = 2L << 30;
    static final int AHEAD_S = 300, BEHIND_S = 60;
    static final int WORKERS = 2;
    static final long KIT_LIMIT = 4L << 30;
    static final int KIT_SECONDS = 60;
    static final long RESERVE = 1L << 30;  // espaço que fica livre na Mi Box para o resto do sistema

    private static CacheServer instance;

    static synchronized CacheServer get(Context ctx) {
        if (instance == null) instance = new CacheServer(ctx.getApplicationContext());
        return instance;
    }

    private final Context ctx;
    private final File dir;
    private final Object lock = new Object();
    private final HashMap<Long, Long> blocks = new HashMap<>();  // bloco do filme aberto → último uso
    private final HashMap<String, Long> store = new HashMap<>();  // todos os blocos no disco ("chave_n") → último uso
    private final HashSet<String> pinned = new HashSet<>();  // blocos dos kits: a reprodução não apaga
    private final HashSet<Long> asked = new HashSet<>();  // blocos que o player pediu logo ao abrir
    private long openedAt;
    private volatile int warmGen;
    private final HashSet<Long> loading = new HashSet<>();
    private final HashMap<Long, Long> partial = new HashMap<>();  // bloco do filme aberto chegando → bytes já gravados
    private ServerSocket server;
    private String upstream, key = "", contentType = "video/x-matroska";
    private long size = -1, bps = 16_000_000L, playhead, urgent = -1;
    private long downloaded, lastDownloaded, lastStatusAt;
    private boolean running;
    private int workersAlive;

    private CacheServer(Context ctx) {
        this.ctx = ctx;
        dir = new File(ctx.getCacheDir(), "blocks");
        dir.mkdirs();
        File[] files = dir.listFiles();
        if (files != null) {
            for (File f : files) {
                String n = f.getName();
                // vazio ou maior que um bloco: sobra de uma gravação interrompida
                if (n.endsWith(".blk") && f.length() > 0 && f.length() <= BLOCK) store.put(n.substring(0, n.length() - 4), f.lastModified());
                else f.delete();
            }
        }
        pinned.addAll(ctx.getSharedPreferences("kit", Context.MODE_PRIVATE).getStringSet("pinned", new HashSet<String>()));
    }

    /** O PC manda .../v/<id>/<tamanho>/<nome>; tamanho + nome identificam o arquivo. */
    static String keyFor(String url) {
        String[] parts = url.split("/");
        // "v2": blocos guardados pela versão anterior (que podia aceitar páginas de erro) são descartados
        return Integer.toHexString(("v2:" + parts[parts.length - 2] + "/" + parts[parts.length - 1]).hashCode());
    }

    /** Prepara um vídeo. Se for o mesmo de antes (mesmo nome e tamanho), aproveita os blocos guardados. */
    void open(String url, long bitsPerSecond) throws IOException {
        String[] parts = url.split("/");
        String newKey = keyFor(url);
        synchronized (lock) {
            upstream = url;
            if (bitsPerSecond > 0) bps = bitsPerSecond;
            urgent = -1;
            // o tamanho vem no próprio endereço: o VLC já recebe resposta na hora, sem esperar uma consulta ao PC
            // (esperando, ele desiste de pular para a posição pedida e começa do início)
            try {
                size = Long.parseLong(parts[parts.length - 2]);
            } catch (NumberFormatException e) {
                size = -1;  // endereço sem tamanho: pergunta ao PC
            }
            if (!newKey.equals(key)) {
                key = newKey;
                playhead = 0;
                blocks.clear();
                partial.clear();
                String prefix = key + "_";
                for (Map.Entry<String, Long> e : store.entrySet()) {
                    if (e.getKey().startsWith(prefix)) blocks.put(Long.parseLong(e.getKey().substring(prefix.length())), e.getValue());
                }
            }
            openedAt = System.currentTimeMillis();
            asked.clear();
            running = true;
            lock.notifyAll();
        }
        startServer();
        startWorkers();
    }

    /** Liga só o servidor (a biblioteca usa antes de existir um filme aberto). */
    void startUi() throws IOException {
        startServer();
    }

    void pause() {
        synchronized (lock) {
            running = false;
            lock.notifyAll();
        }
    }

    // ---------- servidor ----------
    private synchronized void startServer() throws IOException {
        if (server != null && !server.isClosed()) return;
        final ServerSocket ss = new ServerSocket();
        ss.setReuseAddress(true);
        ss.bind(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), PORT));
        server = ss;
        Thread t = new Thread(() -> {
            while (!ss.isClosed()) {
                try {
                    final Socket s = ss.accept();
                    conns.execute(() -> handle(s));  // no máximo 16 conexões trabalhando ao mesmo tempo
                } catch (IOException e) {
                    // servidor fechado
                }
            }
        }, "cache-accept");
        t.setDaemon(true);
        t.start();
    }

    private static String readLine(InputStream in) throws IOException {
        StringBuilder sb = new StringBuilder();
        int c;
        while ((c = in.read()) != -1 && c != '\n') {
            if (c != '\r') sb.append((char) c);
            if (sb.length() > 8192) throw new IOException("linha grande demais");
        }
        return c == -1 && sb.length() == 0 ? null : sb.toString();
    }

    private void handle(Socket s) {
        try (Socket sock = s) {
            sock.setSoTimeout(60000);
            InputStream in = new BufferedInputStream(sock.getInputStream());
            String line = readLine(in);
            if (line == null) return;
            String[] req = line.split(" ");
            String method = req[0], path = req.length > 1 ? req[1] : "/";
            String range = null;
            int headers = 0;
            for (String h; (h = readLine(in)) != null && !h.isEmpty(); ) {
                if (++headers > 64) return;
                if (h.toLowerCase(Locale.ROOT).startsWith("range:")) range = h.substring(6).trim();
            }
            OutputStream out = new BufferedOutputStream(sock.getOutputStream(), 256 * 1024);
            String prefix = "/" + TOKEN + "/";
            if (!path.startsWith(prefix)) {  // sem o token: não existe
                send(out, "404 Not Found", "text/plain", new byte[0]);
                out.flush();
                return;
            }
            path = path.substring(prefix.length() - 1);
            if (path.startsWith("/status")) {
                send(out, "200 OK", "application/json", status().getBytes("UTF-8"));
            } else if (path.startsWith("/stop")) {
                send(out, "200 OK", "text/plain", "ok".getBytes("UTF-8"));
                ctx.stopService(new Intent(ctx, CacheService.class));
            } else if (path.startsWith("/stream/")) {
                stream(method, range, out);
            } else if (path.startsWith("/ui/")) {
                ui(path, out);
            } else {
                send(out, "404 Not Found", "text/plain", "não encontrado".getBytes("UTF-8"));
            }
            out.flush();
        } catch (Exception e) {
            // o VLC fecha a conexão quando pula para outro ponto: normal
        }
    }

    private static void send(OutputStream out, String status, String type, byte[] body) throws IOException {
        send(out, status, type, body, "");
    }

    private static void send(OutputStream out, String status, String type, byte[] body, String extra) throws IOException {
        // imagens da biblioteca: o nome muda quando a imagem muda, então o navegador guarda para sempre
        // (com "no-cache" ele buscava tudo de novo a cada troca de aba, e as capas piscavam)
        String cache = type.startsWith("image/") ? "max-age=31536000, immutable" : "no-cache";
        String h = "HTTP/1.1 " + status + "\r\nContent-Type: " + type + "\r\nContent-Length: " + body.length
                + "\r\nCache-Control: " + cache + "\r\n" + extra + "Connection: close\r\n\r\n";
        out.write(h.getBytes("ISO-8859-1"));
        out.write(body);
    }

    // ---------- biblioteca ----------
    private void ui(String path, OutputStream out) throws IOException {
        String rel = path.substring(4);
        int q = rel.indexOf('?');
        if (q >= 0) rel = rel.substring(0, q);
        rel = Uri.decode(rel);
        if (rel.isEmpty()) rel = "index.html";
        if (rel.contains("..")) {
            send(out, "404 Not Found", "text/plain", new byte[0]);
            return;
        }
        boolean image = rel.startsWith("img/"), sub = rel.startsWith("sub/");
        if (sub && !rel.matches("sub/[0-9a-f]{1,32}/\\d{1,2}")) {
            send(out, "404 Not Found", "text/plain", new byte[0]);
            return;
        }
        String remote = rel.startsWith("api/") || image || sub ? "/" + rel : "/tv/" + rel;
        // imagens e legendas no cache do sistema (o Android apaga quando falta espaço); página e listas, guardadas
        boolean big = image || sub || rel.equals("api/selftest");
        File folder = big ? new File(ctx.getCacheDir(), "ui") : new File(ctx.getFilesDir(), "ui");
        File cached = new File(folder, rel.replace('/', '_'));
        String type = typeOf(rel);
        // imagens não mudam (um arquivo novo ganha outro nome): a cópia guardada vale sempre
        if (image && cached.exists()) {
            send(out, "200 OK", type, readAll(new FileInputStream(cached)));
            return;
        }
        String base = PcFinder.base();
        if (base != null) {
            HttpURLConnection c = null;
            boolean ok = false;
            try {
                c = connect(base + remote.replace(" ", "%20"));  // connect() põe a chave
                c.setConnectTimeout(2000);
                c.setReadTimeout(8000);
                int code = c.getResponseCode();
                if (code == 200) {
                    byte[] body = readAll(c.getInputStream());
                    cached.getParentFile().mkdirs();
                    File tmp = new File(cached.getPath() + ".tmp");
                    try (OutputStream f = new FileOutputStream(tmp)) {
                        f.write(body);
                    }
                    tmp.renameTo(cached);
                    ok = true;
                    send(out, "200 OK", type, body, "X-onVLC-Offline: 0\r\n");
                    return;
                }
                if (code == 404 && image) {
                    send(out, "404 Not Found", "text/plain", new byte[0]);
                    return;
                }
            } catch (IOException e) {
                PcFinder.lost(ctx);
            } finally {
                if (c != null && !ok) c.disconnect();
            }
        } else {
            PcFinder.search(ctx);
        }
        if (cached.exists()) {  // PC desligado: a última cópia
            send(out, "200 OK", type, readAll(new FileInputStream(cached)), "X-onVLC-Offline: 1\r\n");
            return;
        }
        try {  // nunca falou com o PC: a cópia da tela que veio dentro do app
            send(out, "200 OK", type, readAll(ctx.getAssets().open("tvui/" + rel)), "X-onVLC-Offline: 1\r\n");
        } catch (IOException e) {
            send(out, "503 Service Unavailable", "text/plain", new byte[0]);
        }
    }

    private static String typeOf(String rel) {
        if (rel.startsWith("sub/")) return "text/plain; charset=utf-8";
        if (rel.equals("api/selftest")) return "video/mp4";
        if (rel.startsWith("api/")) return "application/json; charset=utf-8";
        if (rel.endsWith(".html")) return "text/html; charset=utf-8";
        if (rel.endsWith(".js")) return "text/javascript; charset=utf-8";
        if (rel.endsWith(".css")) return "text/css; charset=utf-8";
        if (rel.endsWith(".png")) return "image/png";
        if (rel.endsWith(".svg")) return "image/svg+xml";
        return "image/jpeg";
    }

    private static byte[] readAll(InputStream in) throws IOException {
        try (InputStream src = in) {
            ByteArrayOutputStream b = new ByteArrayOutputStream();
            byte[] buf = new byte[64 * 1024];
            int r;
            while ((r = src.read(buf)) > 0) b.write(buf, 0, r);
            return b.toByteArray();
        }
    }

    private void stream(String method, String range, OutputStream out) throws Exception {
        long total = waitSize();
        long start = 0, end = total - 1;
        boolean partial = false;
        if (range != null && range.startsWith("bytes=")) {
            String[] r = range.substring(6).split("-", 2);
            if (!r[0].trim().isEmpty()) {
                start = Long.parseLong(r[0].trim());
                if (r.length > 1 && !r[1].trim().isEmpty()) end = Math.min(total - 1, Long.parseLong(r[1].trim()));
            } else if (r.length > 1) {
                start = Math.max(0, total - Long.parseLong(r[1].trim()));
            }
            partial = true;
        }
        if (start >= total || end < start) {
            out.write(("HTTP/1.1 416 Range Not Satisfiable\r\nContent-Range: bytes */" + total
                    + "\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").getBytes("ISO-8859-1"));
            return;
        }
        StringBuilder h = new StringBuilder(partial ? "HTTP/1.1 206 Partial Content\r\n" : "HTTP/1.1 200 OK\r\n");
        if (partial) h.append("Content-Range: bytes ").append(start).append('-').append(end).append('/').append(total).append("\r\n");
        h.append("Content-Type: ").append(contentType).append("\r\nAccept-Ranges: bytes\r\nContent-Length: ")
                .append(end - start + 1).append("\r\nConnection: close\r\n\r\n");
        out.write(h.toString().getBytes("ISO-8859-1"));
        if (method.equals("HEAD")) return;

        byte[] buf = new byte[256 * 1024];
        long pos = start;
        int bad = 0;
        while (pos <= end) {
            long idx = pos / BLOCK;
            long off = pos - idx * BLOCK;
            long[] avail = new long[1];
            File f = waitData(idx, pos, off, avail);
            if (avail[0] >= 0) {  // bloco ainda chegando: manda o que já tem e volta a esperar o resto
                try (RandomAccessFile raf = new RandomAccessFile(f, "r")) {
                    long n = Math.min(avail[0] - off, end - pos + 1);
                    raf.seek(off);
                    while (n > 0) {
                        int r = raf.read(buf, 0, (int) Math.min(buf.length, n));
                        if (r <= 0) break;
                        out.write(buf, 0, r);
                        n -= r;
                        pos += r;
                    }
                } catch (java.io.FileNotFoundException gone) {
                    // acabou de chegar (o .tmp virou .blk) ou falhou: a próxima volta acha o bloco certo
                }
                out.flush();
                continue;
            }
            long want = Math.min(total, (idx + 1) * BLOCK) - idx * BLOCK;
            if (f.length() != want) {
                // apagado pelo Android ou cortado: descarta e espera baixar de novo
                if (++bad > 3) throw new IOException("bloco " + idx + " não ficou bom");
                synchronized (lock) {
                    drop(key + "_" + idx);
                    lock.notifyAll();
                }
                continue;
            }
            try (RandomAccessFile raf = new RandomAccessFile(f, "r")) {
                long n = Math.min(raf.length() - off, end - pos + 1);
                if (n <= 0) throw new IOException("bloco curto");
                raf.seek(off);
                while (n > 0) {
                    int r = raf.read(buf, 0, (int) Math.min(buf.length, n));
                    if (r <= 0) break;
                    out.write(buf, 0, r);
                    n -= r;
                    pos += r;
                }
            }
            out.flush();
        }
    }

    private long waitSize() throws Exception {
        synchronized (lock) {
            long t0 = System.currentTimeMillis();
            while (size < 0) {
                ensureRunning();
                lock.wait(500);
                if (System.currentTimeMillis() - t0 > 20000) throw new IOException("o PC não respondeu");
            }
            return size;
        }
    }

    /**
     * Espera dados do bloco a partir de `off`; quem pede um bloco que falta passa na frente da fila.
     * Bloco pronto: devolve o .blk e avail[0] = -1. Ainda chegando: devolve o .tmp e avail[0] = bytes já gravados.
     */
    private File waitData(long idx, long pos, long off, long[] avail) throws Exception {
        synchronized (lock) {
            // leituras no fim do arquivo (índice do MKV) não mudam o ponto que está tocando
            if (pos < size - 2 * BLOCK) playhead = pos;
            ensureRunning();
            lock.notifyAll();
            long t0 = System.currentTimeMillis();
            avail[0] = -1;
            while (!blocks.containsKey(idx)) {
                Long got = partial.get(idx);
                if (got != null && got > off) {
                    avail[0] = got;
                    if (System.currentTimeMillis() - openedAt < 15000 && asked.add(idx)) learn(key, idx);
                    return new File(dir, key + "_" + idx + ".tmp");
                }
                urgent = idx;
                lock.notifyAll();
                lock.wait(500);
                if (System.currentTimeMillis() - t0 > 60000) throw new IOException("tempo esgotado");
            }
            if (urgent == idx) urgent = -1;
            long now = System.currentTimeMillis();
            blocks.put(idx, now);
            store.put(key + "_" + idx, now);
            // o que o player pede nos primeiros segundos entra no kit deste arquivo (índice, legendas, etc.)
            if (now - openedAt < 15000 && asked.add(idx)) learn(key, idx);
            return new File(dir, key + "_" + idx + ".blk");
        }
    }

    private void ensureRunning() {
        if (!running) {
            running = true;
            startWorkers();
        }
    }

    // ---------- downloads ----------
    private void startWorkers() {
        synchronized (lock) {
            while (workersAlive < WORKERS) {
                workersAlive++;
                Thread t = new Thread(this::workLoop, "cache-worker");
                t.setDaemon(true);
                t.start();
            }
        }
    }

    private void workLoop() {
        try {
            while (true) {
                String url, k;
                long idx = -1, from = 0, to = 0;
                boolean probe;
                synchronized (lock) {
                    if (!running) return;
                    url = upstream;
                    k = key;
                    probe = size < 0;
                    if (!probe) {
                        idx = next();
                        if (idx < 0) {
                            lock.wait(1000);
                            continue;
                        }
                        loading.add(idx);
                        from = idx * BLOCK;
                        to = Math.min(size, from + BLOCK) - 1;
                    }
                }
                if (probe) {
                    try {
                        long n = probe(url);
                        synchronized (lock) {
                            if (url.equals(upstream)) {
                                size = n;
                                lock.notifyAll();
                            }
                        }
                    } catch (IOException e) {
                        Thread.sleep(2000);
                    }
                    continue;
                }
                try {
                    download(url, k, idx, from, to, size);
                } catch (IOException e) {
                    Thread.sleep(1000);  // PC ocupado ou Wi-Fi oscilou: tenta de novo
                } finally {
                    synchronized (lock) {
                        loading.remove(idx);
                        lock.notifyAll();
                    }
                }
            }
        } catch (InterruptedException e) {
            // encerrando
        } finally {
            synchronized (lock) {
                workersAlive--;
            }
        }
    }

    /** Próximo bloco a baixar (chamado com o lock). */
    private long next() {
        long total = (size + BLOCK - 1) / BLOCK;
        long p = playhead / BLOCK;
        double perSecond = bps / 8.0 / BLOCK;  // blocos por segundo de filme
        long ahead = Math.max(2, (long) Math.ceil(AHEAD_S * perSecond));
        long behind = (long) Math.ceil(BEHIND_S * perSecond);
        if (urgent >= 0 && need(urgent, total)) return makeRoom(urgent, ahead, behind) ? urgent : -1;
        for (long i = 0; i <= ahead; i++) {
            if (need(p + i, total)) return makeRoom(p, ahead, behind) ? p + i : -1;
        }
        for (long i = 1; i <= behind; i++) {
            if (need(p - i, total)) return makeRoom(p, ahead, behind) ? p - i : -1;
        }
        return -1;
    }

    private boolean need(long i, long total) {
        return i >= 0 && i < total && !blocks.containsKey(i) && !loading.contains(i);
    }

    /** Blocos dos kits já baixados (chamado com o lock). */
    private long kitHave() {
        long n = 0;
        for (String k : pinned) if (store.containsKey(k)) n++;
        return n;
    }

    /** Bytes guardados fora dos kits (chamado com o lock). */
    private long cachedBytes() {
        long n = 0;
        for (String k : store.keySet()) if (!pinned.contains(k)) n++;
        return n * BLOCK;
    }

    /** Pouco espaço livre na Mi Box? (a conta do sistema é barata: uma chamada) */
    private boolean diskLow() {
        return dir.getUsableSpace() < RESERVE + BLOCK;
    }

    /**
     * Abre espaço para mais um bloco apagando o usado há mais tempo (de qualquer filme), fora da janela atual
     * e dos kits: quando passa de 2 GB ou quando a Mi Box está ficando sem espaço.
     */
    private boolean makeRoom(long p, long ahead, long behind) {
        String prefix = key + "_";
        long used = cachedBytes();
        while (used + BLOCK > LIMIT || diskLow()) {
            String victim = null;
            long oldest = Long.MAX_VALUE;
            for (Map.Entry<String, Long> e : store.entrySet()) {
                String n = e.getKey();
                if (pinned.contains(n)) continue;
                if (n.startsWith(prefix)) {
                    long i = Long.parseLong(n.substring(prefix.length()));
                    if (i >= p - behind && i <= p + ahead) continue;
                }
                if (e.getValue() < oldest) {
                    oldest = e.getValue();
                    victim = n;
                }
            }
            if (victim == null && diskLow()) {
                // disco quase cheio e só sobraram blocos de kit: solta um kit (o Android precisa do espaço)
                for (String n : store.keySet()) {
                    if (pinned.contains(n) && !(n.startsWith(prefix) && Math.abs(Long.parseLong(n.substring(prefix.length())) - p) <= ahead)) {
                        victim = n;
                        pinned.remove(n);
                        break;
                    }
                }
            }
            if (victim == null) return false;
            drop(victim);
            used -= BLOCK;
        }
        return true;
    }

    private void drop(String name) {
        new File(dir, name + ".blk").delete();
        store.remove(name);
        String prefix = key + "_";
        if (name.startsWith(prefix)) blocks.remove(Long.parseLong(name.substring(prefix.length())));
    }

    private HttpURLConnection connect(String url) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        PcFinder.auth(c);  // a biblioteca do PC só responde a quem tem a chave
        c.setConnectTimeout(5000);
        c.setReadTimeout(15000);
        return c;
    }

    private long probe(String url) throws IOException {
        HttpURLConnection c = connect(url);
        c.setRequestMethod("HEAD");
        try {
            if (c.getResponseCode() != 200) throw new IOException("o PC respondeu " + c.getResponseCode());
            long n = c.getContentLengthLong();
            if (n <= 0) throw new IOException("o PC não informou o tamanho");
            String t = c.getContentType();
            if (t != null) contentType = t;
            return n;
        } finally {
            c.disconnect();
        }
    }

    private void download(String url, String k, long idx, long from, long to, long total) throws IOException {
        HttpURLConnection c = connect(url);
        c.setRequestProperty("Range", "bytes=" + from + "-" + to);
        File tmp = new File(dir, k + "_" + idx + ".tmp");
        File dst = new File(dir, k + "_" + idx + ".blk");
        // só aceita o pedaço pedido do arquivo certo; uma página de erro nunca vira bloco
        if (c.getResponseCode() != 206) {
            c.disconnect();
            throw new IOException("o PC respondeu " + c.getResponseCode());
        }
        String cr = c.getHeaderField("Content-Range");
        if (cr == null || !cr.endsWith("/" + total)) {
            c.disconnect();
            throw new IOException("pedaço de outro arquivo: " + cr);
        }
        boolean ok = false;
        boolean mine;
        synchronized (lock) {
            mine = k.equals(key);
            if (mine) partial.put(idx, 0L);
        }
        try (InputStream in = c.getInputStream(); FileOutputStream out = new FileOutputStream(tmp)) {
            byte[] buf = new byte[256 * 1024];
            long got = 0;
            int r;
            while ((r = in.read(buf)) > 0) {
                out.write(buf, 0, r);  // sem buffer na frente: quem lê o .tmp já vê estes bytes
                got += r;
                synchronized (lock) {
                    downloaded += r;
                    if (mine && k.equals(key)) {
                        partial.put(idx, got);
                        lock.notifyAll();
                    }
                }
                if (Thread.currentThread().isInterrupted()) throw new IOException("cancelado");
            }
            if (got != to - from + 1) throw new IOException("bloco incompleto");
            out.getFD().sync();  // no disco de verdade antes de valer: uma queda de energia não deixa bloco cortado
            ok = true;
        } catch (IOException e) {
            synchronized (lock) {
                if (mine && k.equals(key)) partial.remove(idx);
            }
            tmp.delete();
            throw e;
        } finally {
            // lido até o fim, a conexão volta para ser usada no próximo bloco (sem abrir outra com o PC)
            if (!ok) c.disconnect();
        }
        synchronized (lock) {
            if (mine && k.equals(key)) partial.remove(idx);
            if (!tmp.renameTo(dst)) {
                tmp.delete();
                lock.notifyAll();
                return;
            }
            long now = System.currentTimeMillis();
            store.put(k + "_" + idx, now);
            if (k.equals(key)) blocks.put(idx, now);  // de outro filme (kit ou troca no meio): fica guardado para ele
            lock.notifyAll();
        }
    }

    // ---------- kit de abertura ----------
    static final class KitItem {
        String url;
        long size, bps;
        double pos;  // onde parou (0: do começo)
    }

    private void learn(String k, long idx) {
        android.content.SharedPreferences p = ctx.getSharedPreferences("learn", Context.MODE_PRIVATE);
        String cur = p.getString(k, "");
        if (("," + cur + ",").contains("," + idx + ",")) return;
        String[] list = cur.isEmpty() ? new String[0] : cur.split(",");
        if (list.length >= 16) return;
        p.edit().putString(k, cur.isEmpty() ? String.valueOf(idx) : cur + "," + idx).apply();
    }

    private ArrayList<Long> learned(String k) {
        ArrayList<Long> out = new ArrayList<>();
        String cur = ctx.getSharedPreferences("learn", Context.MODE_PRIVATE).getString(k, "");
        for (String x : cur.split(",")) {
            if (!x.isEmpty()) out.add(Long.parseLong(x));
        }
        return out;
    }

    /** Blocos do kit de um filme, do mais importante para o menos: o que abre, o ponto de retomada, o começo. */
    private ArrayList<Long> kitBlocks(KitItem it, String k) {
        java.util.LinkedHashSet<Long> want = new java.util.LinkedHashSet<>();
        long total = (it.size + BLOCK - 1) / BLOCK;
        double perSecond = Math.max(1_000_000, it.bps) / 8.0 / BLOCK;
        long minute = Math.min(KIT_LIMIT / BLOCK, (long) Math.ceil(KIT_SECONDS * perSecond) + 1);
        for (long i : learned(k)) if (i < total) want.add(i);
        want.add(0L);
        if (total > 1) want.add(total - 1);  // índice do MKV / MP4 no fim
        if (total > 2) want.add(total - 2);
        if (it.pos > 0) {
            long r = (long) (it.pos * perSecond);
            for (long i = Math.max(0, r - 1); i <= r + minute && i < total; i++) want.add(i);
        }
        for (long i = 0; i <= minute && i < total; i++) want.add(i);
        return new ArrayList<>(want);
    }

    /**
     * Monta os kits na ordem recebida (prioridade), até KIT_LIMIT. Chamar de novo troca a fila: a página manda a
     * lista ao abrir e de novo, com o filme da frente, quando a pessoa entra na página de um filme.
     * Não baixa nada enquanto um filme toca.
     */
    void warm(final java.util.List<KitItem> items) {
        final int gen = ++warmGen;
        Thread t = new Thread(() -> {
            java.util.LinkedHashMap<String, KitItem> want = new java.util.LinkedHashMap<>();
            long budget = 0;
            outer:
            for (KitItem it : items) {
                String k = keyFor(it.url);
                for (long i : kitBlocks(it, k)) {
                    String n = k + "_" + i;
                    if (want.containsKey(n)) continue;
                    if (budget + BLOCK > KIT_LIMIT) break outer;
                    want.put(n, it);
                    budget += BLOCK;
                }
            }
            synchronized (lock) {
                pinned.clear();
                pinned.addAll(want.keySet());  // o que saiu do kit volta a ser bloco comum (pode ser apagado)
                ctx.getSharedPreferences("kit", Context.MODE_PRIVATE).edit().putStringSet("pinned", new HashSet<>(pinned)).apply();
            }
            for (Map.Entry<String, KitItem> e : want.entrySet()) {
                if (gen != warmGen) return;
                String n = e.getKey();
                synchronized (lock) {
                    if (store.containsKey(n) && new File(dir, n + ".blk").exists()) continue;
                    store.remove(n);  // apagado pelo Android: baixa de novo
                    // o filme aberto já está baixando este bloco: não baixa duas vezes
                    if (n.startsWith(key + "_") && loading.contains(Long.parseLong(n.substring(n.indexOf('_') + 1)))) continue;
                }
                try {
                    while (true) {  // espera o filme que está tocando parar
                        synchronized (lock) {
                            if (!running || upstream == null) break;
                        }
                        if (gen != warmGen) return;
                        Thread.sleep(3000);
                    }
                    if (diskLow()) return;  // pouco espaço: o kit para por aqui
                    KitItem it = e.getValue();
                    long idx = Long.parseLong(n.substring(n.indexOf('_') + 1));
                    long from = idx * BLOCK, to = Math.min(it.size, from + BLOCK) - 1;
                    download(it.url, n.substring(0, n.indexOf('_')), idx, from, to, it.size);
                } catch (InterruptedException ie) {
                    return;
                } catch (IOException io) {
                    // PC fora do ar ou ocupado: a próxima lista tenta de novo
                }
            }
        }, "kit");
        t.setDaemon(true);
        t.setPriority(Thread.MIN_PRIORITY);
        t.start();
    }

    /** Quanto do kit de um filme já está na Mi Box (0 a 1), para a página mostrar "pronto para abrir". */
    double kitReady(KitItem it) {
        String k = keyFor(it.url);
        ArrayList<Long> list = kitBlocks(it, k);
        int have = 0;
        synchronized (lock) {
            for (long i : list) if (store.containsKey(k + "_" + i)) have++;
        }
        return list.isEmpty() ? 1 : (double) have / list.size();
    }

    // ---------- status ----------
    /** O mesmo JSON de /status, para o player da biblioteca mostrar quanto já está guardado. */
    String statusJson() {
        return status();
    }

    private String status() {
        synchronized (lock) {
            long now = System.currentTimeMillis();
            double mbps = 0;
            if (lastStatusAt > 0 && now > lastStatusAt) {
                mbps = (downloaded - lastDownloaded) * 8.0 / 1e6 / ((now - lastStatusAt) / 1000.0);
            }
            lastStatusAt = now;
            lastDownloaded = downloaded;
            double spb = 8.0 / bps;  // segundos de filme por byte
            long p = playhead / BLOCK;
            double ahead = 0, behind = 0;
            if (blocks.containsKey(p)) {
                long a = p, b = p;
                while (blocks.containsKey(a + 1)) a++;
                while (blocks.containsKey(b - 1)) b--;
                ahead = (Math.min(size, (a + 1) * BLOCK) - playhead) * spb;
                behind = (playhead - b * BLOCK) * spb;
            }
            ArrayList<Long> ids = new ArrayList<>(blocks.keySet());
            java.util.Collections.sort(ids);
            StringBuilder ranges = new StringBuilder("[");
            for (int i = 0; i < ids.size(); ) {
                int j = i;
                while (j + 1 < ids.size() && ids.get(j + 1) == ids.get(j) + 1) j++;
                if (ranges.length() > 1) ranges.append(',');
                ranges.append(String.format(Locale.US, "[%.1f,%.1f]", ids.get(i) * BLOCK * spb,
                        Math.min(size, (ids.get(j) + 1) * BLOCK) * spb));
                i = j + 1;
            }
            ranges.append(']');
            // src: qual endereço está sendo servido (o PC usa para saber se outra janela assumiu a TV)
            String src = org.json.JSONObject.quote(upstream == null ? "" : upstream);
            return String.format(Locale.US,
                    "{\"state\":\"%s\",\"src\":%s,\"pos\":%.1f,\"dur\":%.1f,\"ahead\":%.1f,\"behind\":%.1f,"
                            + "\"cachedMb\":%d,\"limitMb\":%d,\"kitMb\":%d,\"kitWantMb\":%d,\"mbps\":%.1f,\"loading\":%d,\"ranges\":%s}",
                    upstream == null ? "idle" : (running ? "ok" : "paused"), src, playhead * spb, size > 0 ? size * spb : 0,
                    ahead, behind, cachedBytes() >> 20, LIMIT >> 20, kitHave() * (BLOCK >> 20), pinned.size() * (BLOCK >> 20),
                    mbps, loading.size(), ranges);
        }
    }
}
