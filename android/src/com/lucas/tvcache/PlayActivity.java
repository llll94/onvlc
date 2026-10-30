package com.lucas.tvcache;

import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;

/**
 * Aberta pelo PC: liga o cache para o vídeo recebido e abre o VLC lendo do cache local.
 * Extras: position (ms), bps (bits por segundo do arquivo), title.
 */
public class PlayActivity extends Activity {
    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        Intent in = getIntent();
        Uri upstream = in.getData();
        long position = in.getLongExtra("position", 0);
        long bps = in.getLongExtra("bps", 0);
        String title = in.getStringExtra("title");

        Uri target = upstream;
        if (upstream != null) {
            try {
                CacheServer.get(this).open(upstream.toString(), bps);
                startForegroundService(new Intent(this, CacheService.class));
                // o código da sessão no endereço faz o VLC tratar como vídeo novo e respeitar a posição pedida
                // (num endereço que ele já conhece, o VLC retoma de onde ele mesmo parou e ignora a posição)
                java.util.List<String> seg = upstream.getPathSegments();
                String session = seg.size() >= 3 ? seg.get(seg.size() - 3) : "s";
                target = Uri.parse("http://127.0.0.1:" + CacheServer.PORT + "/stream/" + Uri.encode(session) + "/"
                        + Uri.encode(upstream.getLastPathSegment()));
            } catch (Exception e) {
                target = upstream;  // sem cache, o VLC lê direto do PC
            }
            Intent vlc = new Intent(Intent.ACTION_VIEW);
            vlc.setClassName("org.videolan.vlc", "org.videolan.vlc.gui.video.VideoPlayerActivity");
            vlc.setDataAndType(target, "video/*");
            vlc.putExtra("position", position);
            if (title != null) vlc.putExtra("title", title);
            vlc.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(vlc);
        }
        finish();
    }
}
