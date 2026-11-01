package app.onvlc.tv;

import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/** Conta para o PC onde o filme parou (a mesma posição do onVLC de lá). */
final class Progress {
    /** Em segundo plano: pode ser chamado da tela. */
    static void post(final String base, final String id, final double pos, final double dur) {
        new Thread(() -> send(base, id, pos, dur), "progress").start();
    }

    static void send(String base, String id, double pos, double dur) {
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(base + "/api/progress").openConnection();
            PcFinder.auth(c);
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
            // PC fora do ar: vai na próxima
        }
    }
}
