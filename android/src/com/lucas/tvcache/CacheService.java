package com.lucas.tvcache;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.IBinder;

/** Mantém o app vivo enquanto o filme toca (o servidor do cache roda dentro deste processo). */
public class CacheService extends Service {
    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel("tv", "TV Cache", NotificationManager.IMPORTANCE_LOW));
        Notification n = new Notification.Builder(this, "tv")
                .setContentTitle("TV Cache")
                .setContentText("Guardando o filme em volta do ponto que está tocando")
                .setSmallIcon(android.R.drawable.stat_sys_download)
                .build();
        startForeground(1, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        return START_NOT_STICKY;
    }

    @Override
    public void onDestroy() {
        CacheServer.get(this).pause();
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
