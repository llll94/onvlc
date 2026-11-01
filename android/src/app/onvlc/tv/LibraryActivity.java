package app.onvlc.tv;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.KeyEvent;
import android.view.View;
import android.view.WindowManager;
import android.webkit.JavascriptInterface;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;
import android.widget.TextView;

import org.json.JSONObject;

/**
 * A biblioteca na tela inicial da Mi Box: uma página (pasta tvui do PC) dentro de um WebView.
 * A página passa pelo servidor local do cache (127.0.0.1:8090/ui/), que guarda uma cópia para
 * mostrar a última lista mesmo com o PC desligado.
 *
 * O filme toca aqui mesmo (VideoPlayer, motor do VLC) numa superfície atrás da página; a página
 * fica transparente e desenha a barra e os menus por cima. A posição vai para o PC a cada 10 s e ao parar.
 *
 * Pedidos do PC (tocar, parar, teste) chegam pela PairActivity, que só o adb abre; esta tela só obedece a extras que
 * trazem o token da PairActivity (ela é exportada só porque o lançador da TV precisa abrir).
 *
 * Segurança da página: o WebView só carrega o que vem do servidor local deste app (CacheServer, com um token na
 * rota); qualquer outro endereço (link, redirect, iframe) é bloqueado, porque a página tem acesso à ponte
 * window.onvlc. A depuração do WebView só liga numa build de desenvolvimento (tools\build_tvcache.py --debug).
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
        // só na build de desenvolvimento: o PC mexe na página pelo adb (tools\tvjs.py)
        WebView.setWebContentsDebuggingEnabled((getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0);
        PcFinder.key(this);
        PcFinder.search(this);
        try {
            CacheServer.get(this).startUi();
        } catch (Exception e) {
            // outro app está com a porta: não abre a página dele (ela teria a ponte deste app)
            TextView msg = new TextView(this);
            msg.setText("O onVLC não conseguiu abrir. Reinicie a TV e tente de novo.");
            msg.setTextSize(24);
            msg.setPadding(96, 96, 96, 96);
            setContentView(msg);
            return;
        }
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.parseColor("#0b0b0c"));
        web = new WebView(this);
        web.setBackgroundColor(Color.TRANSPARENT);  // o vídeo aparece por baixo quando a página deixa
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setMediaPlaybackRequiresUserGesture(false);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        web.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView v, String url) {
                pageReady = true;
                deliverPlay();
            }

            // navegação (link, redirect, location.href): só dentro da página local
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest r) {
                return !CacheServer.isLocal(r.getUrl().toString());
            }

            // qualquer outro carregamento (iframe, imagem, script, fetch de fora): bloqueado
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView v, WebResourceRequest r) {
                if (CacheServer.isLocal(r.getUrl().toString())) return null;
                return new WebResourceResponse("text/plain", "utf-8", 403, "Forbidden", null,
                        new java.io.ByteArrayInputStream(new byte[0]));
            }

            // a página travou ou foi derrubada: recria a tela em vez de fechar o app
            @Override
            public boolean onRenderProcessGone(WebView v, RenderProcessGoneDetail d) {
                recreate();
                return true;
            }
        });
        web.addJavascriptInterface(new Bridge(), "onvlc");
        root.addView(web, new FrameLayout.LayoutParams(-1, -1));
        player = new VideoPlayer(this, root, this);
        setContentView(root);
        web.requestFocus();
        // abre na hora com a última lista guardada; quando o PC é achado, a página pede a lista nova sozinha
        web.loadUrl(CacheServer.local("/ui/index.html"));
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
        // só obedece ao que veio da PairActivity (o adb do PC); outros apps podem abrir esta tela, mas não mandar nela
        if (!PairActivity.TOKEN.equals(i.getStringExtra("t"))) return;
        i.removeExtra("t");
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
                player.open("selftest", CacheServer.local("/ui/api/selftest"), 0, new String[0]);
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

        /** A TV já foi configurada pelo onVLC do PC (tem a chave)? A tela usa para explicar o que fazer. */
        @JavascriptInterface
        public boolean paired() {
            return PcFinder.paired(LibraryActivity.this);
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
            // só rotas da biblioteca, sem truques no caminho, e corpo pequeno
            if (b == null || !path.matches("/api/[a-z/]{1,40}") || json == null || json.length() > 16384) return;
            new Thread(() -> {
                try {
                    java.net.HttpURLConnection c = (java.net.HttpURLConnection) new java.net.URL(b + path).openConnection();
                    PcFinder.auth(c);
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
                for (int i = 0; i < a.length() && i < 64; i++) {
                    JSONObject o = a.getJSONObject(i);
                    String id = o.getString("id");
                    long size = o.getLong("size");
                    if (!id.matches("[0-9a-f]{1,32}") || size <= 0 || size > (2L << 40)) continue;  // até 2 TB
                    CacheServer.KitItem it = new CacheServer.KitItem();
                    it.url = b + "/v/" + id + "/" + size + "/" + Uri.encode(o.getString("file"));
                    it.size = size;
                    it.bps = Math.max(0, Math.min(400_000_000L, o.optLong("bps", 0)));  // até 400 Mb/s
                    it.pos = Math.max(0, o.optDouble("pos", 0));
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
            String id = o.getString("id");
            if (!id.matches("[0-9a-f]{1,32}")) return;
            String upstream = base + "/v/" + id + "/" + o.getLong("size") + "/" + Uri.encode(o.getString("file"));
            CacheServer.get(this).open(upstream, Math.max(0, Math.min(400_000_000L, o.optLong("bps", 0))));
            String session = Long.toHexString(System.nanoTime());
            String local = CacheServer.local("/stream/" + session + "/" + Uri.encode(o.getString("file")));
            if (!prepareOnly) {
                startForegroundService(new Intent(this, CacheService.class));
                getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
            }
            lastSent = -1;
            // legendas pelo servidor local (ele põe a chave no pedido ao PC; o motor de vídeo não sabe pôr)
            String[] subs = new String[Math.max(0, Math.min(32, o.optInt("subs", 0)))];
            for (int i = 0; i < subs.length; i++) subs[i] = CacheServer.local("/ui/sub/" + id + "/" + i);
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
