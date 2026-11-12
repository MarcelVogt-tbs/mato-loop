<template>
  <q-card flat class="app-surface q-mt-md">
    <q-card-section class="q-gutter-md">
      <div class="text-subtitle2">Microsoft-Anmeldung</div>
      <div role="status" aria-live="polite">{{ status }}</div>
      <template v-if="attempt">
        <div v-if="attempt.redirectUri" class="text-body2 app-muted">
          Registrierte Web-Rückruf-URL: <code style="overflow-wrap: anywhere">{{ attempt.redirectUri }}</code>
        </div>
        <div v-if="attempt.userCode">
          Öffne Microsoft und gib diesen Code ein: <strong class="text-h6">{{ attempt.userCode }}</strong>
        </div>
        <div class="text-body2 app-muted">
          Melde dich nur mit deinem gewünschten Firmenkonto an. Der Zugriff gilt für dessen eigenes Postfach.
          Kehre danach hierher zurück; MatoLoop schließt die Anmeldung automatisch ab.
        </div>
        <div class="row q-gutter-sm">
          <q-btn outline no-caps color="primary" icon="open_in_new" label="Microsoft öffnen" :href="attempt.authorizationUrl" target="_blank" rel="noopener noreferrer" />
          <q-btn flat no-caps label="Anmeldung abbrechen" :disable="requesting" @click="cancel" />
        </div>
      </template>
      <div v-else class="row q-gutter-sm">
        <q-btn outline no-caps color="primary" icon="login" :label="integration.hasCredential ? 'Erneut bei Microsoft anmelden' : 'Bei Microsoft anmelden'" :loading="requesting" :disable="disabled" @click="start" />
        <q-btn v-if="integration.hasCredential" flat no-caps color="negative" label="Verbindung trennen" :disable="disabled || requesting" @click="disconnect" />
      </div>
    </q-card-section>
  </q-card>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { useQuasar } from 'quasar'
import { useConfirm } from 'src/composables/confirm'
import { useIntegrationStore } from 'stores/integrations'

const props = defineProps({ integration: { type: Object, required: true }, disabled: Boolean })
const emit = defineEmits(['busy', 'changed'])
const integrations = useIntegrationStore()
const $q = useQuasar()
const { confirm } = useConfirm()
const attempt = ref(null)
const requesting = ref(false)
const failed = ref(false)
let timer
let generation = 0
let expiresAt = 0
const status = computed(() => attempt.value ? 'Warte auf die Microsoft-Anmeldung …' : failed.value
  ? 'Die Anmeldung wurde nicht abgeschlossen. Starte sie bei Bedarf erneut.'
  : props.integration.authStatus === 'authorized' ? 'Microsoft 365 ist verbunden.' : 'Eine Microsoft-Anmeldung ist erforderlich.')

function stop() {
  clearTimeout(timer)
  generation += 1
  attempt.value = null
  requesting.value = false
  emit('busy', false)
}

function schedule(version, interval) {
  timer = setTimeout(() => poll(version), Math.max(1, interval) * 1000)
}

async function poll(version) {
  if (version !== generation || !attempt.value) return
  if (Date.now() >= expiresAt) { failed.value = true; stop(); return }
  requesting.value = true
  try {
    const result = await integrations.finishMicrosoft365Login(attempt.value.id)
    if (version !== generation) return
    if (result.status === 'authorized') {
      stop()
      $q.notify({ type: 'positive', message: 'Microsoft 365 verbunden' })
      emit('changed')
    } else schedule(version, result.interval)
  } catch {
    if (version === generation) { failed.value = true; stop() }
  } finally {
    if (version === generation) requesting.value = false
  }
}

async function start() {
  if (props.disabled || requesting.value) return
  failed.value = false
  requesting.value = true
  emit('busy', true)
  const version = ++generation
  try {
    const result = await integrations.startMicrosoft365Login(props.integration.id)
    if (version !== generation) return
    attempt.value = result
    expiresAt = Date.now() + result.expiresIn * 1000
    schedule(version, result.interval)
  } catch {
    if (version === generation) { failed.value = true; stop() }
  } finally {
    if (version === generation) requesting.value = false
  }
}

async function cancel() {
  if (!attempt.value || requesting.value) return
  requesting.value = true
  try {
    await integrations.cancelMicrosoft365Login(attempt.value.id)
    stop()
  } catch {
    // Keep the attempt visible when cancellation fails; the interceptor shows the error.
  } finally { requesting.value = false }
}

async function disconnect() {
  if (!await confirm('MatoLoop verliert den Zugriff auf dieses Postfach. Die Microsoft-Einwilligung wird dadurch nicht widerrufen.', 'Microsoft 365 trennen?', { okLabel: 'Trennen' })) return
  requesting.value = true
  emit('busy', true)
  try {
    await integrations.disconnectMicrosoft365(props.integration.id)
    $q.notify({ type: 'positive', message: 'Microsoft-365-Verbindung getrennt' })
    emit('changed')
  } catch {
    // The central API interceptor displays the error.
  } finally { requesting.value = false; emit('busy', false) }
}

onBeforeUnmount(stop)
</script>
