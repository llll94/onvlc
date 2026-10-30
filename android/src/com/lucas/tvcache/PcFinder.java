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
 */
final class PcFinder {
    static final int PORT = 8780;
    private static volatile String base;  // "http://192.168.1.69:8780"
    private static volatile boolean searching;

    static String base() {
        return base;
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
        if (last != null && hello(last, 1500)) return url(last);
        String prefix = prefix();
        if (prefix == null) return null;
        final AtomicReference<String> hit = new AtomicReference<>();
        ExecutorService pool = Executors.newFixedThreadPool(48);
        for (int i = 1; i < 255; i++) {
            final String ip = prefix + i;
            pool.execute(() -> {
                if (hit.get() == null && hello(ip, 800)) hit.compareAndSet(null, ip);
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
        prefs.edit().putString("ip", ip).apply();
        return url(ip);
    }

    private static String url(String ip) {
        return "http://" + ip + ":" + PORT;
    }

    static boolean hello(String ip, int timeout) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(url(ip) + "/api/hello").openConnection();
            c.setConnectTimeout(timeout);
            c.setReadTimeout(timeout + 1000);
            if (c.getResponseCode() != 200) return false;
            byte[] buf = new byte[512];
            InputStream in = c.getInputStream();
            int n = in.read(buf);
            return n > 0 && new String(buf, 0, n, "UTF-8").contains("onvlc-library");
        } catch (Exception e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
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
