<template>
  <div v-if="!auth.statusLoaded" class="startup-view column items-center justify-center">
    <AppLogo :size="56" />
    <div class="text-h6 text-weight-bold q-mt-md">MatoLoop</div>
    <q-spinner-dots color="primary" size="32px" class="q-mt-sm" />
    <q-btn
      v-if="startupSlow"
      flat
      no-caps
      color="primary"
      label="Erneut versuchen"
      class="q-mt-md"
      @click="reload"
    />
  </div>
  <router-view v-else />

  <q-banner
    v-if="updateAvailable"
    dense
    rounded
    class="pwa-update-banner app-surface shadow-2"
    role="status"
  >
    Eine neue Version ist verfügbar.
    <template #action>
      <q-btn flat dense no-caps color="primary" label="Später" @click="snoozeUpdate" />
      <q-btn flat dense no-caps color="primary" label="Jetzt aktualisieren" :loading="restarting" @click="applyUpdate" />
    </template>
  </q-banner>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import AppLogo from 'components/AppLogo.vue'
import { useAuthStore } from 'stores/auth'
import { notifyError } from 'src/utils/notifications'
import {
  PWA_UPDATE_EVENT,
  pwaUpdateSnoozeRemaining,
  requestPwaUpdate,
  snoozePwaUpdate
} from 'src/utils/pwa-update'

defineOptions({ name: 'App' })

const auth = useAuthStore()
const startupSlow = ref(false)
const updateAvailable = ref(false)
const restarting = ref(false)
let startupTimer = null
let updateTimer = null

function reload() {
  window.location.reload()
}

function showUpdate() {
  window.clearTimeout(updateTimer)
  const snoozeRemaining = pwaUpdateSnoozeRemaining()
  if (snoozeRemaining) {
    updateTimer = window.setTimeout(() => { void detectWaitingUpdate() }, snoozeRemaining)
    return
  }
  updateAvailable.value = true
}

function snoozeUpdate() {
  snoozePwaUpdate()
  updateAvailable.value = false
  showUpdate()
}

async function detectWaitingUpdate() {
  try {
    const registration = await navigator.serviceWorker?.getRegistration?.()
    if (registration?.waiting) showUpdate()
    else updateAvailable.value = false
  } catch {
    // Keep the current prompt; the next check can reconcile it.
  }
}

async function applyUpdate() {
  if (restarting.value) return
  restarting.value = true
  try {
    if (!await requestPwaUpdate()) await detectWaitingUpdate()
  } catch (error) {
    notifyError(error, 'Die Aktualisierung konnte nicht abgeschlossen werden. Versuche es erneut.')
    await detectWaitingUpdate()
  } finally {
    restarting.value = false
  }
}

onMounted(() => {
  startupTimer = window.setTimeout(() => { startupSlow.value = true }, 3000)
  window.addEventListener(PWA_UPDATE_EVENT, showUpdate)
  void detectWaitingUpdate()
})

onBeforeUnmount(() => {
  window.clearTimeout(startupTimer)
  window.clearTimeout(updateTimer)
  window.removeEventListener(PWA_UPDATE_EVENT, showUpdate)
})
</script>

<style scoped>
.startup-view {
  min-height: 100dvh;
  background: var(--app-shell-bg);
  color: var(--app-text);
}

.pwa-update-banner {
  position: fixed;
  z-index: 7000;
  right: max(16px, env(safe-area-inset-right));
  bottom: max(16px, env(safe-area-inset-bottom));
  left: max(16px, env(safe-area-inset-left));
  max-width: 560px;
  margin-left: auto;
}
</style>
