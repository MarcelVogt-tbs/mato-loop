<template>
  <q-dialog v-model="open" :maximized="$q.screen.lt.sm" :persistent="saving">
    <q-card class="integration-dialog app-surface column">
      <q-form class="col column no-wrap" @submit.prevent="save">
        <q-card-section>
          <div class="text-h6">{{ project ? 'Projekt bearbeiten' : 'Projekt erstellen' }}</div>
        </q-card-section>
        <q-card-section class="col scroll q-pt-none">
          <q-input
            v-model="name"
            outlined
            autofocus
            label="Name"
            maxlength="240"
            lazy-rules
            :rules="[requiredRule]"
          />
          <q-input
            v-model="instructions"
            outlined
            type="textarea"
            rows="6"
            label="Projektanweisungen"
            hint="Optionale feste Regeln und Entscheidungen für alle Sessions. Die Projekthistorie wird zusätzlich über relevante Session-Auszüge berücksichtigt."
            maxlength="4000"
            counter
          />
          <q-separator class="q-my-lg" />
          <div class="text-subtitle1 text-weight-bold q-mb-sm">Session-Vorgaben</div>
          <q-select
            v-model="executionAgentId"
            outlined
            emit-value
            map-options
            label="Computer"
            :options="executionAgentOptions"
            :loading="executionAgents.loading"
            @update:model-value="selectExecutionAgent"
          >
            <template #option="scope">
              <q-item v-bind="scope.itemProps">
                <q-item-section avatar>
                  <q-icon :name="scope.opt.connected === false ? 'computer_off' : 'computer'" />
                </q-item-section>
                <q-item-section>
                  <q-item-label>{{ scope.opt.label }}</q-item-label>
                  <q-item-label caption>{{ scope.opt.caption }}</q-item-label>
                </q-item-section>
              </q-item>
            </template>
          </q-select>
          <q-input
            v-model="currentDirectory"
            outlined
            class="q-mt-md"
            label="Arbeitsverzeichnis"
            maxlength="4096"
            :disable="!selectedAgent?.connected"
            hint="Wird für neue Sessions übernommen und kann dort geändert werden."
          >
            <template #append>
              <q-btn
                flat
                round
                dense
                icon="folder_open"
                aria-label="Arbeitsverzeichnis auswählen"
                :disable="!directoryPickerAvailable"
                @click="directoryPickerOpen = true"
              >
                <q-tooltip>Arbeitsverzeichnis auswählen</q-tooltip>
              </q-btn>
            </template>
          </q-input>
        </q-card-section>
        <q-card-actions align="right" class="q-pa-md q-pt-none">
          <q-btn flat no-caps label="Abbrechen" :disable="saving" v-close-popup />
          <q-btn unelevated no-caps color="primary" label="Speichern" type="submit" :loading="saving" />
        </q-card-actions>
      </q-form>
    </q-card>
  </q-dialog>

  <ExecutionAgentDirectoryPickerDialog
    v-model="directoryPickerOpen"
    :execution-agent-id="executionAgentId"
    :initial-path="currentDirectory || selectedAgent?.homeDirectory || '/'"
    @select="currentDirectory = $event"
  />
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useProjectStore } from 'stores/projects'
import { useExecutionAgentStore } from 'stores/execution-agents'
import ExecutionAgentDirectoryPickerDialog from 'src/features/computers/ExecutionAgentDirectoryPickerDialog.vue'

const props = defineProps({ project: { type: Object, default: null } })
const emit = defineEmits(['created'])
const open = defineModel({ type: Boolean, required: true })
const $q = useQuasar()
const projects = useProjectStore()
const executionAgents = useExecutionAgentStore()
const name = ref('')
const instructions = ref('')
const executionAgentId = ref(null)
const currentDirectory = ref(null)
const directoryPickerOpen = ref(false)
const saving = ref(false)
const selectedAgent = computed(() => executionAgents.byId(executionAgentId.value))
const executionAgentOptions = computed(() => {
  const options = [
    { label: 'Keine Projektvorgabe', value: null, connected: null, caption: 'Allgemeine Session-Vorgabe verwenden' },
    ...executionAgents.items.map(agent => ({
      label: agent.alias, value: agent.id, connected: agent.connected,
      disable: !agent.connected && agent.id !== executionAgentId.value,
      caption: agent.connected ? agent.computerName || agent.platform : 'Offline'
    }))
  ]
  if (executionAgentId.value && !selectedAgent.value) {
    options.push({ label: 'Nicht verfügbarer Computer', value: executionAgentId.value, connected: false, caption: 'Nicht mehr eingerichtet' })
  }
  return options
})
const directoryPickerAvailable = computed(() => selectedAgent.value?.connected === true
  && selectedAgent.value.operations?.includes('filesystem.list'))
const requiredRule = value => Boolean(String(value || '').trim()) || 'Pflichtfeld'

watch(open, (shown) => {
  if (!shown) return
  name.value = props.project?.name || ''
  instructions.value = props.project?.instructions || ''
  executionAgentId.value = props.project?.defaultExecutionAgentId || null
  currentDirectory.value = props.project?.defaultCurrentDirectory || null
  directoryPickerOpen.value = false
  void executionAgents.fetchAll({ suppressErrorToast: true }).catch(() => {})
}, { immediate: true })

function selectExecutionAgent(id) {
  directoryPickerOpen.value = false
  currentDirectory.value = executionAgents.byId(id)?.homeDirectory || null
}

async function save() {
  const title = name.value.trim()
  if (!title || saving.value) return
  const defaults = {
    defaultExecutionAgentId: executionAgentId.value,
    defaultCurrentDirectory: executionAgentId.value ? currentDirectory.value?.trim() || null : null
  }
  saving.value = true
  try {
    if (props.project) {
      await projects.update(props.project.id, title, instructions.value.trim(), defaults)
      open.value = false
      $q.notify({ type: 'positive', message: 'Projekt gespeichert.' })
    } else {
      const project = await projects.create(title, instructions.value.trim(), defaults)
      open.value = false
      $q.notify({ type: 'positive', message: 'Projekt erstellt.' })
      emit('created', project)
    }
  } catch {
    // The central API interceptor displays the error; the dialog remains open.
  } finally {
    saving.value = false
  }
}
</script>
