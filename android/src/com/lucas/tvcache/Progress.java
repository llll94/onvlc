package com.lucas.tvcache;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.media.MediaMetadata;
import android.media.session.MediaController;
import android.media.session.MediaSessionManager;
import android.media.session.PlaybackState;
import android.os.SystemClock;

import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Enquanto o VLC toca um filme da biblioteca, lê a posição pelo controle de mídia do Android a cada 5 s
 * e conta para o PC (a mesma posição do onVLC de lá). Quando o VLC fecha, manda a última e para o cache.
 */
final class Progress {
    private static final String VLC = "org.videolan.vlc";
    private static volatile int generation;

    static void track(final Context ctx, final String base, final String id) {
        final int gen = ++generation;
        final Context app = ctx.getApplicationContext();
        Thread t = new Thread(() -> {
            MediaSessionManager msm = app.getSystemService(MediaSessionManager.class);
            ComponentName listener = new ComponentName(app, Listener.class);
            boolean started = false;
            int missing = 0;
            double lastSent = -1, pos = 0, dur = 0;
            long t0 = SystemClock.elapsedRealtime();
            while (gen == generation) {
                try {
                    Thread.sleep(5000);
                } catch (InterruptedException e) {
                    return;
                }
                MediaController vlc = null;
                try {
                    for (MediaController c : msm.getActiveSessions(listener)) {
                        if (VLC.equals(c.getPackageName())) vlc = c;
                    }
                } catch (SecurityException e) {
                    return;  // acesso às notificações não liberado: sem posição
                }
                PlaybackState st = vlc != null ? vlc.getPlaybackState() : null;
                int s = st != null ? st.getState() : PlaybackState.STATE_NONE;
                boolean active = s == PlaybackState.STATE_PLAYING || s == PlaybackState.STATE_PAUSED
                        || s == PlaybackState.STATE_BUFFERING;
                if (active) {
                    started = true;
                    missing = 0;
                    long p = st.getPosition();
                    if (s == PlaybackState.STATE_PLAYING) {  // a posição só é gravada quando o estado muda
                        p += (long) ((SystemClock.elapsedRealtime() - st.getLastPositionUpdateTime()) * st.getPlaybackSpeed());
                    }
                    pos = p / 1000.0;
                    MediaMetadata md = vlc.getMetadata();
                    if (md != null && md.getLong(MediaMetadata.METADATA_KEY_DURATION) > 0) {
                        dur = md.getLong(MediaMetadata.METADATA_KEY_DURATION) / 1000.0;
                    }
                    if (Math.abs(pos - lastSent) >= 4) {
                        send(base, id, pos, dur);
                        lastSent = pos;
                    }
                } else if (started || SystemClock.elapsedRealtime() - t0 > 60000) {
                    // o VLC parou ou fechou (duas olhadas seguidas, para não confundir com uma troca de faixa)
                    if (++missing >= 2) break;
                }
            }
            if (gen != generation) return;  // outro filme começou: ele cuida do resto
            if (started) send(base, id, pos, dur);
            app.stopService(new Intent(app, CacheService.class));
        }, "progress");
        t.setDaemon(true);
        t.start();
    }

    private static void send(String base, String id, double pos, double dur) {
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(base + "/api/progress").openConnection();
            c.setConnectTimeout(3000);
            c.setReadTimeout(5000);
            c.setRequestMethod("POST");
            c.setDoOutput(true);
            c.setRequestProperty("Content-Type", "application/json");
            byte[] body = new JSONObject().put("id", id).put("pos", pos).put("dur", dur).toString().getBytes("UTF-8");
            try (OutputStream out = c.getOutputStream()) {
                out.write(body);
            }
            c.getResponseCode();
            c.disconnect();
        } catch (Exception e) {
            // PC fora do ar: tenta de novo na próxima
        }
    }
}
