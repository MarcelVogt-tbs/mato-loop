<template>
  <q-dialog v-model="open" :maximized="$q.screen.lt.sm" :persistent="saving">
    <q-card class="integration-dialog app-surface column">
      <q-form class="col column no-wrap" @submit.prevent="emit('save')">
        <q-card-section>
          <div class="text-h6">{{ mode === 'create' ? 'Projekt erstellen' : 'Projekt bearbeiten' }}</div>
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
            :loading="agentsLoading"
            @update:model-value="emit('select-agent', $event)"
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
import ExecutionAgentDirectoryPickerDialog from 'src/features/computers/ExecutionAgentDirectoryPickerDialog.vue'

defineProps({
  mode: { type: String, required: true },
  saving: Boolean,
  executionAgentOptions: { type: Array, required: true },
  agentsLoading: Boolean,
  selectedAgent: { type: Object, default: null },
  directoryPickerAvailable: Boolean,
  requiredRule: { type: Function, required: true }
})
const emit = defineEmits(['save', 'select-agent'])
const open = defineModel({ type: Boolean, required: true })
const name = defineModel('name', { type: String, required: true })
const instructions = defineModel('instructions', { type: String, required: true })
const executionAgentId = defineModel('executionAgentId', { type: String, default: null })
const currentDirectory = defineModel('currentDirectory', { type: String, default: null })
const directoryPickerOpen = defineModel('directoryPickerOpen', { type: Boolean, required: true })
</script>
