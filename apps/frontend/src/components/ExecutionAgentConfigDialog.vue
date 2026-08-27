<template>
  <q-dialog
    :model-value="modelValue"
    :maximized="$q.screen.lt.sm"
    :persistent="saving"
    @hide="close"
  >
    <q-card class="execution-agent-dialog app-surface column">
      <q-card-section class="row items-start no-wrap q-gutter-md">
        <q-avatar color="primary" text-color="white" icon="computer" />
        <div class="col">
          <div class="text-h6">{{ isEditing ? 'Computer bearbeiten' : 'Computer hinzufügen' }}</div>
          <div class="text-body2 app-muted">
            {{ isEditing ? 'Alias und Zugriffsrichtlinien konfigurieren.' : 'Computer anlegen und anschließend koppeln.' }}
          </div>
        </div>
        <q-btn
          flat
          round
          dense
          icon="close"
          aria-label="Dialog schließen"
          :disable="saving"
          @click="close"
        />
      </q-card-section>

      <q-separator />

      <q-form ref="formRef" class="col column no-wrap" @submit.prevent="save">
        <q-card-section class="col scroll q-gutter-md">
          <q-input
            v-model="alias"
            autofocus
            outlined
            label="Alias"
            maxlength="120"
            lazy-rules
            :rules="[requiredRule]"
          />

          <q-select
            v-for="field in policyFields"
            :key="field.key"
            v-model="settings[field.key]"
            outlined
            emit-value
            map-options
            :options="policyOptions"
            :label="field.label"
          />

          <q-toggle
            v-for="field in shellFields"
            :key="field.key"
            v-model="settings[field.key]"
            color="primary"
            :label="field.label"
          />

          <template v-if="isEditing">
            <q-checkbox
              v-model="useAsDefault"
              color="primary"
              label="Als Standardcomputer verwenden"
            />
            <div class="text-body2 app-muted">
              Optional. Wird für neue Sessions vorausgewählt und ersetzt den bisherigen Standardcomputer.
            </div>
          </template>

          <q-select
            v-else
            v-model="pairingDurationSeconds"
            outlined
            emit-value
            map-options
            :options="pairingDurationOptions"
            label="Gültigkeitsdauer des Kopplungstokens"
          />
        </q-card-section>

        <q-separator />
        <q-card-actions class="q-pa-md">
          <q-btn flat no-caps label="Abbrechen" :disable="saving" @click="close" />
          <q-space />
          <q-btn
            unelevated
            no-caps
            color="primary"
            :label="isEditing ? 'Änderungen speichern' : 'Computer hinzufügen'"
            type="submit"
            :loading="saving"
            :disable="!isEditing && pairingDurationSeconds === null"
          />
        </q-card-actions>
      </q-form>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useExecutionAgentStore } from 'stores/execution-agents'

defineOptions({ name: 'ExecutionAgentConfigDialog' })

const props = defineProps({
  modelValue: { type: Boolean, required: true },
  agent: { type: Object, default: null }
})
const emit = defineEmits(['update:modelValue', 'created'])

const $q = useQuasar()
const agents = useExecutionAgentStore()
const formRef = ref(null)
const saving = ref(false)
const alias = ref('')
const settings = reactive({
  homeFilesystemPolicy: 'auto',
  externalFilesystemPolicy: 'ask',
  homeShellEnabled: true,
  externalShellEnabled: true
})
const useAsDefault = ref(false)
const initialDefault = ref(false)
const pairingDurationSeconds = ref(null)
const isEditing = computed(() => Boolean(props.agent))
const isCurrentDefault = computed(() => props.agent?.id === agents.defaultExecutionAgentId)
const policyOptions = [
  { label: 'Automatisch', value: 'auto' },
  { label: 'Nachfragen', value: 'ask' },
  { label: 'Verboten', value: 'deny' }
]
const policyFields = [
  { key: 'homeFilesystemPolicy', label: 'Dateizugriff im Home-Verzeichnis' },
  { key: 'externalFilesystemPolicy', label: 'Dateizugriff außerhalb des Home-Verzeichnisses' }
]
const shellFields = [
  { key: 'homeShellEnabled', label: 'Shell im Home-Verzeichnis aktivieren' },
  { key: 'externalShellEnabled', label: 'Shell außerhalb des Home-Verzeichnisses aktivieren' }
]
const pairingDurationOptions = computed(() => [
  { label: '30 Minuten', value: 1800 },
  { label: '1 Stunde', value: 3600 },
  { label: '4 Stunden', value: 14_400 },
  { label: '12 Stunden', value: 43_200 },
  { label: '24 Stunden', value: 86_400 }
].filter((option) => option.value <= (agents.pairingPolicy?.maximumDurationSeconds || 0)))
let initialization = 0

const requiredRule = (value) => Boolean(String(value || '').trim()) || 'Alias erforderlich.'

async function save() {
  if (
    saving.value
    || (!isEditing.value && pairingDurationSeconds.value === null)
    || !await formRef.value?.validate()
  ) return
  const agent = props.agent
  const values = { alias: alias.value.trim(), ...settings }
  const submittedDefault = useAsDefault.value
  saving.value = true
  try {
    if (!agent) {
      const result = await agents.create(values, pairingDurationSeconds.value)
      emit('update:modelValue', false)
      emit('created', result)
      return
    }
    if ([...policyFields, ...shellFields].some(({ key }) => values[key] !== agent[key]) || values.alias !== agent.alias) {
      await agents.update(agent.id, values)
    }
    if (submittedDefault !== initialDefault.value) {
      await agents.setDefault(submittedDefault ? agent.id : null)
    }
    $q.notify({ type: 'positive', message: 'Computer gespeichert' })
    emit('update:modelValue', false)
  } finally {
    saving.value = false
  }
}

function close() {
  if (saving.value) return
  emit('update:modelValue', false)
}

watch(() => props.modelValue, async (open) => {
  const currentInitialization = ++initialization
  if (!open) return
  alias.value = props.agent?.alias || ''
  for (const { key } of [...policyFields, ...shellFields]) {
    settings[key] = props.agent?.[key] ?? (key.endsWith('Enabled') ? true : key === 'homeFilesystemPolicy' ? 'auto' : 'ask')
  }
  initialDefault.value = isCurrentDefault.value
  useAsDefault.value = initialDefault.value
  if (!props.agent) {
    try {
      const policy = await agents.fetchPairingPolicy()
      if (currentInitialization !== initialization) return
      pairingDurationSeconds.value = policy.defaultDurationSeconds
    } catch {
      if (currentInitialization === initialization && props.modelValue && !props.agent) close()
    }
  }
})
</script>
