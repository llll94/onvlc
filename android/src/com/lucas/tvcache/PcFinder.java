package com.lucas.tvcache;

import android.content.Context;
import android.content.SharedPreferences;

import java.io.InputStream;
import java.net.HttpURLConnection;
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
 */
final class PcFinder {
    static final int PORT = 8780;
    private static volatile String base;  // "http://192.168.1.69:8780"
    private static volatile boolean searching;
    private static long lastScan, scanGap = 30_000;

    static String base() {
        return base;
    }

    /** O assistente do PC abriu o app dizendo onde o PC está ("http://IP:8780"): usa esse endereço, sem varrer a rede. */
    static void use(Context ctx, String pcUrl) {
        String host = android.net.Uri.parse(pcUrl).getHost();
        if (host == null) return;
        ctx.getSharedPreferences("pc", Context.MODE_PRIVATE).edit().putString("ip", host).commit();
        synchronized (PcFinder.class) {
            lastScan = 0;
            scanGap = 30_000;
        }
        if (base != null && !base.equals(url(host))) base = null;
        search(ctx);
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

    static boolean hello(Context ctx, String ip, int timeout) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(url(ip) + "/api/hello").openConnection();
            c.setConnectTimeout(timeout);
            c.setReadTimeout(timeout + 1000);
            if (c.getResponseCode() != 200) return false;
            byte[] buf = new byte[512];
            InputStream in = c.getInputStream();
            int n = in.read(buf);
            String body = n > 0 ? new String(buf, 0, n, "UTF-8") : "";
            if (!body.contains("onvlc-library")) return false;
            try {  // o MAC da placa do PC, para acordar ele depois
                String mac = new org.json.JSONObject(body).optString("mac", "");
                if (mac.matches("([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}")) {
                    ctx.getSharedPreferences("pc", Context.MODE_PRIVATE).edit().putString("mac", mac).apply();
                }
            } catch (Exception e) {
                // biblioteca antiga, sem MAC
            }
            return true;
        } catch (Exception e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
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
