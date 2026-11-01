package app.onvlc.tv;

import android.app.Activity;
import android.graphics.PixelFormat;
import android.net.Uri;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.SurfaceView;
import android.view.View;
import android.widget.FrameLayout;

import org.json.JSONArray;
import org.json.JSONObject;
import org.videolan.libvlc.LibVLC;
import org.videolan.libvlc.Media;
import org.videolan.libvlc.MediaPlayer;
import org.videolan.libvlc.interfaces.IMedia;
import org.videolan.libvlc.interfaces.IVLCVout;

import java.util.ArrayList;

/**
 * O player dentro do app: o motor do VLC (libVLC) desenhando numa superfície atrás da página da biblioteca.
 * A página (tvui) desenha a barra, os menus e o resto por cima, com fundo transparente, e manda os comandos
 * por window.onvlc; o estado volta para ela a cada meio segundo por window.onPlayer(...).
 */
final class VideoPlayer implements MediaPlayer.EventListener, IVLCVout.OnNewVideoLayoutListener {
    interface Listener {
        void onState(JSONObject state);

        void onStopped(String id, double pos, double dur, boolean ended);
    }

    private final Activity act;
    private final Listener listener;
    private final FrameLayout box;
    private final SurfaceView video, subs;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private LibVLC lib;
    private MediaPlayer mp;
    private String id;
    private String state = "idle";
    private float buffering = 100;
    private int videoW, videoH, sarNum = 1, sarDen = 1;
    private boolean ended, prepared;
    private long preparedAt;
    private Thread warmer;  // o motor (38 MB de biblioteca nativa) começa a carregar quando o app abre
    private JSONArray audioJson = new JSONArray(), spuJson = new JSONArray();
    private boolean tracksDirty = true;  // a lista de faixas só é refeita quando o motor avisa que mudou

    VideoPlayer(Activity act, FrameLayout root, Listener listener) {
        this.act = act;
        this.listener = listener;
        box = new FrameLayout(act);
        box.setBackgroundColor(0xff000000);
        box.setVisibility(View.GONE);
        video = new SurfaceView(act);
        subs = new SurfaceView(act);
        subs.setZOrderMediaOverlay(true);  // legenda acima do vídeo e abaixo da página
        subs.getHolder().setFormat(PixelFormat.TRANSLUCENT);
        box.addView(video, new FrameLayout.LayoutParams(-1, -1, Gravity.CENTER));
        box.addView(subs, new FrameLayout.LayoutParams(-1, -1, Gravity.CENTER));
        root.addView(box, 0, new FrameLayout.LayoutParams(-1, -1));
        final android.content.Context app = act.getApplicationContext();
        warmer = new Thread(() -> {
            ArrayList<String> opts = new ArrayList<>();
            opts.add("--audio-time-stretch");
            opts.add("--no-sub-autodetect-file");
            lib = new LibVLC(app, opts);
        }, "libvlc-init");
        warmer.start();
    }

    boolean active() {
        return mp != null && id != null && !prepared;
    }

    /** Aberto pausado, atrás da página, à espera do "Assistir" (ver prepare). */
    boolean preparedFor(String fileId, long posMs) {
        return prepared && fileId.equals(id) && Math.abs(preparedAt - posMs) < 3000;
    }

    String currentId() {
        return id;
    }

    /**
     * Abre o filme pausado no ponto de início, atrás da página da biblioteca: o motor já lê o índice do MKV
     * e liga o decodificador (uns 4 s num 4K da Mi Box) enquanto a pessoa lê a sinopse. O "Assistir" só solta o pause.
     */
    void prepare(String fileId, String url, long posMs, String[] subtitles) {
        if (preparedFor(fileId, posMs)) return;
        open(fileId, url, posMs, subtitles, true);
    }

    /** Solta o filme preparado. */
    void start() {
        prepared = false;
        if (mp != null) mp.play();
        ui.removeCallbacks(tick);
        ui.post(tick);
    }

    void open(String fileId, String url, long posMs, String[] subtitles) {
        open(fileId, url, posMs, subtitles, false);
    }

    private void open(String fileId, String url, long posMs, String[] subtitles, boolean paused) {
        if (warmer != null) {  // quase sempre já terminou: o app abre bem antes do primeiro filme
            try {
                warmer.join();
            } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
            }
            warmer = null;
        }
        if (mp == null) {
            mp = new MediaPlayer(lib);
            mp.setEventListener(this);
        }
        if (id != null) stop(false);
        id = fileId;
        prepared = paused;
        preparedAt = posMs;
        ended = false;
        state = "opening";
        buffering = 0;
        tracksDirty = true;
        box.setVisibility(View.VISIBLE);
        IVLCVout vout = mp.getVLCVout();
        vout.setVideoView(video);
        vout.setSubtitlesView(subs);
        vout.attachViews(this);
        box.post(() -> mp.getVLCVout().setWindowSize(box.getWidth(), box.getHeight()));
        Media m = new Media(lib, Uri.parse(url));
        m.setHWDecoderEnabled(true, false);
        // o vídeo já vem do cache local (127.0.0.1): uma reserva curta basta e o filme começa antes
        m.addOption(":network-caching=400");
        // o VLC enche 16 MB a cada pedido antes de seguir (pensado para internet); abrir um MKV faz vários
        // pedidos (começo, índice no fim, ponto de retomada) e cada um esperava os 16 MB do disco da Mi Box
        m.addOption(":prefetch-read-size=262144");
        m.addOption(":prefetch-buffer-size=4096");
        for (String sub : subtitles) {  // legendas em arquivo, servidas pelo PC (entram depois das do próprio vídeo)
            m.addSlave(new IMedia.Slave(IMedia.Slave.Type.Subtitle, 0, sub));
        }
        if (posMs > 0) m.addOption(":start-time=" + (posMs / 1000.0));
        if (paused) m.addOption(":start-paused");
        mp.setMedia(m);
        m.release();
        mp.play();
        ui.removeCallbacks(tick);
        if (!paused) ui.post(tick);
    }

    void toggle() {
        if (mp == null) return;
        if (mp.isPlaying()) mp.pause();
        else mp.play();
    }

    void seek(long ms) {
        // pelo quadro-chave mais perto: quem decodifica é a Mi Box, e a busca exata num 4K demora bem mais
        if (mp != null) mp.setTime(Math.max(0, ms), true);
    }

    void setAudio(int track) {
        if (mp != null) mp.setAudioTrack(track);
        tracksDirty = true;
    }

    void setSpu(int track) {
        if (mp != null) mp.setSpuTrack(track);
        tracksDirty = true;
    }

    /** Para e devolve a posição; `report` avisa quem chamou (para mandar ao PC). */
    void stop(boolean report) {
        if (mp == null || id == null) return;
        double pos = Math.max(0, mp.getTime()) / 1000.0, dur = Math.max(0, mp.getLength()) / 1000.0;
        String was = id;
        boolean wasPrepared = prepared;
        id = null;
        prepared = false;
        ui.removeCallbacks(tick);
        mp.stop();
        mp.getVLCVout().detachViews();
        box.setVisibility(View.GONE);
        state = "idle";
        if (report && !wasPrepared) listener.onStopped(was, ended ? dur : pos, dur, ended);
    }

    void release() {
        if (warmer != null) {
            try {
                warmer.join();
            } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
            }
            warmer = null;
        }
        stop(true);
        if (mp != null) {
            mp.release();
            mp = null;
        }
        if (lib != null) {
            lib.release();
            lib = null;
        }
    }

    double position() {
        return mp == null ? 0 : Math.max(0, mp.getTime()) / 1000.0;
    }

    double length() {
        return mp == null ? 0 : Math.max(0, mp.getLength()) / 1000.0;
    }

    // ---------- eventos do motor ----------
    @Override
    public void onEvent(MediaPlayer.Event e) {
        switch (e.type) {
            case MediaPlayer.Event.Opening:
                state = "opening";
                break;
            case MediaPlayer.Event.Buffering: {
                // chega várias vezes por segundo ao abrir e ao pular: só avisa a página quando termina ou recomeça
                float b = e.getBuffering();
                boolean changed = (b >= 100) != (buffering >= 100);
                buffering = b;
                if (!changed) return;
                break;
            }
            case MediaPlayer.Event.ESAdded:
            case MediaPlayer.Event.ESDeleted:
            case MediaPlayer.Event.ESSelected:
                tracksDirty = true;
                break;
            case MediaPlayer.Event.Playing:
                state = "playing";
                break;
            case MediaPlayer.Event.Paused:
                state = "paused";
                break;
            case MediaPlayer.Event.EndReached:
                ended = true;
                state = "ended";
                break;
            case MediaPlayer.Event.EncounteredError:
                state = "error";
                break;
            default:
                return;
        }
        push();
    }

    @Override
    public void onNewVideoLayout(IVLCVout vout, int width, int height, int visibleWidth, int visibleHeight, int sarNum, int sarDen) {
        videoW = visibleWidth;
        videoH = visibleHeight;
        this.sarNum = sarNum > 0 ? sarNum : 1;
        this.sarDen = sarDen > 0 ? sarDen : 1;
        fit();
    }

    /** O vídeo inteiro na tela, sem esticar (faixas pretas em cima e embaixo no 2.39:1). */
    private void fit() {
        int bw = box.getWidth(), bh = box.getHeight();
        if (videoW <= 0 || videoH <= 0 || bw <= 0 || bh <= 0) return;
        double ar = (double) videoW * sarNum / sarDen / videoH;
        int w = bw, h = (int) Math.round(bw / ar);
        if (h > bh) {
            h = bh;
            w = (int) Math.round(bh * ar);
        }
        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(w, h, Gravity.CENTER);
        video.setLayoutParams(lp);
        subs.setLayoutParams(new FrameLayout.LayoutParams(w, h, Gravity.CENTER));
    }

    // ---------- estado para a página ----------
    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            if (id == null) return;
            push();
            ui.postDelayed(this, 500);
        }
    };

    private void push() {
        if (mp == null || id == null) return;
        try {
            JSONObject o = new JSONObject();
            o.put("id", id);
            o.put("state", state);
            o.put("buffering", buffering);
            o.put("time", Math.max(0, mp.getTime()) / 1000.0);
            o.put("length", Math.max(0, mp.getLength()) / 1000.0);
            if (tracksDirty) {
                audioJson = tracks(mp.getAudioTracks());
                spuJson = tracks(mp.getSpuTracks());
                tracksDirty = false;
            }
            o.put("audio", audioJson);
            o.put("audioId", mp.getAudioTrack());
            o.put("spu", spuJson);
            o.put("spuId", mp.getSpuTrack());
            listener.onState(o);
        } catch (Exception e) {
            // o motor ainda abrindo
        }
    }

    private static JSONArray tracks(MediaPlayer.TrackDescription[] list) throws Exception {
        JSONArray a = new JSONArray();
        if (list == null) return a;
        for (MediaPlayer.TrackDescription t : list) {
            a.put(new JSONObject().put("id", t.id).put("name", t.name == null ? "" : t.name));
        }
        return a;
    }
}
