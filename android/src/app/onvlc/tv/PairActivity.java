package app.onvlc.tv;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;

import java.security.SecureRandom;

/**
 * Porta de entrada dos pedidos do PC. No AndroidManifest ela exige a permissão DUMP, que só o adb (shell) e o
 * sistema têm: outro app instalado na TV, ou um link num navegador, não consegue abrir esta tela. O assistente e o
 * "Assistir na TV" do PC usam:
 *
 *   am start -n app.onvlc.tv/.PairActivity --es pc http://IP:8780 --es key CHAVE [--ez selftest true]
 *                                          [--es play ID --el pos MS] [--ez stop true]
 *
 * pc + key: onde está a biblioteca do PC e a chave dela (PcFinder.use confere e guarda). O resto é repassado à
 * LibraryActivity com um token deste processo: ela só obedece a pedidos que vieram por aqui.
 */
public class PairActivity extends Activity {
    /** Só quem está neste processo sabe: a LibraryActivity (exportada, por causa do lançador) ignora extras sem ele. */
    static final String TOKEN;

    static {
        byte[] b = new byte[16];
        new SecureRandom().nextBytes(b);
        TOKEN = PcFinder.hex(b);
    }

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        handle(getIntent());
    }

    // o Android pode entregar o pedido seguinte a esta mesma tela, se ela ainda estiver por cima
    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        handle(intent);
    }

    private void handle(Intent in) {
        String pc = in.getStringExtra("pc"), key = in.getStringExtra("key");
        if (pc != null || key != null) PcFinder.use(this, pc, key);
        Intent out = new Intent(this, LibraryActivity.class)
                .putExtra("t", TOKEN)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        if (in.getBooleanExtra("selftest", false)) out.putExtra("selftest", true);
        if (in.getBooleanExtra("stop", false)) out.putExtra("stop", true);
        String play = in.getStringExtra("play");
        if (play != null && play.matches("[0-9a-f]{1,32}")) {
            out.putExtra("play", play).putExtra("pos", Math.max(0, in.getLongExtra("pos", 0)));
        }
        startActivity(out);
        finish();
        overridePendingTransition(0, 0);
    }
}
