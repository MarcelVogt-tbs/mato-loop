<template>
  <q-dialog ref="dialogRef" :transition-duration="0" @hide="onDialogHide">
    <q-card style="width: 480px; max-width: 100vw">
      <q-card-section class="text-h6">{{ label }} {{ permanent ? 'endgültig löschen' : 'in den Papierkorb verschieben' }}</q-card-section>
      <q-card-section class="q-pt-none">
        <p class="text-break q-mb-none">„{{ title }}“</p>
        <template v-if="session && worktree">
          <q-checkbox v-model="removeWorktree" :disable="Boolean(worktree.removalError && !worktree.scheduled)" label="Worktree ebenfalls entfernen" class="q-mt-sm" />
          <p v-if="worktree.removalError" class="q-mb-none text-caption text-break" role="status">{{ apiErrorMessage({ error: worktree.removalError }) }}</p>
          <template v-if="removeWorktree">
            <p class="q-mb-none text-caption" role="status">{{ permanent ? '' : 'Erst beim endgültigen Löschen. ' }}Nicht committete Änderungen und unversionierte Dateien gehen verloren. Ungesicherte Commits können verloren gehen.</p>
            <p v-if="worktree.sessions?.length" class="q-mb-none text-caption text-break">Beim Entfernen wechseln zu Lokal: {{ worktree.sessions.map(item => item.title || 'Ohne Titel').join(', ') }}.</p>
          </template>
        </template>
        <p v-else-if="session && hasWorktree" class="q-mt-sm q-mb-none text-caption" role="status">Der Worktree kann nicht geprüft werden und bleibt erhalten.</p>
        <q-checkbox v-if="!permanentLocked" v-model="permanent" label="Sofort endgültig löschen" class="q-mt-sm" />
      </q-card-section>
      <q-card-actions align="right">
        <q-btn flat no-caps label="Abbrechen" @click="onDialogCancel" />
        <q-btn unelevated no-caps color="negative" label="Bestätigen" @click="submit" />
      </q-card-actions>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { ref } from 'vue'
import { useDialogPluginComponent } from 'quasar'
import { apiErrorMessage } from 'src/utils/api-errors'

const props = defineProps({
  label: { type: String, required: true }, title: { type: String, required: true },
  session: Boolean, permanentLocked: Boolean,
  worktree: { type: Object, default: null }, hasWorktree: Boolean
})
defineEmits([...useDialogPluginComponent.emits])
const { dialogRef, onDialogHide, onDialogOK, onDialogCancel } = useDialogPluginComponent()
const permanent = ref(props.permanentLocked)
const removeWorktree = ref(Boolean(props.worktree?.scheduled))

function submit() {
  onDialogOK(props.session ? {
    permanent: permanent.value,
    worktreeImpact: removeWorktree.value && !props.worktree?.scheduled ? props.worktree?.impact || null : null,
    keepWorktree: Boolean(props.worktree?.scheduled && !removeWorktree.value)
  } : permanent.value)
}
</script>
