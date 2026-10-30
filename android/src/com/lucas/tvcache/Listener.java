package com.lucas.tvcache;

import android.service.notification.NotificationListenerService;

/**
 * Não faz nada sozinho: ter o acesso de "notificações" liberado é o que deixa o app ler o controle de mídia
 * do VLC (posição e duração do filme). Liberado uma vez pelo PC:
 * adb shell cmd notification allow_listener com.lucas.tvcache/com.lucas.tvcache.Listener
 */
public class Listener extends NotificationListenerService {
}
