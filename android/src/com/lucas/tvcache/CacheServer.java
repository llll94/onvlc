package com.lucas.tvcache;

import android.content.Context;
import android.content.Intent;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.File;
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
 * O que já baixou fica guardado até 2 GB; passando disso, sai o bloco usado há mais tempo
 * (nunca um da janela atual).
 *
 * Rotas: /stream/<nome> (o vídeo, com Range), /status (JSON da janela), /stop.
 */
final class CacheServer {
    static final int PORT = 8090;
    static final long BLOCK = 8L << 20;
    static final long LIMIT = 2L << 30;
    static final int AHEAD_S = 300, BEHIND_S = 60;
    static final int WORKERS = 2;

    private static CacheServer instance;

    static synchronized CacheServer get(Context ctx) {
        if (instance == null) instance = new CacheServer(ctx.getApplicationContext());
        return instance;
    }

    private final Context ctx;
    private final File dir;
    private final Object lock = new Object();
    private final HashMap<Long, Long> blocks = new HashMap<>();  // bloco no disco → último uso
    private final HashSet<Long> loading = new HashSet<>();
    private ServerSocket server;
    private String upstream, key = "", contentType = "video/x-matroska";
    private long size = -1, bps = 16_000_000L, playhead, urgent = -1, cachedBytes;
    private long downloaded, lastDownloaded, lastStatusAt;
    private boolean running;
    private int workersAlive;

    private CacheServer(Context ctx) {
        this.ctx = ctx;
        dir = new File(ctx.getCacheDir(), "blocks");
        dir.mkdirs();
    }

    /** Prepara um vídeo. Se for o mesmo de antes (mesmo nome e tamanho), aproveita os blocos guardados. */
    void open(String url, long bitsPerSecond) throws IOException {
        // o PC manda .../v/<token>/<tamanho>/<nome>; o token muda a cada vez, tamanho + nome não
        String[] parts = url.split("/");
        // "v2": blocos guardados pela versão anterior (que podia aceitar páginas de erro) são descartados
        String newKey = Integer.toHexString(("v2:" + parts[parts.length - 2] + "/" + parts[parts.length - 1]).hashCode());
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
                cachedBytes = 0;
                File[] files = dir.listFiles();
                if (files != null) {
                    for (File f : files) {
                        String n = f.getName();
                        if (n.startsWith(key + "_") && n.endsWith(".blk")) {
                            blocks.put(Long.parseLong(n.substring(key.length() + 1, n.length() - 4)), f.lastModified());
                            cachedBytes += f.length();
                        } else {
                            f.delete();  // outro filme: libera o espaço
                        }
                    }
                }
            }
            running = true;
            lock.notifyAll();
        }
        startServer();
        startWorkers();
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
                    new Thread(() -> handle(s), "cache-conn").start();
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
        while ((c = in.read()) != -1 && c != '\n') if (c != '\r') sb.append((char) c);
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
            for (String h; (h = readLine(in)) != null && !h.isEmpty(); ) {
                if (h.toLowerCase(Locale.ROOT).startsWith("range:")) range = h.substring(6).trim();
            }
            OutputStream out = new BufferedOutputStream(sock.getOutputStream(), 256 * 1024);
            if (path.startsWith("/status")) {
                send(out, "200 OK", "application/json", status().getBytes("UTF-8"));
            } else if (path.startsWith("/stop")) {
                send(out, "200 OK", "text/plain", "ok".getBytes("UTF-8"));
                ctx.stopService(new Intent(ctx, CacheService.class));
            } else if (path.startsWith("/stream/")) {
                stream(method, range, out);
            } else {
                send(out, "404 Not Found", "text/plain", "não encontrado".getBytes("UTF-8"));
            }
            out.flush();
        } catch (Exception e) {
            // o VLC fecha a conexão quando pula para outro ponto: normal
        }
    }

    private static void send(OutputStream out, String status, String type, byte[] body) throws IOException {
        String h = "HTTP/1.1 " + status + "\r\nContent-Type: " + type + "\r\nContent-Length: " + body.length
                + "\r\nConnection: close\r\n\r\n";
        out.write(h.getBytes("ISO-8859-1"));
        out.write(body);
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
        if (start >= total) {
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
        while (pos <= end) {
            long idx = pos / BLOCK;
            File f = waitBlock(idx, pos);
            try (RandomAccessFile raf = new RandomAccessFile(f, "r")) {
                long off = pos - idx * BLOCK;
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

    /** Espera o bloco ficar pronto; quem pede um bloco que falta passa na frente da fila. */
    private File waitBlock(long idx, long pos) throws Exception {
        synchronized (lock) {
            // leituras no fim do arquivo (índice do MKV) não mudam o ponto que está tocando
            if (pos < size - 2 * BLOCK) playhead = pos;
            ensureRunning();
            lock.notifyAll();
            long t0 = System.currentTimeMillis();
            while (!blocks.containsKey(idx)) {
                urgent = idx;
                lock.notifyAll();
                lock.wait(500);
                if (System.currentTimeMillis() - t0 > 60000) throw new IOException("tempo esgotado");
            }
            if (urgent == idx) urgent = -1;
            blocks.put(idx, System.currentTimeMillis());
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
                    download(url, k, idx, from, to);
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

    /** Abre espaço para mais um bloco apagando o usado há mais tempo fora da janela atual. */
    private boolean makeRoom(long p, long ahead, long behind) {
        while (cachedBytes + BLOCK > LIMIT) {
            long victim = -1, oldest = Long.MAX_VALUE;
            for (Map.Entry<Long, Long> e : blocks.entrySet()) {
                long i = e.getKey();
                if (i >= p - behind && i <= p + ahead) continue;
                if (e.getValue() < oldest) {
                    oldest = e.getValue();
                    victim = i;
                }
            }
            if (victim < 0) return false;
            File f = new File(dir, key + "_" + victim + ".blk");
            cachedBytes -= f.length();
            f.delete();
            blocks.remove(victim);
        }
        return true;
    }

    private HttpURLConnection connect(String url) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
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

    private void download(String url, String k, long idx, long from, long to) throws IOException {
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
        if (cr == null || !cr.endsWith("/" + size)) {
            c.disconnect();
            throw new IOException("pedaço de outro arquivo: " + cr);
        }
        try (InputStream in = c.getInputStream(); OutputStream out = new FileOutputStream(tmp)) {
            byte[] buf = new byte[256 * 1024];
            long got = 0;
            int r;
            while ((r = in.read(buf)) > 0) {
                out.write(buf, 0, r);
                got += r;
                synchronized (lock) {
                    downloaded += r;
                }
            }
            if (got != to - from + 1) throw new IOException("bloco incompleto");
        } catch (IOException e) {
            tmp.delete();
            throw e;
        } finally {
            c.disconnect();
        }
        synchronized (lock) {
            if (!k.equals(key) || !tmp.renameTo(dst)) {  // trocou de filme no meio do download
                tmp.delete();
                return;
            }
            blocks.put(idx, System.currentTimeMillis());
            cachedBytes += dst.length();
            lock.notifyAll();
        }
    }

    // ---------- status ----------
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
            String src = upstream == null ? "" : upstream.replace("\\", "\\\\").replace("\"", "\\\"");
            return String.format(Locale.US,
                    "{\"state\":\"%s\",\"src\":\"%s\",\"pos\":%.1f,\"dur\":%.1f,\"ahead\":%.1f,\"behind\":%.1f,"
                            + "\"cachedMb\":%d,\"limitMb\":%d,\"mbps\":%.1f,\"loading\":%d,\"ranges\":%s}",
                    upstream == null ? "idle" : (running ? "ok" : "paused"), src, playhead * spb, size > 0 ? size * spb : 0,
                    ahead, behind, cachedBytes >> 20, LIMIT >> 20, mbps, loading.size(), ranges);
        }
    }
}
