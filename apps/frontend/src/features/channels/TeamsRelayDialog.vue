<template>
  <q-dialog :model-value="modelValue" :maximized="$q.screen.lt.sm" :persistent="saving || testing" @hide="close">
    <q-card class="integration-dialog app-surface column">
      <q-card-section class="row items-center no-wrap">
        <div class="text-h6 col">Teams-Relay konfigurieren</div>
        <q-btn flat round dense icon="close" aria-label="Dialog schließen" :disable="saving || testing" @click="close" />
      </q-card-section>
      <q-separator />
      <q-form ref="formRef" class="col column no-wrap" @submit.prevent="save">
        <q-card-section class="col scroll q-gutter-md">
          <q-banner rounded class="app-surface-tonal">
            MatoLoop verbindet sich ausgehend mit einem separat betriebenen HTTPS-Relay.
            Microsoft und der Relay können die übertragenen Inhalte sehen.
          </q-banner>
          <q-banner v-if="loadFailed" rounded class="app-surface-tonal">
            Die Konfiguration konnte nicht geladen werden.
            <template #action><q-btn flat no-caps label="Erneut laden" @click="load" /></template>
          </q-banner>
          <q-input v-model="form.relayUrl" dense outlined label="Relay-URL" placeholder="https://teams.example.org" :rules="[required]" :disable="loading" />
          <q-input v-model="form.tenantId" dense outlined label="Microsoft-Mandanten-ID" :rules="[uuidRule]" :disable="loading" />
          <q-input v-model="form.appId" dense outlined label="Microsoft-App-ID" :rules="[uuidRule]" :disable="loading" />
          <q-input
            v-model="form.token" dense outlined type="password"
            :label="saved?.hasCredential ? 'Neuer Relay-Token (optional)' : 'Relay-Token'"
            hint="Mindestens 43 Zeichen. Bei einer neuen Relay-URL ist ein neuer Token erforderlich."
            :rules="[tokenRule]" :disable="loading"
          />
          <q-toggle v-model="form.streamingEnabled" label="Live-Antworten in Teams" :disable="loading || saving" />
          <div v-if="bindingChanged" class="text-body2 app-muted">
            Änderungen an Relay-URL, Microsoft-IDs oder Token setzen vorhandene Teams-Kopplungen zurück.
          </div>
          <q-banner v-if="connection" rounded class="app-surface-tonal" role="status">
            Relay erreichbar. {{ connection.receiverOnline ? 'MatoLoop-Empfänger verbunden.' : 'Noch kein MatoLoop-Empfänger verbunden. Prüfe die globale Kanal- und Teams-Freigabe.' }}
          </q-banner>
        </q-card-section>
        <q-separator />
        <q-card-actions class="q-pa-md">
          <q-btn flat no-caps label="Abbrechen" :disable="saving || testing" @click="close" />
          <q-btn outline no-caps label="Gespeicherte Verbindung testen" :disable="!saved?.hasCredential || saving || loading" :loading="testing" @click="test" />
          <q-space />
          <q-btn unelevated no-caps color="primary" type="submit" label="Speichern" :loading="saving" :disable="loading || loadFailed || testing" />
        </q-card-actions>
      </q-form>
      <q-inner-loading :showing="loading" />
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useConfirm } from 'src/composables/confirm'
import { useChannelStore } from 'stores/channels'

defineOptions({ name: 'TeamsRelayDialog' })
const props = defineProps({ modelValue: { type: Boolean, required: true } })
const emit = defineEmits(['update:modelValue'])
const $q = useQuasar()
const { confirm } = useConfirm()
const channels = useChannelStore()
const formRef = ref(null)
const form = reactive({ relayUrl: '', tenantId: '', appId: '', token: '', streamingEnabled: true })
const saved = ref(null)
const connection = ref(null)
const loading = ref(false)
const loadFailed = ref(false)
const saving = ref(false)
const testing = ref(false)
const normalizedRelayUrl = computed(() => form.relayUrl.trim().replace(/\/+$/, ''))
const bindingChanged = computed(() => saved.value?.hasCredential && (
  Boolean(form.token) || normalizedRelayUrl.value !== saved.value.relayUrl ||
  form.appId.toLowerCase() !== saved.value.appId || form.tenantId.toLowerCase() !== saved.value.tenantId
))

const required = (value) => Boolean(String(value || '').trim()) || 'Pflichtfeld'
const uuidRule = (value) => /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(value) || 'Gültige UUID erforderlich'
const tokenRule = (value) => ((!value && saved.value?.hasCredential && saved.value.relayUrl === normalizedRelayUrl.value) || value.length >= 43) || 'Mindestens 43 Zeichen erforderlich'

function close() {
  if (!saving.value && !testing.value) {
    form.token = ''
    emit('update:modelValue', false)
  }
}

async function load() {
  loading.value = true
  loadFailed.value = false
  try {
    saved.value = await channels.fetchTeamsConfiguration()
    Object.assign(form, {
      relayUrl: saved.value.relayUrl || '', tenantId: saved.value.tenantId || '',
      appId: saved.value.appId || '', token: '', streamingEnabled: saved.value.streamingEnabled ?? true
    })
  } catch {
    loadFailed.value = true
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!await formRef.value?.validate()) return
  if (bindingChanged.value && !await confirm(
    'Die Konfiguration ändern und alle vorhandenen Teams-Kopplungen zurücksetzen?',
    'Teams-Konfiguration ändern', { okLabel: 'Ändern' }
  )) return
  saving.value = true
  connection.value = null
  try {
    const payload = { ...form, relayUrl: form.relayUrl.trim() }
    if (!payload.token) delete payload.token
    saved.value = await channels.saveTeamsConfiguration(payload)
    form.token = ''
    $q.notify({ type: 'positive', message: 'Teams-Relay gespeichert' })
  } catch {
    // The central Axios interceptor displays the error.
  } finally {
    saving.value = false
  }
}

async function test() {
  testing.value = true
  connection.value = null
  try {
    connection.value = await channels.testTeamsConfiguration()
  } catch {
    // The central Axios interceptor displays the error.
  } finally {
    testing.value = false
  }
}

watch(() => props.modelValue, (open) => {
  connection.value = null
  if (open) void load()
  else form.token = ''
})
</script>
