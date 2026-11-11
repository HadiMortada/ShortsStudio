// Shorts Studio notifications. Pushes carry no data: on each one, read the latest event from the
// studio (the signed-in session cookie is sent) and show it.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  event.waitUntil((async () => {
    let latest = null;
    try {
      const response = await fetch('/api/events', {credentials: 'include', cache: 'no-store'});
      if (response.ok) latest = (await response.json()).events[0] || null;
    } catch {}
    await self.registration.showNotification(latest?.title || 'Shorts Studio', {
      body: latest?.text || 'Something happened in your studio. Tap to see.',
      icon: '/icon-192.png', badge: '/badge-96.png', tag: latest?.id || 'shorts-studio',
      data: {url: latest?.url || '/#activity'},
    });
  })());
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || '/#activity', self.location.origin).href;
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({type: 'window', includeUncontrolled: true});
    for (const client of windows) {
      if (new URL(client.url).origin === self.location.origin) {
        await client.focus();
        return client.navigate(target);
      }
    }
    return self.clients.openWindow(target);
  })());
});
