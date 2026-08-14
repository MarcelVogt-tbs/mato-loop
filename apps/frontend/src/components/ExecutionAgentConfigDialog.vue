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
            {{ isEditing ? 'Name und Home-Verzeichnis konfigurieren.' : 'Computer anlegen und anschließend koppeln.' }}
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
            v-model="name"
            autofocus
            outlined
            label="Name"
            maxlength="120"
            lazy-rules
            :rules="[requiredRule]"
          />

          <template v-if="isEditing">
            <q-input
              v-model="homeDirectory"
              outlined
              clearable
              label="Home-Verzeichnis"
              maxlength="4096"
              hint="Leer entfernt das Home-Verzeichnis."
              persistent-hint
              lazy-rules
              :rules="[absolutePathRule]"
            />
            <div class="text-body2 app-muted">
              Absoluter Pfad aus Sicht des Computers oder Containers. Der Pfad muss innerhalb eines lokal freigegebenen Bereichs liegen.
            </div>
            <q-banner v-if="homeRequiresConnection" rounded class="app-surface-tonal">
              <template #avatar><q-icon name="cloud_off" color="warning" /></template>
              Der Computer muss verbunden sein, um einen neuen Pfad zu prüfen und zu speichern. Ein vorhandenes Home-Verzeichnis kann offline entfernt werden.
            </q-banner>
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
            :disable="homeRequiresConnection || (!isEditing && pairingDurationSeconds === null)"
          />
        </q-card-actions>
      </q-form>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
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
const name = ref('')
const homeDirectory = ref('')
const useAsDefault = ref(false)
const initialDefault = ref(false)
const pairingDurationSeconds = ref(null)
const isEditing = computed(() => Boolean(props.agent))
const normalizedHome = computed(() => homeDirectory.value?.trim() || null)
const isCurrentDefault = computed(() => props.agent?.id === agents.defaultExecutionAgentId)
const homeChanged = computed(() => normalizedHome.value !== (props.agent?.homeDirectory || null))
const homeRequiresConnection = computed(() => (
  isEditing.value && homeChanged.value && Boolean(normalizedHome.value) && !props.agent.connected
))
const pairingDurationOptions = computed(() => [
  { label: '30 Minuten', value: 1800 },
  { label: '1 Stunde', value: 3600 },
  { label: '4 Stunden', value: 14_400 },
  { label: '12 Stunden', value: 43_200 },
  { label: '24 Stunden', value: 86_400 }
].filter((option) => option.value <= (agents.pairingPolicy?.maximumDurationSeconds || 0)))
let initialization = 0

const requiredRule = (value) => Boolean(String(value || '').trim()) || 'Name erforderlich.'
const absolutePathRule = (value) => !String(value || '').trim() || String(value).trim().startsWith('/') || 'Ein absoluter Pfad ist erforderlich.'

async function save() {
  if (
    saving.value
    || (!isEditing.value && pairingDurationSeconds.value === null)
    || !await formRef.value?.validate()
  ) return
  const agent = props.agent
  const normalizedName = name.value.trim()
  const submittedHome = normalizedHome.value
  const submittedDefault = useAsDefault.value
  saving.value = true
  try {
    if (!agent) {
      const result = await agents.create(normalizedName, pairingDurationSeconds.value)
      emit('update:modelValue', false)
      emit('created', result)
      return
    }
    if (submittedHome !== (agent.homeDirectory || null)) {
      await agents.setHome(agent.id, submittedHome)
    }
    if (normalizedName !== agent.name) await agents.rename(agent.id, normalizedName)
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
  name.value = props.agent?.name || ''
  homeDirectory.value = props.agent?.homeDirectory || ''
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
