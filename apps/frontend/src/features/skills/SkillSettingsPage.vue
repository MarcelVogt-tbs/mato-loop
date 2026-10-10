<template>
  <section>
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Skills</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          {{ admin ? 'Globale Skills für alle Benutzer verwalten.' : 'Eigene Anweisungen für spezialisierte Aufgaben verwalten.' }}
        </p>
      </div>
      <div v-if="showManagement" class="settings-scope-actions row items-center col-auto">
        <q-btn flat no-caps icon="upload_file" label="Importieren" :disable="writeBlocked" @click="importOpen = true" />
        <q-btn
          unelevated
          no-caps
          no-wrap
          color="primary"
          icon="add"
          label="Skill erstellen"
          :disable="writeBlocked"
          @click="openCreateDialog"
        />
      </div>
    </header>

    <q-tabs v-if="admin" v-model="section" dense no-caps align="left" active-color="primary" indicator-color="primary" class="q-mb-lg">
      <q-tab name="general" icon="settings" label="Einstellungen" />
      <q-tab name="management" icon="school" label="Verwaltung" />
    </q-tabs>
    <PersonalSettingsGate v-if="admin && section === 'general'" :model-value="skills.personalSkillsEnabled" title="Persönliche Skills erlauben" description="Benutzer können eigene Skills und persönliche Anpassungen mitgelieferter Skills erstellen und verwenden." :disabled="writeBlocked" @update:model-value="setPersonalSkillsEnabled" />
    <q-banner v-if="loaded && personalBlocked" rounded class="app-surface-tonal q-mb-md">
      Persönliche Skills sind deaktiviert. Sie bleiben lesbar gespeichert, können aber nicht verändert werden und werden ab dem nächsten Agentenlauf nicht mehr verwendet. Globale Skills bleiben verfügbar.
    </q-banner>

    <q-banner v-if="loadFailed" rounded class="bg-negative text-white q-mb-md">
      <template #avatar><q-icon name="error_outline" /></template>
      Die Skills konnten nicht geladen werden.
      <template #action>
        <q-btn flat no-caps color="white" label="Erneut laden" @click="load" />
      </template>
    </q-banner>

    <q-card v-if="showManagement" flat class="app-surface relative-position">
      <q-card-section v-if="skills.loading && !loaded" class="text-center q-pa-xl">
        <q-spinner color="primary" size="36px" />
        <div class="app-muted q-mt-sm">Skills werden geladen ...</div>
      </q-card-section>
      <q-card-section v-else-if="loaded && !skills.scopedItems.length" class="q-pa-xl">
        <AppStateMessage
          icon="school"
          title="Noch keine Skills"
          :description="admin ? 'Erstelle einen Skill für alle Benutzer.' : 'Erstelle einen persönlichen Skill.'"
        />
      </q-card-section>

      <q-list v-else separator>
        <q-item v-for="skill in skills.scopedItems" :key="skill.id" class="q-py-md">
          <q-item-section avatar top>
            <q-avatar rounded color="primary" text-color="white" icon="school" />
          </q-item-section>
          <q-item-section>
            <q-item-label class="text-subtitle1 text-weight-medium">
              {{ skillDisplayName(skill) }}
              <q-badge v-if="skill.isDefault" outline color="primary" label="Standard" class="q-ml-xs" />
              <q-badge outline color="grey-7" :label="skill.scope === 'instance' ? 'Global' : 'Persönlich'" class="q-ml-xs" />
            </q-item-label>
            <q-item-label caption class="text-body2 q-mt-xs">{{ skillDisplayDescription(skill) }}</q-item-label>
          </q-item-section>
          <q-item-section side top>
            <div class="row no-wrap q-gutter-xs">
              <q-btn flat round dense icon="download" :aria-label="`${skillDisplayName(skill)} exportieren`" @click="exportSkill(skill)" />
              <q-toggle dense
                :model-value="skill.enabled"
                :aria-label="`${skillDisplayName(skill)} ${skill.enabled ? 'deaktivieren' : 'aktivieren'}`"
                :disable="writeBlocked || (skill.scope === 'instance' && !auth.isAdmin)"
                @update:model-value="setEnabled(skill, $event)"
              />
              <q-btn v-if="skill.scope === 'personal' || auth.isAdmin" flat round dense :icon="personalBlocked ? 'visibility' : 'edit'" :aria-label="`${skillDisplayName(skill)} ${personalBlocked ? 'ansehen' : 'bearbeiten'}`" @click="openEditDialog(skill)">
                <q-tooltip>{{ personalBlocked ? 'Ansehen' : 'Bearbeiten' }}</q-tooltip>
              </q-btn>
              <q-btn v-if="skill.isDefault && skill.modified && auth.isAdmin" flat round dense icon="restart_alt" :aria-label="`${skillDisplayName(skill)} auf Standard zurücksetzen`" :disable="writeBlocked" @click="resetSkill(skill)">
                <q-tooltip>Auf Standard zurücksetzen</q-tooltip>
              </q-btn>
              <q-btn v-if="!skill.isDefault && (skill.scope === 'personal' || auth.isAdmin)" flat round dense color="negative" icon="delete_outline" :aria-label="`${skill.name} löschen`" :disable="writeBlocked" @click="removeSkill(skill)">
                <q-tooltip>Löschen</q-tooltip>
              </q-btn>
            </div>
          </q-item-section>
        </q-item>
      </q-list>

      <q-inner-loading :showing="skills.loading" />
    </q-card>

    <q-dialog v-model="dialogOpen" :maximized="$q.screen.lt.sm" :persistent="saving">
      <q-card class="skill-dialog app-surface column no-wrap">
        <q-form ref="formRef" class="column no-wrap full-height" @submit.prevent="saveSkill">
          <q-card-section class="row items-start no-wrap q-gutter-md">
            <q-avatar color="primary" text-color="white" :icon="editingSkill ? 'edit' : 'auto_awesome'" />
            <div class="col">
              <div class="text-h6">{{ personalBlocked ? 'Skill ansehen' : editingSkill ? 'Skill bearbeiten' : 'Skill erstellen' }}</div>
              <div class="text-body2 app-muted">{{ scope === 'instance' ? 'Global – für alle Benutzer' : 'Persönlich' }} · SKILL.md und Begleitdateien</div>
            </div>
            <q-btn flat round dense icon="close" aria-label="Dialog schließen" :disable="saving" v-close-popup />
          </q-card-section>
          <q-separator />
          <q-card-section class="q-pb-none">
            <div class="row items-center no-wrap">
              <q-select class="col q-mr-sm" dense outlined label="Datei" :model-value="selectedPath" :options="editingSkill?.resourcePaths || ['SKILL.md']" :disable="saving" @update:model-value="selectFile" />
              <q-btn v-if="editingSkill" flat round icon="download" aria-label="Datei herunterladen" @click="downloadResource" />
              <q-btn v-if="editingSkill && !personalBlocked" flat round icon="add" aria-label="Datei hinzufügen" @click="uploadOpen = true" />
              <q-btn v-if="editingSkill && selectedPath !== 'SKILL.md' && !personalBlocked" flat round color="negative" icon="delete_outline" aria-label="Datei löschen" @click="deleteResource" />
            </div>
            <q-checkbox v-if="selectedPath !== 'SKILL.md' && !binary" v-model="executable" :disable="personalBlocked" label="Ausführbare Datei" />
          </q-card-section>
          <q-card-section class="col column no-wrap overflow-hidden">
            <q-banner v-if="binary" rounded class="app-surface-tonal">Binärdatei – zum Anzeigen herunterladen oder durch eine neue Datei ersetzen.</q-banner>
            <WorkspaceFileEditor v-else v-model="form.document" class="col" :filename="selectedPath" :markdown="selectedPath.endsWith('.md')" :readonly="personalBlocked" :show-toolbar="false" :dirty="dirty" :saving="saving" @save="saveSkill" />
          </q-card-section>
          <q-separator />
          <q-card-actions align="right" class="q-pa-md">
            <q-btn flat no-caps label="Abbrechen" :disable="saving" v-close-popup />
            <q-btn v-if="!personalBlocked && !binary" unelevated no-caps color="primary" label="Speichern" type="submit" :loading="saving" :disable="writeBlocked" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>
    <q-dialog v-model="importOpen" :persistent="skills.saving">
      <q-card class="q-pa-md">
        <div class="text-h6">Skill importieren</div>
        <p>{{ admin ? 'Global – für alle Benutzer' : 'Persönlicher Skill' }}</p>
        <q-file v-model="importFile" outlined label="SKILL.md oder ZIP-Paket" accept=".md,.zip" :disable="skills.saving" />
        <q-card-actions align="right">
          <q-btn flat no-caps label="Abbrechen" :disable="skills.saving" v-close-popup />
          <q-btn color="primary" no-caps label="Importieren" :disable="!importFile" :loading="skills.saving" @click="importSkill" />
        </q-card-actions>
      </q-card>
    </q-dialog>
    <q-dialog v-model="uploadOpen" :persistent="skills.saving">
      <q-card class="q-pa-md">
        <div class="text-h6 q-mb-md">Paketdatei hinzufügen oder ersetzen</div>
        <q-input v-model="uploadPath" outlined label="Relativer Pfad" placeholder="scripts/example.py" class="q-mb-md" />
        <q-file v-model="uploadFile" outlined label="Datei" />
        <q-checkbox v-model="uploadExecutable" label="Ausführbar" />
        <q-card-actions align="right">
          <q-btn flat no-caps label="Abbrechen" :disable="skills.saving" v-close-popup />
          <q-btn color="primary" no-caps label="Speichern" :disable="!uploadFile || !uploadPath.trim()" :loading="skills.saving" @click="uploadResource" />
        </q-card-actions>
      </q-card>
    </q-dialog>
  </section>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { exportFile, useQuasar } from 'quasar'
import WorkspaceFileEditor from 'src/features/files/components/WorkspaceFileEditor.vue'
import AppStateMessage from 'src/shared/ui/AppStateMessage.vue'
import PersonalSettingsGate from 'src/features/settings/PersonalSettingsGate.vue'
import { useConfirm } from 'src/composables/confirm'
import { skillDisplayDescription, skillDisplayName } from 'src/features/skills/skill-display'
import { useSkillStore } from 'stores/skills'
import { useAuthStore } from 'stores/auth'

defineOptions({ name: 'SkillSettingsPage' })
const { admin } = defineProps({ admin: { type: Boolean, default: false } })

const $q = useQuasar()
const { confirm } = useConfirm()
const skills = useSkillStore()
const auth = useAuthStore()
const loaded = ref(false)
const loadFailed = ref(false)
const dialogOpen = ref(false)
const formRef = ref(null)
const editingSkill = ref(null)
const saving = ref(false)
const scope = computed(() => admin ? 'instance' : 'personal')
const section = ref('general')
const showManagement = computed(() => !admin || section.value === 'management')
const personalBlocked = computed(() => !admin && !auth.isAdmin && !skills.personalSkillsEnabled)
const writeBlocked = computed(() => !loaded.value || loadFailed.value || !skills.settingsLoaded || personalBlocked.value || skills.saving)
const form = reactive({ document: '' })
const selectedPath = ref('SKILL.md')
const original = ref('')
const executable = ref(false)
const originalExecutable = ref(false)
const dirty = computed(() => form.document !== original.value || executable.value !== originalExecutable.value)
const binary = ref(false)
const importOpen = ref(false)
const importFile = ref(null)
const uploadOpen = ref(false)
const uploadFile = ref(null)
const uploadPath = ref('')
const uploadExecutable = ref(false)

async function load() {
  loaded.value = false
  loadFailed.value = false
  try {
    skills.selectScope(scope.value)
    await Promise.all([skills.fetchSkills(scope.value), skills.fetchSettings()])
    loaded.value = true
  } catch {
    loadFailed.value = true
  }
}

function openCreateDialog() {
  editingSkill.value = null
  form.document = '---\nname: mein-skill\ndescription: Beschreibe, wann dieser Skill verwendet werden soll.\n---\n\n# Anweisungen\n\n'
  selectedPath.value = 'SKILL.md'
  original.value = form.document
  executable.value = false
  originalExecutable.value = false
  binary.value = false
  dialogOpen.value = true
}

function openEditDialog(skill) {
  editingSkill.value = skill
  skills.selectScope(skill.scope)
  form.document = skill.document
  selectedPath.value = 'SKILL.md'
  original.value = form.document
  executable.value = false
  originalExecutable.value = false
  binary.value = false
  dialogOpen.value = true
}

async function saveSkill() {
  if (writeBlocked.value) return
  if (!await formRef.value?.validate()) return
  saving.value = true
  try {
    const payload = { document: form.document, expectedRevision: editingSkill.value?.revision }
    const submittedExecutable = executable.value
    if (editingSkill.value) {
      editingSkill.value = selectedPath.value === 'SKILL.md'
        ? await skills.updateSkill(editingSkill.value.id, payload)
        : await skills.writeFile(editingSkill.value, selectedPath.value, new File([payload.document], selectedPath.value.split('/').pop()), submittedExecutable)
      $q.notify({ type: 'positive', message: 'Skill gespeichert' })
    } else {
      skills.selectScope(scope.value)
      editingSkill.value = await skills.createSkill(payload)
      $q.notify({ type: 'positive', message: 'Skill erstellt' })
    }
    original.value = payload.document
    originalExecutable.value = submittedExecutable
  } catch {
    // The central Axios interceptor displays the error; keep the dialog open.
  } finally {
    saving.value = false
  }
}

async function selectFile(path) {
  if (dirty.value && !await confirm('Ungespeicherte Änderungen verwerfen?', 'Datei wechseln')) return
  if (!editingSkill.value) return
  saving.value = true
  try {
    const response = await skills.readFile(editingSkill.value, path)
    let text = ''
    let isBinary = false
    try { text = new TextDecoder('utf-8', { fatal: true }).decode(response.data); isBinary = text.includes('\0') } catch { isBinary = true }
    form.document = text
    original.value = text
    selectedPath.value = path
    binary.value = isBinary
    executable.value = response.headers['x-skill-executable'] === 'true'
    originalExecutable.value = executable.value
    editingSkill.value = { ...editingSkill.value, revision: response.headers.etag.replaceAll('"', '') }
  } catch { /* Central API toast. */ } finally { saving.value = false }
}

async function importSkill() {
  try {
    skills.selectScope(scope.value)
    await skills.importSkill(importFile.value)
    importOpen.value = false
    importFile.value = null
    $q.notify({ type: 'positive', message: 'Skill importiert' })
  } catch { /* Central API toast. */ }
}

async function exportSkill(skill) {
  try { exportFile(`${skill.name}.zip`, await skills.exportSkill(skill), 'application/zip') } catch { /* Central API toast. */ }
}

async function downloadResource() {
  try { const { data } = await skills.readFile(editingSkill.value, selectedPath.value); exportFile(selectedPath.value.split('/').pop(), data) } catch { /* Central API toast. */ }
}

async function uploadResource() {
  if (editingSkill.value.resourcePaths.includes(uploadPath.value) && !await confirm('Die vorhandene Datei ersetzen?', 'Datei ersetzen')) return
  try {
    editingSkill.value = await skills.writeFile(editingSkill.value, uploadPath.value, uploadFile.value, uploadExecutable.value)
    if (uploadPath.value === selectedPath.value) {
      original.value = form.document
      originalExecutable.value = executable.value
      await selectFile(selectedPath.value)
    }
    uploadOpen.value = false
    uploadFile.value = null
    $q.notify({ type: 'positive', message: 'Paketdatei gespeichert' })
  } catch { /* Central API toast. */ }
}

async function deleteResource() {
  if (!await confirm(`Datei „${selectedPath.value}“ löschen?`, 'Datei löschen', { okColor: 'negative' })) return
  try {
    editingSkill.value = await skills.deleteFile(editingSkill.value, selectedPath.value)
    original.value = form.document
    originalExecutable.value = executable.value
    await selectFile('SKILL.md')
    $q.notify({ type: 'positive', message: 'Paketdatei gelöscht' })
  } catch { /* Central API toast. */ }
}

async function removeSkill(skill) {
  const accepted = await confirm(
    `Soll der Skill „${skill.name}“ dauerhaft gelöscht werden?`,
    'Skill löschen',
    { okLabel: 'Löschen', okColor: 'negative' }
  )
  if (!accepted) return
  try {
    skills.selectScope(skill.scope)
    await skills.deleteSkill(skill.id)
    await load()
    $q.notify({ type: 'positive', message: 'Skill gelöscht' })
  } catch {
    // The central Axios interceptor displays the error.
  }
}

async function setEnabled(skill, enabled) {
  try {
    skills.selectScope(skill.scope)
    await skills.setEnabled(skill.id, enabled)
    await load()
    $q.notify({ type: 'positive', message: enabled ? 'Skill aktiviert' : 'Skill deaktiviert' })
  } catch {
    // The central Axios interceptor displays the error.
  }
}

async function setPersonalSkillsEnabled(enabled) {
  try {
    await skills.setPersonalSkillsEnabled(enabled)
    $q.notify({ type: 'positive', message: 'Einstellung gespeichert' })
  } catch { /* Central API toast. */ }
}

async function resetSkill(skill) {
  const accepted = await confirm(
    `Soll der Skill „${skillDisplayName(skill)}“ auf den ausgelieferten Standard zurückgesetzt werden?`,
    'Skill zurücksetzen',
    { okLabel: 'Zurücksetzen' }
  )
  if (!accepted) return
  try {
    skills.selectScope(skill.scope)
    await skills.resetSkill(skill.id)
    await load()
    $q.notify({ type: 'positive', message: 'Skill auf Standard zurückgesetzt' })
  } catch {
    // The central Axios interceptor displays the error.
  }
}

onMounted(load)
watch(() => admin, load)
</script>

<style scoped>
.skill-dialog {
  width: min(720px, calc(100vw - 32px));
  height: calc(100dvh - 32px);
  max-height: calc(100dvh - 32px);
}

@media (max-width: 599px) {
  .skill-dialog {
    width: 100%;
    height: 100dvh;
    max-height: 100dvh;
  }
}
</style>
