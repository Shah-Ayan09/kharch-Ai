// --- SERVICE WORKER FOR KHARCH AI ---
// This script runs in the background and handles push notifications

const CACHE_VERSION = 'v5';

self.addEventListener('install', (event) => {
    console.log('✅ Service Worker installed', CACHE_VERSION);
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    console.log('✅ Service Worker activated', CACHE_VERSION);
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.map(k => caches.delete(k)))
        ).then(() => self.clients.claim())
    );
});

// --- HANDLE PUSH NOTIFICATIONS ---
self.addEventListener('push', (event) => {
    console.log('📨 Push notification received');

    let data = {
        title: 'Kharch AI',
        body: 'New transaction — tap to categorize',
        url: '/categorize'
    };

    if (event.data) {
        try {
            data = { ...data, ...event.data.json() };
        } catch (e) {
            console.log('Could not parse push data:', e);
        }
    }

    event.waitUntil(
        self.registration.showNotification(data.title, {
            body: data.body,
            vibrate: [200, 100, 200],
            data: { url: data.url }
        })
    );
});

// --- HANDLE NOTIFICATION CLICKS ---
self.addEventListener('notificationclick', (event) => {
    console.log('👆 Notification clicked');
    event.notification.close();

    const urlToOpen = event.notification.data?.url || '/categorize';

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
            for (const client of clientList) {
                if (client.url.includes(location.origin) && 'focus' in client) {
                    client.navigate(urlToOpen);
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(urlToOpen);
            }
        })
    );
});