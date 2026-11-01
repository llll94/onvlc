package app.onvlc.tv;

import android.content.Context;
import android.content.SharedPreferences;

import java.io.InputStream;
import java.net.HttpURLConnection;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.URL;
import java.util.Collections;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * Acha o PC com a biblioteca do onVLC (porta 8780): primeiro no último endereço que funcionou,
 * depois perguntando para cada endereço da rede de casa (x.x.x.1 a 254) ao mesmo tempo.
 *
 * Com o PC desligado a página continua pedindo a lista; o último endereço é testado a cada pedido, mas a
 * varredura da rede inteira só se repete com intervalo crescente (30 s, 1 min, 2 min... até 5 min).
 *
 * A biblioteca conta o MAC da placa de rede do PC no /api/hello; ele fica guardado para o botão "Ligar o PC"
 * mandar o pacote de acordar (Wake-on-LAN) quando o PC estiver dormindo ou desligado.
 *
 * Só vale o PC que sabe a chave: o assistente do PC entrega a chave da biblioteca pela PairActivity (que só o adb
 * abre) e ela fica guardada aqui. Ao achar um candidato, a TV manda um número aleatório (nonce) e confere a
 * resposta HMAC-SHA256(chave, "hello:" + nonce): outro aparelho da rede que finja ser o onVLC não sabe a chave e é
 * ignorado. Todo pedido ao PC leva a chave no cabeçalho X-onVLC-Key (auth()). Sem chave (TV nunca configurada),
 * nenhum PC é aceito: a tela pede para configurar pelo onVLC do PC.
 */
final class PcFinder {
    static final int PORT = 8780;
    private static volatile String base;  // "http://192.168.1.69:8780"
    private static volatile boolean searching;
    private static long lastScan, scanGap = 30_000;

    static String base() {
        return base;
    }

    private static volatile String key;  // a chave da biblioteca do PC (64 letras hex), guardada no pareamento

    static String key(Context ctx) {
        if (key == null) key = ctx.getSharedPreferences("pc", Context.MODE_PRIVATE).getString("key", "");
        return key;
    }

    static boolean paired(Context ctx) {
        return !key(ctx).isEmpty();
    }

    /** Põe a chave no pedido para o PC. */
    static void auth(HttpURLConnection c) {
        String k = key;
        if (k != null && !k.isEmpty()) c.setRequestProperty("X-onVLC-Key", k);
    }

    /**
     * O assistente do PC (pela PairActivity, só adb) disse onde o PC está ("http://IP:8780") e qual é a chave.
     * Só aceita um IPv4 da rede local, na mesma rede da TV (x.x.x.*): nunca um nome, a própria TV ou a internet.
     * Devolve false se o endereço ou a chave não servem.
     */
    static boolean use(Context ctx, String pcUrl, String newKey) {
        String host = pcUrl == null ? null : android.net.Uri.parse(pcUrl).getHost();
        if (host == null || !host.matches("\\d{1,3}(\\.\\d{1,3}){3}")) return false;
        String prefix = prefix();
        try {
            InetAddress a = InetAddress.getByName(host);  // IPv4 literal: não consulta DNS
            if (!a.isSiteLocalAddress() || prefix == null || !host.startsWith(prefix)) return false;
        } catch (Exception e) {
            return false;
        }
        if (newKey == null || !newKey.matches("[0-9a-f]{64}")) return false;
        ctx.getSharedPreferences("pc", Context.MODE_PRIVATE).edit().putString("ip", host).putString("key", newKey).commit();
        key = newKey;
        synchronized (PcFinder.class) {
            lastScan = 0;
            scanGap = 30_000;
        }
        if (base != null && !base.equals(url(host))) base = null;
        search(ctx);
        return true;
    }

    /** O PC parou de responder: procura de novo em segundo plano. */
    static void lost(Context ctx) {
        search(ctx);
    }

    static void search(final Context ctx) {
        synchronized (PcFinder.class) {
            if (searching) return;
            searching = true;
        }
        Thread t = new Thread(() -> {
            try {
                String found = find(ctx.getApplicationContext());
                if (found != null) base = found;
            } finally {
                searching = false;
            }
        }, "pc-finder");
        t.setDaemon(true);
        t.start();
    }

    private static String find(Context ctx) {
        SharedPreferences prefs = ctx.getSharedPreferences("pc", Context.MODE_PRIVATE);
        if (!paired(ctx)) return null;  // sem chave não há como reconhecer o PC: espera o assistente
        String last = prefs.getString("ip", null);
        if (last != null && hello(ctx, last, 1500)) {
            scanGap = 30_000;
            return url(last);
        }
        long now = System.currentTimeMillis();
        if (now - lastScan < scanGap) return null;
        lastScan = now;
        scanGap = Math.min(scanGap * 2, 300_000);  // não achou desta vez: a próxima varredura espera mais
        String prefix = prefix();
        if (prefix == null) return null;
        final AtomicReference<String> hit = new AtomicReference<>();
        ExecutorService pool = Executors.newFixedThreadPool(48);
        for (int i = 1; i < 255; i++) {
            final String ip = prefix + i;
            pool.execute(() -> {
                if (hit.get() == null && hello(ctx, ip, 800)) hit.compareAndSet(null, ip);
            });
        }
        pool.shutdown();
        try {
            pool.awaitTermination(15, TimeUnit.SECONDS);
        } catch (InterruptedException e) {
            // segue com o que achou
        }
        String ip = hit.get();
        if (ip == null) return null;
        scanGap = 30_000;
        prefs.edit().putString("ip", ip).apply();
        return url(ip);
    }

    private static String url(String ip) {
        return "http://" + ip + ":" + PORT;
    }

    private static final SecureRandom RNG = new SecureRandom();

    /** É o PC de verdade? Pergunta com um nonce e confere a resposta com a chave. */
    static boolean hello(Context ctx, String ip, int timeout) {
        String k = key(ctx);
        if (k.isEmpty()) return false;
        HttpURLConnection c = null;
        try {
            byte[] nb = new byte[16];
            RNG.nextBytes(nb);
            String nonce = hex(nb);
            c = (HttpURLConnection) new URL(url(ip) + "/api/hello?nonce=" + nonce).openConnection();
            c.setConnectTimeout(timeout);
            c.setReadTimeout(timeout + 1000);
            if (c.getResponseCode() != 200) return false;
            byte[] buf = new byte[1024];
            InputStream in = c.getInputStream();
            int n = 0, r;
            while (n < buf.length && (r = in.read(buf, n, buf.length - n)) > 0) n += r;
            org.json.JSONObject o = new org.json.JSONObject(new String(buf, 0, n, "UTF-8"));
            if (!"onvlc-library".equals(o.optString("app"))) return false;
            byte[] want = hmac(k, "hello:" + nonce).getBytes("UTF-8");
            if (!MessageDigest.isEqual(want, o.optString("auth", "").getBytes("UTF-8"))) return false;  // não sabe a chave
            String mac = o.optString("mac", "");  // o MAC da placa do PC, para acordar ele depois
            if (mac.matches("([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}")) {
                ctx.getSharedPreferences("pc", Context.MODE_PRIVATE).edit().putString("mac", mac).apply();
            }
            return true;
        } catch (Exception e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    static String hmac(String k, String text) throws Exception {
        javax.crypto.Mac m = javax.crypto.Mac.getInstance("HmacSHA256");
        m.init(new javax.crypto.spec.SecretKeySpec(k.getBytes("UTF-8"), "HmacSHA256"));
        return hex(m.doFinal(text.getBytes("UTF-8")));
    }

    static String hex(byte[] b) {
        StringBuilder sb = new StringBuilder(b.length * 2);
        for (byte x : b) sb.append(String.format("%02x", x & 0xff));
        return sb.toString();
    }

    /** Já sabe o MAC do PC (achou a biblioteca pelo menos uma vez)? */
    static boolean canWake(Context ctx) {
        return ctx.getSharedPreferences("pc", Context.MODE_PRIVATE).getString("mac", null) != null;
    }

    /**
     * Manda o pacote de acordar (Wake-on-LAN: 6 bytes FF e o MAC 16 vezes) para a rede inteira e para o último IP,
     * algumas vezes (no Wi-Fi um pacote às vezes se perde). Depois volta a procurar o PC sem esperar a fila.
     */
    static void wake(final Context ctx) {
        final SharedPreferences prefs = ctx.getSharedPreferences("pc", Context.MODE_PRIVATE);
        final String mac = prefs.getString("mac", null), last = prefs.getString("ip", null);
        if (mac == null) return;
        Thread t = new Thread(() -> {
            try {
                String[] hex = mac.split("[:-]");
                byte[] pkt = new byte[6 + 16 * 6];
                for (int i = 0; i < 6; i++) pkt[i] = (byte) 0xff;
                for (int r = 0; r < 16; r++) {
                    for (int i = 0; i < 6; i++) pkt[6 + r * 6 + i] = (byte) Integer.parseInt(hex[i], 16);
                }
                String prefix = prefix();
                java.util.ArrayList<String> dst = new java.util.ArrayList<>();
                dst.add("255.255.255.255");
                if (prefix != null) dst.add(prefix + "255");
                if (last != null) dst.add(last);
                try (java.net.DatagramSocket s = new java.net.DatagramSocket()) {
                    s.setBroadcast(true);
                    for (int round = 0; round < 3; round++) {
                        for (String d : dst) {
                            for (int port : new int[]{9, 7}) {
                                s.send(new java.net.DatagramPacket(pkt, pkt.length, InetAddress.getByName(d), port));
                            }
                        }
                        Thread.sleep(300);
                    }
                }
            } catch (Exception e) {
                // sem rede: o botão pode ser apertado de novo
            }
            synchronized (PcFinder.class) {
                lastScan = 0;
                scanGap = 30_000;
            }
        }, "pc-wake");
        t.setDaemon(true);
        t.start();
    }

    /** "192.168.1." a partir do endereço da própria Mi Box na rede. */
    private static String prefix() {
        try {
            for (NetworkInterface ni : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!ni.isUp() || ni.isLoopback()) continue;
                for (InetAddress a : Collections.list(ni.getInetAddresses())) {
                    if (a instanceof Inet4Address && a.isSiteLocalAddress()) {
                        String ip = a.getHostAddress();
                        return ip.substring(0, ip.lastIndexOf('.') + 1);
                    }
                }
            }
        } catch (Exception e) {
            // sem rede
        }
        return null;
    }
}
