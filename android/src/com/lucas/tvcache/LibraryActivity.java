package com.lucas.tvcache;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.KeyEvent;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * A biblioteca na tela inicial da Mi Box: uma página (pasta tvui do PC) dentro de um WebView.
 * A página passa pelo servidor local do cache (127.0.0.1:8090/ui/), que guarda uma cópia para
 * mostrar a última lista mesmo com o PC desligado. Tocar abre o VLC e, quando ele fecha,
 * a posição volta para o PC (a mesma do onVLC de lá).
 */
public class LibraryActivity extends Activity {
    private static final int REQ_VLC = 1;
    private WebView web;
    private String playingId;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        PcFinder.search(this);
        try {
            CacheServer.get(this).startUi();
        } catch (Exception e) {
            // a porta já está aberta por este mesmo app
        }
        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#0b0b0c"));
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        web.setWebViewClient(new WebViewClient());
        web.addJavascriptInterface(new Bridge(), "onvlc");
        setContentView(web);
        web.requestFocus();
        // espera um instante pela busca do PC para a primeira tela já vir com a lista nova
        web.postDelayed(() -> web.loadUrl("http://127.0.0.1:" + CacheServer.PORT + "/ui/index.html"), 1200);
    }

    @Override
    public void onBackPressed() {
        web.evaluateJavascript("window.tvBack ? tvBack() : false", v -> {
            if (!"true".equals(v)) finish();
        });
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent e) {
        // o botão "voltar" do controle chega aqui antes do WebView
        if (e.getKeyCode() == KeyEvent.KEYCODE_BACK) {
            if (e.getAction() == KeyEvent.ACTION_UP) onBackPressed();
            return true;
        }
        return super.dispatchKeyEvent(e);
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (web != null) web.evaluateJavascript("window.tvRefresh && tvRefresh()", null);
    }

    private final class Bridge {
        @JavascriptInterface
        public void play(final String json) {
            runOnUiThread(() -> startPlay(json));
        }
    }

    private void startPlay(String json) {
        try {
            JSONObject o = new JSONObject(json);
            String base = PcFinder.base();
            if (base == null) {
                web.evaluateJavascript("toast('Não achei o PC na rede. Ele está ligado com o onVLC aberto?')", null);
                PcFinder.lost(this);
                return;
            }
            String upstream = base + "/v/" + o.getString("id") + "/" + o.getLong("size") + "/" + Uri.encode(o.getString("file"));
            CacheServer.get(this).open(upstream, o.optLong("bps", 0));
            startForegroundService(new Intent(this, CacheService.class));
            // código novo a cada vez: o VLC respeita a posição pedida (num endereço conhecido ele retoma sozinho)
            String session = Long.toHexString(System.nanoTime());
            Uri target = Uri.parse("http://127.0.0.1:" + CacheServer.PORT + "/stream/" + session + "/" + Uri.encode(o.getString("file")));
            Intent vlc = new Intent(Intent.ACTION_VIEW);
            vlc.setClassName("org.videolan.vlc", "org.videolan.vlc.gui.video.VideoPlayerActivity");
            vlc.setDataAndType(target, "video/*");
            vlc.putExtra("position", o.optLong("pos", 0));
            vlc.putExtra("title", o.optString("title", ""));
            playingId = o.getString("id");
            startActivityForResult(vlc, REQ_VLC);
        } catch (Exception e) {
            web.evaluateJavascript("toast('Não consegui abrir o VLC')", null);
        }
    }

    @Override
    protected void onActivityResult(int req, int result, Intent data) {
        super.onActivityResult(req, result, data);
        if (req != REQ_VLC || playingId == null) return;
        final String id = playingId;
        playingId = null;
        long pos = data != null ? data.getLongExtra("extra_position", -1) : -1;
        long dur = data != null ? data.getLongExtra("extra_duration", 0) : 0;
        stopService(new Intent(this, CacheService.class));
        if (pos < 0) return;
        final double posS = pos / 1000.0, durS = dur / 1000.0;
        final String base = PcFinder.base();
        if (base == null) return;
        new Thread(() -> {
            try {
                HttpURLConnection c = (HttpURLConnection) new URL(base + "/api/progress").openConnection();
                c.setConnectTimeout(3000);
                c.setReadTimeout(5000);
                c.setRequestMethod("POST");
                c.setDoOutput(true);
                c.setRequestProperty("Content-Type", "application/json");
                byte[] body = new JSONObject().put("id", id).put("pos", posS).put("dur", durS).toString().getBytes("UTF-8");
                try (OutputStream out = c.getOutputStream()) {
                    out.write(body);
                }
                c.getResponseCode();
                c.disconnect();
            } catch (Exception e) {
                // o PC desligou no meio: a posição fica só no VLC da TV
            }
            runOnUiThread(() -> web.evaluateJavascript("window.tvRefresh && tvRefresh()", null));
        }, "progress").start();
    }
}
