// --- SERVICE WORKER FOR KHARCH AI ---
// This script runs in the background and handles push notifications

self.addEventListener('install', (event) => {
    console.log('✅ Service Worker installed');
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    console.log('✅ Service Worker activated');
    event.waitUntil(clients.claim());
});

// --- HANDLE PUSH NOTIFICATIONS ---
self.addEventListener('push', (event) => {
    console.log('📨 Push notification received');
    
    let data = {
        title: 'Kharch AI',
        body: 'New transaction — tap to categorize',
        icon: 'https://via.placeholder.com/192x192/6c6ff0/ffffff?text=K',
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
            icon: data.icon,
            badge: data.icon,
            vibrate: [200, 100, 200],
            data: { url: data.url },
            actions: [
                { action: 'open', title: 'Categorize' }
            ]
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