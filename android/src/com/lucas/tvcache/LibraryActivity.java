package com.lucas.tvcache;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;

import org.json.JSONObject;

/**
 * A biblioteca na tela inicial da Mi Box: uma página (pasta tvui do PC) dentro de um WebView.
 * A página passa pelo servidor local do cache (127.0.0.1:8090/ui/), que guarda uma cópia para
 * mostrar a última lista mesmo com o PC desligado.
 *
 * O filme toca aqui mesmo (VideoPlayer, motor do VLC) numa superfície atrás da página; a página
 * fica transparente e desenha a barra e os menus por cima. A posição vai para o PC a cada 10 s e ao parar.
 *
 * "Assistir na TV" do PC abre esta tela pelo adb com extras: play (id do vídeo) + pos (ms), ou stop.
 * O assistente "Configurar a TV" do PC abre com pc ("http://IP:8780": onde o PC está, sem procurar) e selftest (toca o
 * vídeo de teste e conta o resultado). Os dois também valem pelo link onvlc://abrir?pc=...&selftest=1.
 */
public class LibraryActivity extends Activity implements VideoPlayer.Listener {
    private WebView web;
    private VideoPlayer player;
    private String base;  // o PC do filme que está tocando
    private double lastSent = -1;
    private boolean pageReady;
    private JSONObject pendingPlay;  // pedido do PC esperando a página carregar
    private final Handler ui = new Handler(Looper.getMainLooper());
    // modo teste
    private boolean testing, testPlaying, testBuffering;
    private int testStalls;
    private double testMax;
    private String testBase;
    private JSONObject testRes;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        WebView.setWebContentsDebuggingEnabled(true);  // o PC mexe na página pelo adb (tools\tvjs.py)
        PcFinder.search(this);
        try {
            CacheServer.get(this).startUi();
        } catch (Exception e) {
            // a porta já está aberta por este mesmo app
        }
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.parseColor("#0b0b0c"));
        web = new WebView(this);
        web.setBackgroundColor(Color.TRANSPARENT);  // o vídeo aparece por baixo quando a página deixa
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView v, String url) {
                pageReady = true;
                deliverPlay();
            }
        });
        web.addJavascriptInterface(new Bridge(), "onvlc");
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));
        player = new VideoPlayer(this, root, this);
        setContentView(root);
        web.requestFocus();
        // abre na hora com a última lista guardada; quando o PC é achado, a página pede a lista nova sozinha
        web.loadUrl("http://127.0.0.1:" + CacheServer.PORT + "/ui/index.html");
        handleIntent(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleIntent(intent);
    }

    /** Pedidos do PC: onde ele está, teste, tocar um vídeo da biblioteca num ponto, ou parar o que está tocando. */
    private void handleIntent(Intent i) {
        if (i == null) return;
        Uri link = i.getData();
        if (link != null && "onvlc".equals(link.getScheme())) {
            i.setData(null);
            if (link.getQueryParameter("pc") != null) PcFinder.use(this, link.getQueryParameter("pc"));
            if ("1".equals(link.getQueryParameter("selftest"))) startSelfTest();
            return;
        }
        String pc = i.getStringExtra("pc");
        if (pc != null) {
            i.removeExtra("pc");
            PcFinder.use(this, pc);
        }
        if (i.getBooleanExtra("selftest", false)) {
            i.removeExtra("selftest");
            startSelfTest();
            return;
        }
        if (i.getBooleanExtra("stop", false)) {
            i.removeExtra("stop");
            if (player.active()) {
                player.stop(true);
                web.evaluateJavascript("window.onPlayerClosed && onPlayerClosed()", null);
            }
            return;
        }
        String id = i.getStringExtra("play");
        if (id == null) return;
        i.removeExtra("play");
        try {
            pendingPlay = new JSONObject().put("id", id).put("pos", i.getLongExtra("pos", 0) / 1000.0);
        } catch (Exception e) {
            return;
        }
        deliverPlay();
    }

    /** Entrega o pedido à página (que espera o vídeo aparecer na lista antes de tocar). */
    private void deliverPlay() {
        if (!pageReady || pendingPlay == null) return;
        final JSONObject req = pendingPlay;
        web.evaluateJavascript("window.tvPlayId ? tvPlayId(" + req + ") : false", v -> {
            if ("true".equals(v) && pendingPlay == req) pendingPlay = null;
        });
    }

    // ---------- modo teste ----------
    /** Mede a rede, lê o que a TV suporta e toca 10 s do vídeo de teste do PC, com a página escondida. */
    private void startSelfTest() {
        if (testing) return;
        if (player.active()) {
            player.stop(true);
            web.evaluateJavascript("window.onPlayerClosed && onPlayerClosed()", null);
        }
        testing = true;
        new Thread(() -> {
            String b = null;
            for (int i = 0; i < 30 && (b = PcFinder.base()) == null; i++) {  // acabou de saber onde o PC está
                PcFinder.search(this);
                try {
                    Thread.sleep(500);
                } catch (InterruptedException e) {
                    return;
                }
            }
            if (b == null) {
                runOnUiThread(() -> testing = false);
                return;
            }
            JSONObject r = SelfTest.caps(this);
            try {
                r.put("speed", Math.round(SelfTest.speed(b) * 10) / 10.0).put("done", false);
            } catch (Exception e) {
                // segue
            }
            SelfTest.post(b, r);
            final String base0 = b;
            runOnUiThread(() -> {
                if (!testing) return;
                testBase = base0;
                testRes = r;
                testMax = 0;
                testStalls = 0;
                testPlaying = false;
                testBuffering = false;
                web.setVisibility(View.INVISIBLE);
                getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                player.open("selftest", base0 + "/api/selftest", 0, new String[0]);
                // segurança: o vídeo não andou. Folgado: na 1ª vez depois de instalar, o motor monta a lista de fontes
                // do sistema antes de mostrar o primeiro quadro (35 s na Mi Box)
                ui.postDelayed(this::finishSelfTest, 120_000);
            });
        }, "selftest").start();
    }

    private void selfTestState(JSONObject st) {
        String s = st.optString("state");
        boolean buf = st.optDouble("buffering", 100) < 100;
        if ("playing".equals(s)) testPlaying = true;
        if (testPlaying && buf && !testBuffering) testStalls++;  // travou depois de começar
        testBuffering = buf;
        testMax = Math.max(testMax, st.optDouble("time", 0));
        if (testMax >= 9.5 || "ended".equals(s)) finishSelfTest();
    }

    private void finishSelfTest() {
        if (!testing) return;
        testing = false;
        ui.removeCallbacksAndMessages(null);
        player.stop(false);
        web.setVisibility(View.VISIBLE);
        web.requestFocus();
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        final JSONObject r = testRes;
        final String b = testBase;
        if (r == null || b == null) return;
        try {
            r.put("played", Math.round(testMax * 10) / 10.0).put("stalls", Math.max(0, testStalls)).put("done", true);
        } catch (Exception e) {
            // segue
        }
        new Thread(() -> SelfTest.post(b, r), "selftest-post").start();
    }

    @Override
    public void onBackPressed() {
        if (testing) {
            finishSelfTest();
            return;
        }
        web.evaluateJavascript("window.tvBack ? tvBack() : false", v -> {
            if (!"true".equals(v)) finish();
        });
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent e) {
        int k = e.getKeyCode();
        // o botão "voltar" do controle chega aqui antes do WebView
        if (k == KeyEvent.KEYCODE_BACK) {
            if (e.getAction() == KeyEvent.ACTION_UP) onBackPressed();
            return true;
        }
        // botões de mídia do controle viram teclas que a página entende
        if (player.active() && (k == KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE || k == KeyEvent.KEYCODE_MEDIA_PLAY
                || k == KeyEvent.KEYCODE_MEDIA_PAUSE)) {
            if (e.getAction() == KeyEvent.ACTION_UP) player.toggle();
            return true;
        }
        return super.dispatchKeyEvent(e);
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (web != null && !player.active()) web.evaluateJavascript("window.tvRefresh && tvRefresh()", null);
    }

    @Override
    protected void onPause() {
        // saiu para a tela inicial no meio do filme: para e guarda a posição
        if (player.active()) {
            player.stop(true);
            web.evaluateJavascript("window.onPlayerClosed && onPlayerClosed()", null);
        }
        player.stop(false);  // filme preparado (pausado atrás da página) sem ter começado
        CacheServer.get(this).pause();  // e o cache para de baixar o filme que ficou para trás
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        player.release();
        super.onDestroy();
    }

    private final class Bridge {
        @JavascriptInterface
        public void play(final String json) {
            runOnUiThread(() -> startPlay(json, false));
        }

        @JavascriptInterface
        public void toggle() {
            runOnUiThread(() -> player.toggle());
        }

        @JavascriptInterface
        public void seek(final double sec) {
            runOnUiThread(() -> player.seek((long) (sec * 1000)));
        }

        @JavascriptInterface
        public void audio(final int track) {
            runOnUiThread(() -> player.setAudio(track));
        }

        @JavascriptInterface
        public void spu(final int track) {
            runOnUiThread(() -> player.setSpu(track));
        }

        @JavascriptInterface
        public void stop() {
            runOnUiThread(() -> player.stop(true));
        }

        /** A pessoa entrou na página do filme: abre pausado, escondido, para o "Assistir" ser imediato. */
        @JavascriptInterface
        public void prepare(final String json) {
            runOnUiThread(() -> startPlay(json, true));
        }

        /** Saiu da página sem assistir. */
        @JavascriptInterface
        public void unprepare() {
            runOnUiThread(() -> {
                if (!player.active()) {
                    player.stop(false);
                    CacheServer.get(LibraryActivity.this).pause();
                }
            });
        }

        /** O botão "Ligar o PC" aparece só quando o app já sabe o MAC do PC. */
        @JavascriptInterface
        public boolean canWakePc() {
            return PcFinder.canWake(LibraryActivity.this);
        }

        /** Acorda o PC pela rede (Wake-on-LAN). */
        @JavascriptInterface
        public void wakePc() {
            PcFinder.wake(LibraryActivity.this);
        }

        /** Manda um pedido para a biblioteca do PC. */
        @JavascriptInterface
        public void post(final String path, final String json) {
            final String b = PcFinder.base();
            if (b == null || !path.startsWith("/api/")) return;
            new Thread(() -> {
                try {
                    java.net.HttpURLConnection c = (java.net.HttpURLConnection) new java.net.URL(b + path).openConnection();
                    c.setConnectTimeout(3000);
                    c.setReadTimeout(5000);
                    c.setRequestMethod("POST");
                    c.setDoOutput(true);
                    c.setRequestProperty("Content-Type", "application/json");
                    try (java.io.OutputStream out = c.getOutputStream()) {
                        out.write(json.getBytes("UTF-8"));
                    }
                    c.getResponseCode();
                    c.disconnect();
                } catch (Exception e) {
                    // PC fora do ar
                }
            }, "post").start();
        }

        /** Kits de abertura: lista de filmes em ordem de prioridade ([{id,size,file,bps,pos}]). */
        @JavascriptInterface
        public void warm(final String json) {
            String b = PcFinder.base();
            if (b == null) return;
            try {
                org.json.JSONArray a = new org.json.JSONArray(json);
                java.util.ArrayList<CacheServer.KitItem> items = new java.util.ArrayList<>();
                for (int i = 0; i < a.length(); i++) {
                    JSONObject o = a.getJSONObject(i);
                    CacheServer.KitItem it = new CacheServer.KitItem();
                    it.url = b + "/v/" + o.getString("id") + "/" + o.getLong("size") + "/" + Uri.encode(o.getString("file"));
                    it.size = o.getLong("size");
                    it.bps = o.optLong("bps", 0);
                    it.pos = o.optDouble("pos", 0);
                    items.add(it);
                }
                CacheServer.get(LibraryActivity.this).warm(items);
            } catch (Exception e) {
                // lista inválida: ignora
            }
        }
    }

    private void startPlay(String json, boolean prepareOnly) {
        try {
            JSONObject o = new JSONObject(json);
            if (!prepareOnly && player.preparedFor(o.getString("id"), o.optLong("pos", 0))) {
                // já está aberto e pausado no ponto certo: só soltar
                startForegroundService(new Intent(this, CacheService.class));
                getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                lastSent = -1;
                player.start();
                return;
            }
            if (prepareOnly && player.active()) return;  // um filme tocando: não mexe
            base = PcFinder.base();
            if (base == null) {
                web.evaluateJavascript("toast('Não achei o PC na rede. Ele está ligado com o onVLC aberto?')", null);
                PcFinder.lost(this);
                return;
            }
            String upstream = base + "/v/" + o.getString("id") + "/" + o.getLong("size") + "/" + Uri.encode(o.getString("file"));
            CacheServer.get(this).open(upstream, o.optLong("bps", 0));
            String session = Long.toHexString(System.nanoTime());
            String local = "http://127.0.0.1:" + CacheServer.PORT + "/stream/" + session + "/" + Uri.encode(o.getString("file"));
            if (!prepareOnly) {
                startForegroundService(new Intent(this, CacheService.class));
                getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            }
            lastSent = -1;
            String[] subs = new String[o.optInt("subs", 0)];
            for (int i = 0; i < subs.length; i++) subs[i] = base + "/sub/" + o.getString("id") + "/" + i;
            if (prepareOnly) player.prepare(o.getString("id"), local, o.optLong("pos", 0), subs);
            else player.open(o.getString("id"), local, o.optLong("pos", 0), subs);
        } catch (Exception e) {
            web.evaluateJavascript("toast('Não consegui abrir o filme')", null);
        }
    }

    // ---------- VideoPlayer.Listener ----------
    @Override
    public void onState(JSONObject st) {
        if (testing) {
            selfTestState(st);
            return;
        }
        // o estado do cache já vem em JSON: entra no texto como está (sem ler e escrever de novo a cada meio segundo)
        String js = st.toString();
        try {
            js = js.substring(0, js.length() - 1) + ",\"cache\":" + CacheServer.get(this).statusJson() + "}";
        } catch (Exception e) {
            // sem cache: só o estado do player
        }
        web.evaluateJavascript("window.onPlayer && onPlayer(" + js + ")", null);
        double t = st.optDouble("time", 0);
        if ("playing".equals(st.optString("state")) && Math.abs(t - lastSent) >= 10 && base != null) {
            lastSent = t;
            Progress.post(base, st.optString("id"), t, st.optDouble("length", 0));
        }
    }

    @Override
    public void onStopped(String id, double pos, double dur, boolean ended) {
        if ("selftest".equals(id)) {
            finishSelfTest();
            return;
        }
        getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        stopService(new Intent(this, CacheService.class));
        final String b = base;
        if (b != null) {
            new Thread(() -> {
                Progress.send(b, id, pos, dur);
                runOnUiThread(() -> web.evaluateJavascript("window.tvRefresh && tvRefresh()", null));
            }, "progress").start();
        }
    }
}
