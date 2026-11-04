<template>
  <q-card flat bordered class="app-surface">
    <q-card-section>
      <div class="text-caption app-muted"><q-icon name="folder_open" class="q-mr-xs" />Arbeitsbereich</div>
      <div class="row items-center no-wrap q-gutter-x-sm">
        <span class="col text-weight-medium git-workspace-path">{{ context.currentDirectory || 'Nicht ausgewählt' }}</span>
        <span v-if="currentWorktree?.dirty" class="text-caption text-warning">● Geändert</span>
        <q-btn flat dense round class="col-auto" icon="content_copy" aria-label="Pfad kopieren" :disable="!context.currentDirectory" @click="copyPath">
          <q-tooltip>Pfad kopieren</q-tooltip>
        </q-btn>
        <q-btn flat dense round class="col-auto" icon="edit" aria-label="Arbeitsbereich bearbeiten" :disable="disabled" @click="openPicker('workspace')" />
      </div>
    </q-card-section>
    <q-separator />
    <q-card-section class="git-workspace-status">
      <div v-for="row in rows" :key="row.label" class="overflow-hidden">
        <div class="text-caption app-muted q-mb-xs"><q-icon :name="row.icon" class="q-mr-xs" />{{ row.label }}</div>
        <div class="row items-center no-wrap q-gutter-x-sm">
          <span class="text-weight-medium ellipsis">{{ row.value }}</span>
          <q-btn flat dense round class="col-auto" icon="edit" :aria-label="`${row.label} bearbeiten`" :disable="row.disabled" @click="row.edit" />
        </div>
      </div>
    </q-card-section>
    <q-card-actions v-if="project" class="q-px-md q-pb-md q-pt-none">
      <q-btn flat no-caps icon="tune" label="Projektvorgaben" @click="emit('project-defaults')" />
    </q-card-actions>
  </q-card>

  <q-dialog :model-value="Boolean(picker)" :transition-duration="0" :maximized="$q.screen.lt.sm" @update:model-value="picker = $event ? picker : null">
    <q-card class="integration-dialog app-surface column">
      <q-card-section class="row items-center no-wrap q-gutter-x-sm">
        <div class="col text-h6">{{ picker === 'workspace' ? 'Arbeitsbereich' : 'Branches' }}</div>
        <q-btn v-if="picker === 'workspace'" flat dense round icon="add" aria-label="Worktree erstellen" :disable="busy || !canCreate" @click="selectWorkspace(null, true)" />
        <q-btn v-else flat dense round icon="add" aria-label="Branch erstellen" :disable="actions.messageDisabled || !chat.current" @click="promptBranch(false)" />
        <q-btn flat dense round icon="close" aria-label="Schließen" v-close-popup />
      </q-card-section>
      <q-card-section class="q-pt-none">
        <q-input v-model="search" outlined dense clearable :label="picker === 'workspace' ? 'Arbeitsbereich suchen' : 'Branch suchen'" />
        <div v-if="picker === 'workspace' && !worktreesEnabled" class="text-caption app-muted q-mt-xs">Worktrees sind in der Sessionvorlage deaktiviert.</div>
      </q-card-section>
      <q-list class="col scroll">
        <template v-if="picker === 'workspace'">
          <template v-for="(item, index) in filteredWorkspaces" :key="item.path">
            <q-separator v-if="index" />
            <q-item clickable manual-focus :focused="selected === item.path"
              :aria-label="`Arbeitsbereich ${item.path} markieren`" @click="selected = item.path">
              <q-item-section>
                <q-item-label class="ellipsis">{{ workspaceName(item.path) }}</q-item-label>
                <q-item-label caption class="app-muted ellipsis">{{ item.main ? item.path : parentDirectory(item.path) }}</q-item-label>
              </q-item-section>
              <q-item-section v-if="repository?.repository === item.path" side>
                <q-icon name="my_location" role="img" aria-hidden="false" aria-label="Aktiver Arbeitsbereich" />
              </q-item-section>
              <q-item-section side>
                <q-btn flat dense round icon="more_vert"
                  :aria-label="item.main ? 'Aktionen für lokalen Arbeitsbereich' : `Aktionen für Worktree ${baseName(item.path)}`" @click.stop>
                  <AppMenu>
                    <q-list dense>
                      <q-item v-close-popup clickable :aria-label="`Arbeitsbereich ${item.path} aktivieren`"
                        :disable="busy || item.path === repository?.repository || (!item.main && (item.locked || !worktreesEnabled))"
                        @click="selectWorkspace(item.path)">
                        <q-item-section side><q-icon name="play_arrow" /></q-item-section>
                        <q-item-section>Aktivieren</q-item-section>
                      </q-item>
                      <q-separator v-if="!item.main" />
                      <q-item v-if="!item.main" v-close-popup clickable :disable="actions.messageDisabled || item.locked" class="text-negative"
                        :aria-label="`Worktree ${baseName(item.path)} entfernen`" @click="removeWorktree(item.path)">
                        <q-item-section side><q-icon name="delete_outline" /></q-item-section>
                        <q-item-section>Entfernen</q-item-section>
                      </q-item>
                    </q-list>
                  </AppMenu>
                </q-btn>
              </q-item-section>
            </q-item>
          </template>
          <q-item v-if="!filteredWorkspaces.length"><q-item-section class="app-muted">Keine Arbeitsbereiche gefunden.</q-item-section></q-item>
        </template>
        <template v-else>
          <q-item v-for="name in filteredBranches" :key="name" clickable :active="selected === name"
            :aria-label="`Branch ${name} markieren`" @click="selected = name">
            <q-item-section>
              <q-item-label class="row no-wrap">
                <span v-if="name.includes('/')" class="col-shrink ellipsis app-muted">{{ name.slice(0, name.lastIndexOf('/') + 1) }}</span>
                <span class="col-auto ellipsis">{{ baseName(name) }}</span>
              </q-item-label>
              <q-item-label v-if="branchWorktree(name)" caption class="ellipsis">in {{ workspaceName(branchWorktree(name).path) }}</q-item-label>
            </q-item-section>
            <q-item-section v-if="name === currentBranch" side>
              <q-icon name="check" role="img" aria-hidden="false" aria-label="Aktueller Branch" />
            </q-item-section>
            <q-item-section side>
              <q-btn flat dense round icon="more_vert" :aria-label="`Aktionen für Branch ${name}`" @click.stop>
                <AppMenu>
                  <q-list dense>
                    <q-item v-if="name === currentBranch" v-close-popup clickable :disable="busy" aria-label="Aktuellen Branch umbenennen" @click="promptBranch(true)">
                      <q-item-section side><q-icon name="edit" /></q-item-section>
                      <q-item-section>Umbenennen</q-item-section>
                    </q-item>
                    <q-item v-else v-close-popup clickable class="text-negative" :aria-label="`Branch ${name} löschen`"
                      :disable="actions.messageDisabled || name === targetBranch || worktrees.some(item => item.branch === name)" @click="removeBranch(name)">
                      <q-item-section side><q-icon name="delete_outline" /></q-item-section>
                      <q-item-section>Löschen</q-item-section>
                    </q-item>
                  </q-list>
                </AppMenu>
              </q-btn>
            </q-item-section>
          </q-item>
          <q-item v-if="!filteredBranches.length"><q-item-section class="app-muted">Keine Branches gefunden.</q-item-section></q-item>
        </template>
      </q-list>
      <q-separator />
      <q-card-section v-if="picker !== 'workspace' || markedWorkspace" class="scroll">
        <template v-if="picker === 'workspace'">
          <div class="git-workspace-path">{{ markedWorkspace.path }}</div>
          <div v-if="markedWorkspace.dirty === true" class="text-caption text-warning">● Geändert</div>
          <div v-else-if="markedWorkspace.dirty == null" class="text-caption app-muted">Status nicht geprüft</div>
          <div v-if="markedWorkspace.locked" class="text-caption app-muted">Worktree gesperrt</div>
          <div v-if="markedWorkspace.sessions?.length" class="row q-gutter-xs q-mt-xs">
            <q-chip v-for="session in markedWorkspace.sessions" :key="session.id" dense clickable icon="chat_bubble_outline"
              class="git-session-chip" :aria-label="`Session ${session.title} öffnen`" @click="openSession(session)">
              <span class="ellipsis">{{ session.title || 'Ohne Titel' }}</span>
            </q-chip>
          </div>
        </template>
        <template v-else>
          <div v-if="selected" class="git-workspace-path">{{ selected }}</div>
          <div v-if="branchWorktree(selected)" class="text-caption app-muted git-workspace-path">Ausgecheckt in: {{ branchWorktree(selected).path }}</div>
          <div v-if="repository?.localBranchesTruncated" class="text-caption app-muted q-mt-sm">Die Branchliste ist auf 1.000 Einträge begrenzt.</div>
        </template>
      </q-card-section>
      <q-card-actions align="right">
        <q-btn flat no-caps :label="picker === 'workspace' ? 'Schließen' : 'Abbrechen'" v-close-popup />
        <q-btn v-if="picker === 'branch'" unelevated no-caps color="primary" label="Wechseln" aria-label="Branch wechseln"
          :disable="busy || !selected || !repository?.localBranches?.includes(selected) || selected === currentBranch || Boolean(branchWorktree(selected))" @click="selectBranch(selected)" />
      </q-card-actions>
    </q-card>
  </q-dialog>

  <q-dialog v-model="targetOpen" :transition-duration="0" :maximized="$q.screen.lt.sm">
    <q-card class="integration-dialog app-surface">
      <q-form @submit.prevent="setTarget(targetDraft)">
        <q-card-section><div class="text-h6">Zielbranch</div></q-card-section>
        <q-card-section>
          <AppSelect v-model="targetDraft" outlined dense label="Zielbranch" :options="repository?.localBranches || []"
            use-input new-value-mode="add-unique" :disable="busy" />
        </q-card-section>
        <q-card-actions align="right">
          <q-btn flat no-caps label="Abbrechen" v-close-popup />
          <q-btn unelevated no-caps color="primary" type="submit" label="Speichern" :disable="busy || !targetDraft || targetDraft === targetBranch" />
        </q-card-actions>
      </q-form>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { copyToClipboard, useQuasar } from 'quasar'
import { useRouter } from 'vue-router'
import AppMenu from 'src/shared/ui/AppMenu.vue'
import AppSelect from 'src/shared/ui/AppSelect.vue'
import { useChatStore } from 'src/features/chat/store'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useConfirm } from 'src/composables/confirm'
import { useGitActionStore } from './git-actions-store'

const props = defineProps({
  project: { type: Object, default: null }, repository: { type: Object, default: null }, loading: Boolean,
  targetBranch: { type: String, default: null }, remote: { type: String, default: null }, remoteDisabled: Boolean
})
const emit = defineEmits(['changed', 'edit-remote', 'project-defaults', 'inspect-worktrees'])
const $q = useQuasar(), { confirm } = useConfirm()
const router = useRouter()
const chat = useChatStore(), agents = useExecutionAgentStore(), actions = useGitActionStore()
const context = computed(() => chat.activeContext)
const picker = ref(null), selected = ref(null), search = ref('')
const targetOpen = ref(false), targetDraft = ref(null)
const available = computed(() => props.project && agents.byId(context.value.preferredExecutionAgentId)?.connected
  && agents.byId(context.value.preferredExecutionAgentId)?.operations?.includes('git.branch'))
const busy = computed(() => actions.busy || props.loading)
const disabled = computed(() => !available.value || !chat.current)
const worktrees = computed(() => props.repository?.worktrees || [])
const workspaceOptions = computed(() => [...worktrees.value]
  .sort((a, b) => Number(b.path === props.repository?.repository) - Number(a.path === props.repository?.repository)
    || Number(Boolean(b.main)) - Number(Boolean(a.main))))
const worktreesEnabled = computed(() => Boolean(context.value.sessionProfile?.worktreesEnabled))
const canCreate = computed(() => worktreesEnabled.value && props.project?.defaultExecutionAgentId === context.value.preferredExecutionAgentId
  && worktrees.value.some(item => item.path === props.project.defaultCurrentDirectory))
const currentWorktree = computed(() => worktrees.value.find(item => item.path === props.repository?.repository))
const currentBranch = computed(() => currentWorktree.value ? currentWorktree.value.branch : props.repository?.branch)
const baseName = path => path?.split('/').filter(Boolean).pop() || path || 'Lokal'
const parentDirectory = path => path?.slice(0, path.lastIndexOf('/') + 1) || '/'
const workspaceName = path => worktrees.value.some(item => item.path === path && item.main) ? 'Lokal' : baseName(path)
const branchWorktree = name => name ? worktrees.value.find(item => item.branch === name && item.path !== props.repository?.repository) : null
const filteredWorkspaces = computed(() => workspaceOptions.value.filter(item =>
  `${workspaceName(item.path)} ${item.path}`.toLowerCase().includes((search.value || '').trim().toLowerCase())))
const markedWorkspace = computed(() => workspaceOptions.value.find(item => item.path === selected.value))
const filteredBranches = computed(() => (props.repository?.localBranches || [])
  .filter(name => name.toLowerCase().includes((search.value || '').trim().toLowerCase()))
  .sort((a, b) => Number(b === currentBranch.value) - Number(a === currentBranch.value)))
const rows = computed(() => [
  { label: 'Branch', icon: 'call_split', value: currentBranch.value || 'Losgelöster Stand', disabled: disabled.value, edit: () => openPicker('branch') },
  { label: 'Zielbranch', icon: 'arrow_forward', value: props.targetBranch || 'Nicht eingerichtet', disabled: disabled.value || busy.value, edit: () => { targetDraft.value = props.targetBranch; targetOpen.value = true } },
  { label: 'Remote', icon: 'cloud_queue', value: props.remote || 'Nicht eingerichtet', disabled: props.remoteDisabled, edit: () => emit('edit-remote') }
])
watch(() => chat.current?.id, () => { picker.value = null; targetOpen.value = false })

function openPicker(kind) {
  selected.value = kind === 'workspace' ? props.repository?.repository : currentBranch.value
  search.value = ''
  picker.value = kind
  if (kind === 'workspace') emit('inspect-worktrees')
}
async function openSession(session) {
  const failure = await router.push({ name: 'chat', query: { session: session.id, ...(session.projectId ? { project: session.projectId } : {}) } })
  if (!failure) picker.value = null
}

async function copyPath() {
  try { await copyToClipboard(context.value.currentDirectory); $q.notify({ type: 'positive', message: 'Pfad kopiert.' }) }
  catch { $q.notify({ type: 'negative', message: 'Der Pfad konnte nicht kopiert werden.' }) }
}
async function run(action, values = {}) {
  try {
    if (await actions.workspaceAction(action, values, props.repository?.repository)) {
      emit('changed')
      $q.notify({ type: 'positive', message: 'Git-Aktion abgeschlossen.' })
      return true
    }
  } catch { /* Central API toast. */ }
  return false
}
async function selectWorkspace(path, create = false) {
  if (await run('select', { directory: path, create })) picker.value = null
}
async function removeWorktree(path) {
  const active = props.repository?.repository === path
  const sessions = worktrees.value.find(item => item.path === path)?.sessions || []
  const impact = sessions.length ? ` ${sessions.length} Session${sessions.length === 1 ? '' : 's'} wechseln zu Lokal: ${sessions.map(item => item.title || 'Ohne Titel').join(', ')}.` : ''
  const message = `Worktree „${path}“ entfernen? Nicht committete Änderungen sowie zusätzliche und ignorierte Dateien wie .env werden unwiderruflich gelöscht. Branch, Commits und Sessions bleiben erhalten.${impact}`
  if (!await confirm(message, 'Worktree entfernen', { okLabel: 'Entfernen' })) return
  if (await run('remove', { directory: path, force: true }) && active) picker.value = null
}
async function selectBranch(name) {
  if (name !== currentBranch.value && await run('branch-switch', { name })) picker.value = null
}
async function removeBranch(name) {
  if (await confirm(`Lokalen Branch „${name}“ löschen? Der Branch muss vollständig im Zielbranch enthalten sein.`, 'Branch löschen', { okLabel: 'Löschen' })) await run('branch-delete', { name, target_branch: props.targetBranch })
}
function promptBranch(rename) {
  $q.dialog({
    title: rename ? 'Branch umbenennen' : 'Branch erstellen', transitionDuration: 0,
    message: rename ? 'Worktree-Name und Pfad bleiben gleich. Veröffentlichte Branches werden hier nicht umbenannt.' : 'Legt einen Branch vom aktuellen Stand an. Kein Branchwechsel oder Commit.',
    prompt: { model: rename ? currentBranch.value : '', label: 'Branchname', maxlength: 300, outlined: true, dense: true, isValid: value => Boolean(value.trim()) },
    cancel: { label: 'Abbrechen', flat: true, noCaps: true }, ok: { label: 'Speichern', color: 'primary', noCaps: true }
  }).onOk(name => run(rename ? 'branch-rename' : 'branch-create', { name: name.trim() }))
}
async function setTarget(target) {
  if (busy.value) return
  try {
    await actions.setTarget(target)
    emit('changed')
    targetOpen.value = false
    $q.notify({ type: 'positive', message: 'Zielbranch gespeichert.' })
  } catch { /* Central API toast. */ }
}
</script>

<style scoped>
.git-workspace-status {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(min(100%, 13rem), 1fr));
  gap: 16px;
}

.git-workspace-path {
  overflow-wrap: anywhere;
}

.git-session-chip {
  max-width: min(100%, 14rem);
}
</style>
