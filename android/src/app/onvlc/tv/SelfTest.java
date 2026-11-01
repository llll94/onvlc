package app.onvlc.tv;

import android.app.Activity;
import android.media.AudioDeviceInfo;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.os.Build;
import android.view.Display;

import org.json.JSONObject;

import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Modo teste do assistente "Configurar a TV" do PC: mede a rede até o PC, lê o que a TV suporta (4K, HDR, canais de
 * som) e conta tudo para a biblioteca (POST /api/selftest/result). Quem toca o vídeo de teste é a LibraryActivity.
 */
final class SelfTest {
    private SelfTest() {
    }

    /** O que a TV (a tela ligada no HDMI) aceita. */
    static JSONObject caps(Activity a) {
        JSONObject o = new JSONObject();
        try {
            Display d = a.getWindowManager().getDefaultDisplay();
            boolean uhd = false;
            for (Display.Mode m : d.getSupportedModes()) {
                if (Math.max(m.getPhysicalWidth(), m.getPhysicalHeight()) >= 3840) uhd = true;
            }
            boolean hdr10 = false, dv = false, hlg = false;
            Display.HdrCapabilities h = d.getHdrCapabilities();
            if (h != null) {
                for (int t : h.getSupportedHdrTypes()) {
                    if (t == Display.HdrCapabilities.HDR_TYPE_HDR10) hdr10 = true;
                    else if (t == Display.HdrCapabilities.HDR_TYPE_DOLBY_VISION) dv = true;
                    else if (t == Display.HdrCapabilities.HDR_TYPE_HLG) hlg = true;
                }
            }
            int ch = 2;
            AudioManager am = a.getSystemService(AudioManager.class);
            for (AudioDeviceInfo dev : am.getDevices(AudioManager.GET_DEVICES_OUTPUTS)) {
                int t = dev.getType();
                if (t != AudioDeviceInfo.TYPE_HDMI && t != AudioDeviceInfo.TYPE_HDMI_ARC && t != 29 /* HDMI eARC */) continue;
                for (int c : dev.getChannelCounts()) ch = Math.max(ch, c);
                for (int e : dev.getEncodings()) {
                    if (e == AudioFormat.ENCODING_AC3 || e == AudioFormat.ENCODING_E_AC3) ch = Math.max(ch, 6);
                }
            }
            o.put("uhd", uhd).put("hdr10", hdr10).put("dv", dv).put("hlg", hlg).put("channels", ch)
                    .put("model", Build.MANUFACTURER + " " + Build.MODEL).put("android", Build.VERSION.RELEASE);
        } catch (Exception e) {
            // o que deu para ler fica
        }
        return o;
    }

    /** Baixa até 32 MB do PC (no máximo 8 s) e devolve a velocidade em Mb/s. */
    static double speed(String base) {
        HttpURLConnection c = null;
        long n = 0, t0 = System.nanoTime();
        try {
            c = (HttpURLConnection) new URL(base + "/api/speed?mb=32").openConnection();
            PcFinder.auth(c);
            c.setConnectTimeout(3000);
            c.setReadTimeout(10000);
            byte[] buf = new byte[64 << 10];
            try (InputStream in = c.getInputStream()) {
                int r;
                while ((r = in.read(buf)) > 0) {
                    n += r;
                    if (System.nanoTime() - t0 > 8_000_000_000L) break;
                }
            }
        } catch (Exception e) {
            // conta o que chegou
        } finally {
            if (c != null) c.disconnect();
        }
        double secs = Math.max(0.001, (System.nanoTime() - t0) / 1e9);
        return n * 8 / 1e6 / secs;
    }

    static void post(String base, JSONObject o) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(base + "/api/selftest/result").openConnection();
            PcFinder.auth(c);
            c.setConnectTimeout(3000);
            c.setReadTimeout(5000);
            c.setRequestMethod("POST");
            c.setDoOutput(true);
            c.setRequestProperty("Content-Type", "application/json");
            try (OutputStream out = c.getOutputStream()) {
                out.write(o.toString().getBytes("UTF-8"));
            }
            c.getResponseCode();
        } catch (Exception e) {
            // PC fora do ar: o assistente avisa que a TV não respondeu
        } finally {
            if (c != null) c.disconnect();
        }
    }
}
