/* eslint-env serviceworker */

import { parsePushPayload, pushTargetPath } from '../src/utils/web-push'

// Workbox 7 restores its default marker when injectionPoint is undefined; the injected list is empty.
void self.__WB_MANIFEST

self.addEventListener('install', (event) => event.waitUntil(self.skipWaiting()))
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))

async function notifyClients() {
  const clients = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
  for (const client of clients) client.postMessage({ type: 'matoloop:push', version: 1 })
}

self.addEventListener('push', (event) => {
  let payload = null
  try {
    payload = parsePushPayload(event.data?.json())
  } catch {
    return
  }
  if (!payload) return

  event.waitUntil(Promise.allSettled([
    self.registration.showNotification(payload.title, payload.options),
    notifyClients()
  ]))
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const path = pushTargetPath(event.notification.data?.target)
  const destination = new URL(path, self.location.origin)
  event.waitUntil((async () => {
    const clients = await self.clients.matchAll({ type: 'window', includeUncontrolled: true })
    const client = clients.find((item) => new URL(item.url).origin === destination.origin)
    if (client) {
      await client.navigate(destination.href)
      return client.focus()
    }
    return self.clients.openWindow(destination.href)
  })())
})
