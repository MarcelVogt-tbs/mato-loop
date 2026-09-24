<template>
  <q-dialog :model-value="modelValue" :maximized="$q.screen.lt.sm" :persistent="saving" @hide="close">
    <q-card class="integration-dialog app-surface column">
      <q-card-section class="row items-start no-wrap q-gutter-md">
        <q-avatar color="primary" text-color="white" icon="hub" />
        <div class="col">
          <div class="text-h6">{{ server ? `${server.name} bearbeiten` : 'MCP-Server hinzufügen' }}</div>
          <div class="text-body2 app-muted">Streamfähigen HTTP-MCP-Endpunkt verbinden.</div>
        </div>
        <q-btn flat round dense icon="close" aria-label="Dialog schließen" @click="close" />
      </q-card-section>
      <q-separator />

      <q-form ref="formRef" class="col column no-wrap" @submit.prevent="save">
        <q-card-section class="col scroll q-gutter-md">
          <q-input v-model.trim="form.name" outlined label="Name" maxlength="120" lazy-rules :rules="[requiredRule]" />
          <q-input
            v-model.trim="form.endpoint"
            outlined
            type="url"
            label="HTTP-Endpunkt"
            placeholder="https://mcp.example.com/mcp"
            hint="Nur HTTP und HTTPS werden unterstützt."
            persistent-hint
            lazy-rules
            :rules="[requiredRule, urlRule]"
          />
          <q-banner v-if="form.endpoint.toLowerCase().startsWith('http://')" rounded class="app-surface-tonal" role="alert">
            <template #avatar><q-icon name="warning_amber" color="warning" /></template>
            HTTP überträgt MCP-Daten und Zugangsdaten unverschlüsselt. Verwende es nur in einem vertrauenswürdigen lokalen Netz.
          </q-banner>
          <q-input
            v-model="form.bearerToken"
            outlined
            type="password"
            :label="server?.hasBearerToken ? 'Neuer Bearer-Token (optional)' : 'Bearer-Token (optional)'"
            :hint="server?.hasBearerToken ? 'Leer lassen, um den gespeicherten Token beizubehalten.' : 'Für Endpunkte ohne Anmeldung leer lassen.'"
            :disable="form.removeBearerToken"
            lazy-rules
            persistent-hint
          />
          <q-toggle
            v-if="server?.hasBearerToken"
            v-model="form.removeBearerToken"
            color="negative"
            label="Gespeicherten Bearer-Token entfernen"
            @update:model-value="form.bearerToken = ''"
          />
          <q-toggle v-model="form.enabled" color="primary" label="MCP-Server aktivieren" />
        </q-card-section>
        <q-separator />
        <q-card-actions class="q-pa-md">
          <q-btn flat no-caps label="Abbrechen" @click="close" />
          <q-space />
          <q-btn unelevated no-caps color="primary" type="submit" :label="server ? 'Änderungen speichern' : 'MCP-Server hinzufügen'" :loading="saving" />
        </q-card-actions>
      </q-form>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useMcpServerStore } from 'stores/mcp-servers'

defineOptions({ name: 'McpServerDialog' })

const props = defineProps({
  modelValue: { type: Boolean, required: true },
  server: { type: Object, default: null }
})
const emit = defineEmits(['update:modelValue'])
const $q = useQuasar()
const servers = useMcpServerStore()
const formRef = ref(null)
const saving = ref(false)
const form = reactive({ name: '', endpoint: '', bearerToken: '', removeBearerToken: false, enabled: true })
const requiredRule = (value) => Boolean(String(value || '').trim()) || 'Dieses Feld ist erforderlich.'
const urlRule = (value) => {
  try {
    return ['http:', 'https:'].includes(new URL(value).protocol) || 'Gib eine gültige HTTP- oder HTTPS-Adresse ein.'
  } catch {
    return 'Gib eine gültige HTTP- oder HTTPS-Adresse ein.'
  }
}
function assignForm() {
  Object.assign(form, {
    name: props.server?.name || '',
    endpoint: props.server?.endpoint || '',
    bearerToken: '',
    removeBearerToken: false,
    enabled: props.server?.enabled !== false
  })
}

function close() {
  if (!saving.value) emit('update:modelValue', false)
}

async function save() {
  if (!await formRef.value?.validate()) return
  saving.value = true
  try {
    const payload = { ...form }
    if (!payload.bearerToken) delete payload.bearerToken
    if (!props.server) delete payload.removeBearerToken
    if (props.server) await servers.updateServer(props.server.id, payload)
    else await servers.createServer(payload)
    $q.notify({ type: 'positive', message: props.server ? 'MCP-Server gespeichert' : 'MCP-Server hinzugefügt' })
    emit('update:modelValue', false)
  } finally {
    saving.value = false
  }
}

watch(() => [props.modelValue, props.server], assignForm, { immediate: true })
</script>
