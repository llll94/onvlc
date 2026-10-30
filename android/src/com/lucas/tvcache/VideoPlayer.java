package com.lucas.tvcache;

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
    private boolean ended;

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
    }

    boolean active() {
        return mp != null && id != null;
    }

    String currentId() {
        return id;
    }

    void open(String fileId, String url, long posMs) {
        if (lib == null) {
            ArrayList<String> opts = new ArrayList<>();
            opts.add("--audio-time-stretch");
            opts.add("--no-sub-autodetect-file");
            lib = new LibVLC(act.getApplicationContext(), opts);
            mp = new MediaPlayer(lib);
            mp.setEventListener(this);
        }
        if (id != null) stop(false);
        id = fileId;
        ended = false;
        state = "opening";
        buffering = 0;
        box.setVisibility(View.VISIBLE);
        IVLCVout vout = mp.getVLCVout();
        vout.setVideoView(video);
        vout.setSubtitlesView(subs);
        vout.attachViews(this);
        box.post(() -> mp.getVLCVout().setWindowSize(box.getWidth(), box.getHeight()));
        Media m = new Media(lib, Uri.parse(url));
        m.setHWDecoderEnabled(true, false);
        m.addOption(":network-caching=1500");
        if (posMs > 0) m.addOption(":start-time=" + (posMs / 1000.0));
        mp.setMedia(m);
        m.release();
        mp.play();
        ui.removeCallbacks(tick);
        ui.post(tick);
    }

    void toggle() {
        if (mp == null) return;
        if (mp.isPlaying()) mp.pause();
        else mp.play();
    }

    void seek(long ms) {
        if (mp != null) mp.setTime(Math.max(0, ms), false);  // preciso (não só quadro-chave): o PC é rápido
    }

    void setAudio(int track) {
        if (mp != null) mp.setAudioTrack(track);
    }

    void setSpu(int track) {
        if (mp != null) mp.setSpuTrack(track);
    }

    /** Para e devolve a posição; `report` avisa quem chamou (para mandar ao PC). */
    void stop(boolean report) {
        if (mp == null || id == null) return;
        double pos = Math.max(0, mp.getTime()) / 1000.0, dur = Math.max(0, mp.getLength()) / 1000.0;
        String was = id;
        id = null;
        ui.removeCallbacks(tick);
        mp.stop();
        mp.getVLCVout().detachViews();
        box.setVisibility(View.GONE);
        state = "idle";
        if (report) listener.onStopped(was, ended ? dur : pos, dur, ended);
    }

    void release() {
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
            case MediaPlayer.Event.Buffering:
                buffering = e.getBuffering();
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
            o.put("audio", tracks(mp.getAudioTracks()));
            o.put("audioId", mp.getAudioTrack());
            o.put("spu", tracks(mp.getSpuTracks()));
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
