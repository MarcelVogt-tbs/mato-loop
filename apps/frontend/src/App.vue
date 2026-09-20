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
    rounded
    class="pwa-update-banner bg-primary text-white shadow-8"
  >
    Eine neue Version ist verfügbar.
    <template #action>
      <q-btn flat no-caps color="white" label="Neu starten" @click="confirmUpdate" />
    </template>
  </q-banner>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useQuasar } from 'quasar'
import AppLogo from 'components/AppLogo.vue'
import { useAuthStore } from 'stores/auth'
import { PWA_UPDATE_EVENT, pwaWindowCount, requestPwaUpdate } from 'src/utils/pwa-update'

defineOptions({ name: 'App' })

const $q = useQuasar()
const auth = useAuthStore()
const startupSlow = ref(false)
const updateAvailable = ref(false)
let startupTimer = null

function reload() {
  window.location.reload()
}

function showUpdate() {
  updateAvailable.value = true
}

async function detectWaitingUpdate() {
  const registration = await navigator.serviceWorker?.getRegistration?.()
  if (registration?.waiting) showUpdate()
}

async function confirmUpdate() {
  const windowCount = await pwaWindowCount()
  if (windowCount !== 1) {
    $q.dialog({
      title: 'Weitere Fenster schließen',
      message: windowCount === null
        ? 'Die geöffneten MatoLoop-Fenster konnten nicht geprüft werden. Schließe andere Fenster und versuche es erneut.'
        : 'Schließe zuerst alle anderen MatoLoop-Fenster und Tabs, damit keine unterschiedlichen Versionen gleichzeitig laufen.',
      ok: { label: 'Verstanden', color: 'primary', noCaps: true }
    })
    return
  }
  $q.dialog({
    title: 'MatoLoop neu starten?',
    message: 'Speichere offene Änderungen und beende laufende Aufnahmen. MatoLoop wird anschließend neu geladen.',
    cancel: 'Abbrechen',
    ok: { label: 'Neu starten', color: 'primary', noCaps: true }
  }).onOk(() => requestPwaUpdate())
}

onMounted(() => {
  startupTimer = window.setTimeout(() => { startupSlow.value = true }, 3000)
  window.addEventListener(PWA_UPDATE_EVENT, showUpdate)
  void detectWaitingUpdate()
})

onBeforeUnmount(() => {
  window.clearTimeout(startupTimer)
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
