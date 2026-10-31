<template>
  <section class="column full-height no-wrap" aria-label="Git">
    <div class="row items-center no-wrap q-pr-sm">
      <q-tabs v-model="changes.view" dense no-caps align="left" class="col" aria-label="Git-Ansicht">
        <q-tab name="changes" label="Änderungen" />
        <q-tab name="settings" label="Einstellungen" />
      </q-tabs>
      <q-btn flat dense no-caps icon="refresh" label="Aktualisieren" :loading="loading || gitState?.loading" :disable="!online || saving" @click="refresh" />
    </div>
    <q-separator />
    <q-banner v-if="!online" dense rounded class="app-surface-tonal q-ma-md" role="status">Der Computer ist nicht verbunden oder bietet keinen Git-Zugriff.</q-banner>
    <q-banner v-if="error" dense rounded class="app-surface-tonal q-ma-md" role="alert">{{ apiErrorMessage(error) }}</q-banner>
    <q-banner v-if="remoteChanged" dense rounded class="app-surface-tonal q-ma-md" role="status">
      Die Remote-Adresse dieser Session wurde geändert. Übernimm das neue Ziel ausdrücklich, bevor du veröffentlichst.
      <template #action><q-btn flat dense no-caps label="Ziel übernehmen" :disable="busy || !online" @click="openRemote" /></template>
    </q-banner>
    <q-tab-panels v-model="changes.view" keep-alive class="col bg-transparent">
      <q-tab-panel name="changes" class="q-pa-none">
        <q-scroll-area class="fit">
          <div class="q-pa-md q-gutter-y-md">
            <div v-if="gitState?.loading && !gitState.repository" class="q-pa-lg text-center" role="status"><q-spinner color="primary" size="32px" aria-label="Änderungen werden geladen" /></div>
            <AppStateMessage v-else-if="gitState?.error" icon="cloud_off" title="Änderungen konnten nicht geladen werden" :description="apiErrorMessage(gitState.error)">
              <template #actions><q-btn no-caps color="primary" label="Erneut versuchen" @click="refresh" /></template>
            </AppStateMessage>
            <template v-else-if="gitState?.repository">
              <q-input v-if="gitState.files.length" v-model="fileFilter" outlined dense clearable label="Dateien filtern" aria-label="Geänderte Dateien filtern" />
              <q-banner v-if="gitState.truncated" dense rounded class="app-surface-tonal">Die Dateiliste wurde begrenzt. Die Zähler beziehen sich auf die angezeigten Dateien.</q-banner>
              <FileChanges v-if="filteredFiles.length" :git-files="filteredFiles" :compact="false" :read-only="!online || saving" @open-diff="openGitFile" />
              <AppStateMessage v-else icon="check_circle_outline" :title="gitState.files.length ? 'Keine passenden Änderungen' : 'Keine Änderungen'" :description="gitState.files.length ? 'Passe den Dateifilter an.' : 'Es sind keine neuen, geänderten oder gelöschten Dateien vorhanden.'" />
            </template>
            <div v-else class="app-muted" role="status">{{ !chat.current ? 'Die Änderungsübersicht ist nach dem Sessionstart verfügbar.' : !changesAvailable ? 'Für die Änderungsübersicht ist ein verbundener Computer mit Git-Zugriff erforderlich.' : 'Im gewählten Workspace ist kein Git-Repository vorhanden.' }}</div>
            <section v-if="repository?.repository" aria-labelledby="git-commands-title">
              <h2 id="git-commands-title" class="text-subtitle2 q-mt-lg q-mb-sm">Agentenaufträge</h2>
              <q-card flat bordered class="app-surface overflow-hidden">
                <GitActionList :disabled="saving || !online" :target-branch="targetBranch" @refresh="refreshCommands" />
              </q-card>
            </section>
          </div>
        </q-scroll-area>
      </q-tab-panel>
      <q-tab-panel name="settings" class="q-pa-none">
        <q-scroll-area class="fit">
          <div class="q-pa-md q-gutter-y-md">
            <div v-if="!repository" class="app-muted" role="status">{{ loading ? 'Repository wird geprüft …' : 'Wähle einen Computer und Workspace mit installiertem Git.' }}</div>
            <div v-else-if="!repository.repository" class="app-muted" role="status">Im gewählten Workspace ist noch kein Git-Repository vorhanden.</div>
            <template v-if="repository?.repository">
              <GitWorkspacePanel :project="project" :repository="repository" :loading="loading" :target-branch="targetBranch"
                :remote="remote" :remote-disabled="busy || !online" @changed="refresh" @edit-remote="openRemote"
                @inspect-worktrees="refreshRepository({ worktreeDetails: true })"
                @project-defaults="projectOpen = true" />
            </template>
            <section v-if="repository && !repository.repository" aria-labelledby="git-setup-title">
              <h2 id="git-setup-title" class="text-subtitle2 q-mt-lg q-mb-sm">Repository einrichten</h2>
              <q-card flat bordered class="app-surface overflow-hidden">
                <GitActionList :disabled="saving || !online || Boolean(remoteChanged)" @refresh="refreshCommands" />
              </q-card>
            </section>
          </div>
        </q-scroll-area>
      </q-tab-panel>
    </q-tab-panels>

    <q-dialog v-model="remoteOpen" :transition-duration="0" :maximized="$q.screen.lt.sm" :persistent="saving">
      <q-card class="integration-dialog app-surface column">
        <q-form class="col column no-wrap" @submit.prevent="saveRemote">
          <q-card-section><div class="text-h6">Remote einrichten</div></q-card-section>
          <q-card-section class="col scroll q-gutter-y-md">
            <AppSelect v-model="remoteName" outlined dense label="Name" :options="repository?.remotes || []" use-input new-value-mode="add-unique" :disable="saving" :rules="[requiredRule]" @update:model-value="selectRemote" />
            <q-input v-model.trim="remoteUrl" outlined dense label="Adresse" maxlength="4096" :disable="saving" hint="HTTPS-/SSH-Adresse ohne Zugangsdaten. Die Anmeldung wird auf dem Computer eingerichtet." persistent-hint />
            <div class="text-caption app-muted">Eine Adressänderung betrifft das gemeinsame Repository und alle seine Worktrees. Sie wird als Agentenauftrag ausgeführt. Ein vorhandener unveränderter Remote wird als Ziel übernommen.</div>
            <div v-if="!chat.current && context.workspaceMode === 'worktree'" class="text-caption app-muted">Die Einrichtung startet zunächst lokal. Danach kannst du hier in den Einstellungen einen Worktree auswählen.</div>
          </q-card-section>
          <q-card-actions align="right" class="q-pa-md">
            <q-btn flat no-caps label="Abbrechen" :disable="saving" v-close-popup />
            <q-btn unelevated no-caps color="primary" label="Speichern" type="submit" :loading="saving" :disable="!online || activeRun" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>
    <ProjectDialog v-if="project" v-model="projectOpen" :project="project" />
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import AppSelect from 'src/shared/ui/AppSelect.vue'
import ProjectDialog from 'src/features/projects/ProjectDialog.vue'
import AppStateMessage from 'src/shared/ui/AppStateMessage.vue'
import FileChanges from 'src/features/chat/components/FileChanges.vue'
import { useChatStore } from 'src/features/chat/store'
import { useControlCenterStore } from 'stores/control-center'
import { useExecutionAgentStore } from 'stores/execution-agents'
import { useProjectStore } from 'stores/projects'
import { gitContext, gitContextKey, useGitChangesStore } from 'src/features/files/git-store'
import { apiErrorMessage } from 'src/utils/api-errors'
import { gitControlCenterAvailable } from 'src/app/control-center/manifest'
import { useRepositoryProbe } from './repository'
import { useGitActionStore } from './git-actions-store'
import { useCommandCatalog } from 'src/features/commands/catalog'
import GitActionList from './GitActionList.vue'
import GitWorkspacePanel from './GitWorkspacePanel.vue'

defineOptions({ name: 'GitApp' })
const $q = useQuasar()
const chat = useChatStore(), controlCenter = useControlCenterStore(), agents = useExecutionAgentStore()
const projects = useProjectStore(), commands = useGitActionStore(), changes = useGitChangesStore()
changes.view = changes.view === 'changes' ? 'changes' : 'settings'
const context = computed(() => chat.activeContext)
const project = computed(() => projects.items.find(item => item.id === (chat.current?.projectId || chat.sessionsProjectId)))
const visible = computed(() => controlCenter.open && controlCenter.activeContentId === 'git')
const online = computed(() => gitControlCenterAvailable(context.value, agents))
const changesAvailable = computed(() => online.value && Boolean(chat.current?.id)
  && agents.byId(context.value?.preferredExecutionAgentId)?.operations?.includes('git.changes'))
const overviewContext = computed(() => changesAvailable.value ? gitContext(chat.current) : null)
const gitState = computed(() => gitContextKey(changes.overview) === gitContextKey(overviewContext.value) ? changes.overview : null)
const fileFilter = ref('')
const filteredFiles = computed(() => (gitState.value?.files || []).filter(file => file.path.toLocaleLowerCase().includes((fileFilter.value || '').toLocaleLowerCase())))
const { refresh: refreshCommands } = useCommandCatalog(visible)
const saving = ref(false), remoteOpen = ref(false), projectOpen = ref(false)
const remoteName = ref('origin'), remoteUrl = ref('')
const remoteScope = ref(null)
const scopeKey = computed(() => JSON.stringify([context.value?.preferredExecutionAgentId, context.value?.currentDirectory,
  chat.current?.projectId || chat.sessionsProjectId]))
const binding = computed(() => chat.current?.codingWorkspace || {})
const { repository, loading, error, refresh: refreshRepository } = useRepositoryProbe(
  () => context.value?.preferredExecutionAgentId,
  () => context.value?.currentDirectory, visible
)
const boundContext = computed(() => binding.value.executionAgentId === context.value?.preferredExecutionAgentId
  && (Boolean(repository.value?.commonDirectory && repository.value.commonDirectory === binding.value.commonDirectory)
    || [binding.value.repository, binding.value.path].includes(repository.value?.repository)))
const projectDefaults = computed(() => project.value?.defaultExecutionAgentId === context.value?.preferredExecutionAgentId
  && (project.value?.defaultCurrentDirectory === (repository.value?.repository || context.value?.currentDirectory)
    || repository.value?.worktrees?.some(item => item.path === project.value.defaultCurrentDirectory))
  ? project.value : null)
const remote = computed(() => boundContext.value && Object.hasOwn(binding.value, 'remote')
  ? binding.value.remote : projectDefaults.value?.gitRemote || repository.value?.defaultRemote)
const targetBranch = computed(() => (boundContext.value ? binding.value.targetBranch : null) || projectDefaults.value?.gitTargetBranch || repository.value?.defaultBranch)
const activeRun = computed(() => Boolean(chat.running || chat.runId || chat.current?.activeRun))
const busy = computed(() => saving.value || commands.busy)
const remoteChanged = computed(() => boundContext.value && binding.value.remoteIdentity
  && repository.value?.remoteIdentities?.[remote.value] && binding.value.remoteIdentity !== repository.value.remoteIdentities[remote.value])
const requiredRule = value => Boolean(String(value || '').trim()) || 'Pflichtfeld'
async function refresh() {
  changes.setOverviewContext(overviewContext.value)
  await Promise.all([refreshRepository({ worktreeDetails: changes.view === 'settings' }), refreshCommands(), changes.refresh(changes.overview)])
}
watch([visible, () => gitContextKey(overviewContext.value), () => chat.running], ([shown, key, running], [wasShown, , wasRunning] = []) => {
  if (!shown) return
  const changed = key !== gitContextKey(changes.overview)
  changes.setOverviewContext(overviewContext.value)
  if (changed) fileFilter.value = ''
  if (wasShown && wasRunning && !running) void refresh()
  else if (changesAvailable.value && (changed || !wasShown)) void changes.refresh(changes.overview)
}, { immediate: true })
watch(repository, info => { if (info && !info.repository) changes.view = 'settings' })
watch(() => chat.current?.id, (id, previous) => {
  if (previous && id !== previous) { remoteOpen.value = false; projectOpen.value = false; changes.view = 'changes'; fileFilter.value = '' }
})
watch(scopeKey, () => { remoteOpen.value = false; projectOpen.value = false })

function selectRemote(name) { remoteUrl.value = repository.value?.remoteUrls?.[name] || '' }
function openRemote() {
  remoteScope.value = scopeKey.value
  remoteName.value = remote.value || repository.value?.defaultRemote || 'origin'
  selectRemote(remoteName.value)
  remoteOpen.value = true
}
async function saveRemote() {
  const name = remoteName.value?.trim(), url = remoteUrl.value.trim()
  if (!name || busy.value || !repository.value || remoteScope.value !== scopeKey.value) return
  const info = repository.value
  const scope = scopeKey.value
  saving.value = true
  try {
    const exists = info.remotes.includes(name)
    if (exists && (!url || url === info.remoteUrls?.[name])) {
      if (chat.current) {
        await commands.updateTarget({ remote: name, remote_identity: info.remoteIdentities[name] })
      } else if (projectDefaults.value) {
        const item = projectDefaults.value
        await projects.update(item.id, item.name, item.instructions, { gitRemote: name })
      } else {
        if (context.value.workspaceMode === 'worktree') await chat.updateActive({ workspace_mode: 'local' })
        if (chat.current || scopeKey.value !== scope) return
        const session = await chat.createEmptySession(controlCenter)
        if (!session || chat.current?.id !== session.id || scopeKey.value !== scope) return
        await commands.updateTarget({ remote: name, remote_identity: info.remoteIdentities[name] })
      }
      $q.notify({ type: 'positive', message: 'Git-Ziel gespeichert.' })
    } else {
      if (!url) return
      const sessionId = await commands.run('git-remote', [name, url])
      if (!sessionId) return
      $q.notify({ type: 'positive', message: 'Remote-Einrichtung gestartet.' })
    }
    remoteOpen.value = false
  } catch { /* Central API toast; keep the input for correction. */ }
  finally { saving.value = false }
}
async function openGitFile(path) {
  if (!gitState.value || !changesAvailable.value) return
  try { await changes.openFile(path, gitState.value) }
  catch { /* Central API toast. */ }
}
</script>
