<template>
  <section class="memory-page">
    <header class="settings-section-header">
      <div>
        <h2 class="text-h6 text-weight-bold q-my-none">Memory</h2>
        <p class="text-body2 app-muted q-mb-none q-mt-xs">
          Langfristige Erinnerungen durchsuchen, pflegen und ihre Hintergrundverarbeitung steuern.
        </p>
      </div>
      <q-btn
        v-if="section === 'memories'"
        unelevated
        no-caps
        color="primary"
        icon="add"
        label="Erinnerung hinzufügen"
        @click="openCreateDialog"
      />
    </header>

    <q-tabs
      v-model="section"
      dense
      no-caps
      align="left"
      active-color="primary"
      indicator-color="primary"
      class="memory-tabs q-mb-lg"
    >
      <q-tab name="memories" icon="psychology" label="Erinnerungen" />
      <q-tab name="dreaming" icon="bedtime" label="Dreaming" />
    </q-tabs>

    <q-tab-panels
      v-model="section"
      animated
      transition-prev="fade"
      transition-next="fade"
      class="memory-panels bg-transparent"
    >
      <q-tab-panel name="memories" class="q-pa-none">
        <q-card flat class="app-surface memory-access-card q-mb-md">
          <q-card-section class="memory-access-header row items-center no-wrap q-gutter-md">
            <q-avatar rounded color="primary" text-color="white" icon="psychology" />
            <div class="col">
              <div class="text-subtitle1 text-weight-medium">Memory-Zugriff</div>
              <div class="text-body2 app-muted">
                Steuert den Memory-Zugriff für deine Sessions sowie manuelles und automatisches Dreaming.
              </div>
            </div>
            <q-toggle
              :model-value="settingsForm.enabled"
              color="primary"
              label="Memory aktiv"
              :disable="!settingsLoaded || memories.settingsLoading"
              @update:model-value="toggleMemory"
            />
          </q-card-section>
          <q-inner-loading :showing="memories.settingsLoading && !settingsLoaded" />
        </q-card>

        <q-card flat class="app-surface memory-filter-card q-mb-md">
          <q-card-section>
            <div class="memory-category-filter row items-center q-gutter-xs q-mb-md">
              <q-chip
                v-for="option in categoryFilterOptions"
                :key="option.value || 'all'"
                clickable
                :outline="filters.category !== option.value"
                :color="filters.category === option.value ? 'primary' : undefined"
                :text-color="filters.category === option.value ? 'white' : undefined"
                :icon="option.icon"
                @click="setCategoryFilter(option.value)"
              >
                {{ option.label }}
              </q-chip>
            </div>
            <div class="memory-filters">
              <q-input
                v-model="filters.query"
                outlined
                dense
                clearable
                debounce="350"
                label="Erinnerungen durchsuchen"
                class="memory-search"
                @update:model-value="applyFilters"
              >
                <template #prepend><q-icon name="search" /></template>
              </q-input>
              <q-select
                v-model="filters.sourceType"
                outlined
                dense
                clearable
                emit-value
                map-options
                label="Quelle"
                :options="sourceTypeOptions"
                @update:model-value="applyFilters"
              />
              <q-select
                v-model="filters.trustLevel"
                outlined
                dense
                clearable
                emit-value
                map-options
                label="Vertrauen"
                :options="trustLevelOptions"
                @update:model-value="applyFilters"
              />
              <q-select
                v-model="filters.retrievalLevel"
                outlined
                dense
                clearable
                emit-value
                map-options
                label="Abruf"
                :options="retrievalLevelOptions"
                @update:model-value="applyFilters"
              />
              <q-btn
                flat
                round
                icon="filter_alt_off"
                aria-label="Filter zurücksetzen"
                :disable="!filtersActive"
                @click="resetFilters"
              >
                <q-tooltip>Filter zurücksetzen</q-tooltip>
              </q-btn>
            </div>
          </q-card-section>
        </q-card>

        <q-card flat class="app-surface memory-list-card">
          <q-card-section v-if="!memories.loading && !memories.items.length" class="memory-empty text-center">
            <q-icon name="psychology_alt" size="48px" color="grey-6" />
            <div class="text-subtitle1 text-weight-medium q-mt-sm">
              {{ filtersActive ? 'Keine passenden Erinnerungen' : 'Noch keine Erinnerungen' }}
            </div>
            <div class="text-body2 app-muted q-mt-xs">
               {{ filtersActive ? 'Passe die Suche oder die Filter an.' : 'Füge eine Erinnerung manuell hinzu oder starte Dreaming.' }}
            </div>
          </q-card-section>

          <q-list v-else separator>
            <q-item v-for="item in memories.items" :key="item.id" class="memory-item">
              <q-item-section>
                <div class="memory-item__chips row items-center q-gutter-xs q-mb-sm">
                  <q-chip
                    v-if="!item.seenAt"
                    dense
                    square
                    color="positive"
                    text-color="white"
                    icon="fiber_new"
                  >
                    Neu
                  </q-chip>
                  <q-chip
                    dense
                    square
                    :color="categoryMeta(item.category).color"
                    text-color="white"
                    :icon="categoryMeta(item.category).icon"
                  >
                    {{ categoryMeta(item.category).label }}
                  </q-chip>
                  <q-chip dense square :icon="sourceMeta(item.sourceType).icon">
                    {{ sourceMeta(item.sourceType).label }}
                  </q-chip>
                  <q-chip
                    dense
                    square
                    :color="item.trustLevel === 'private' ? 'deep-purple' : 'grey-7'"
                    text-color="white"
                    :icon="item.trustLevel === 'private' ? 'lock' : 'public'"
                  >
                    {{ trustLevelLabel(item.trustLevel) }}
                  </q-chip>
                  <q-chip dense square outline icon="manage_search">
                    {{ retrievalLevelLabel(item.retrievalLevel) }}
                  </q-chip>
                </div>
                <div class="memory-item__content text-body1">{{ item.content }}</div>
                <div class="memory-item__meta row items-center q-gutter-x-md q-gutter-y-xs q-mt-sm text-caption app-muted">
                  <router-link
                    v-if="item.sourceSession"
                    :to="{ name: 'chat', query: { session: item.sourceSession.id } }"
                    class="memory-source-link"
                  >
                    <q-icon name="forum" /> {{ item.sourceSession.title }}
                  </router-link>
                  <span>Aktualisiert {{ formatDate(item.updatedAt) }}</span>
                </div>
              </q-item-section>
              <q-item-section side top class="memory-item__actions">
                <q-btn flat round dense icon="edit" :aria-label="`Erinnerung bearbeiten`" @click="openEditDialog(item)">
                  <q-tooltip>Bearbeiten</q-tooltip>
                </q-btn>
                <q-btn flat round dense color="negative" icon="delete_outline" aria-label="Erinnerung löschen" @click="removeMemory(item)">
                  <q-tooltip>Löschen</q-tooltip>
                </q-btn>
              </q-item-section>
            </q-item>
          </q-list>

          <q-separator v-if="memories.total" />
          <q-card-actions v-if="memories.total" class="memory-pagination q-pa-md">
            <span class="text-caption app-muted">{{ resultRange }} von {{ memories.total }}</span>
            <q-space />
            <q-select
              v-model="pageSize"
              dense
              outlined
              emit-value
              map-options
              options-dense
              label="Pro Seite"
              :options="pageSizeOptions"
              @update:model-value="changePageSize"
            />
            <q-pagination
              v-model="page"
              color="primary"
              direction-links
              boundary-links
              :max="pageCount"
              :max-pages="$q.screen.lt.sm ? 3 : 7"
              @update:model-value="changePage"
            />
          </q-card-actions>
          <q-inner-loading :showing="memories.loading" />
        </q-card>
      </q-tab-panel>

      <q-tab-panel name="dreaming" class="q-pa-none">
        <div class="dreaming-grid">
          <q-card flat class="app-surface dreaming-settings-card">
            <q-card-section class="dreaming-settings-header row items-start no-wrap q-gutter-md">
              <q-avatar rounded color="deep-purple" text-color="white" icon="bedtime" />
              <div class="col">
                <div class="text-h6">Dreaming-Einstellungen</div>
                <div class="text-body2 app-muted">Erinnerungen automatisch aus abgeschlossenen Gesprächen ableiten.</div>
              </div>
              <div v-if="settingsLoaded" class="column items-end">
                <q-toggle
                  :model-value="settingsForm.automaticDreamingEnabled"
                  color="deep-purple"
                  label="Automatisches Dreaming aktiv"
                  :disable="!settingsForm.enabled || memories.settingsLoading"
                  @update:model-value="toggleAutomaticDreaming"
                />
              </div>
            </q-card-section>
            <q-separator />
            <q-form class="dreaming-settings-form" @submit.prevent="saveSettings">
              <q-card-section>
                 <q-banner v-if="settingsForm.automaticDreamingUnavailableReason" rounded class="app-surface-tonal q-mb-md">
                   <template #avatar><q-icon name="info_outline" color="warning" /></template>
                   {{ automaticUnavailableReasonMessage }}
                 </q-banner>
                 <q-input
                  v-model="settingsForm.dreamingInstructions"
                  outlined
                  autogrow
                  type="textarea"
                  maxlength="8000"
                  counter
                  label="Zusätzliche Dreaming-Hinweise"
                    hint="Kann Schwerpunkte, Kategorien und Abrufarten festlegen. Vertrauen, Sicherheit und Evidenz bleiben hostkontrolliert."
                   class="q-mb-lg"
                   :disable="memories.settingsLoading"
                 />
                 <div class="dreaming-number-grid">
                   <q-input v-model.number="settingsForm.maxContextItems" outlined type="number" min="0" max="100" label="Max. Kontexteinträge" :disable="memories.settingsLoading" :rules="[integerRangeRule(0, 100)]" />
                   <q-input v-model.number="settingsForm.maxContextCharacters" outlined type="number" min="0" max="200000" label="Max. Kontextzeichen" :disable="memories.settingsLoading" :rules="[integerRangeRule(0, 200000)]" />
                   <q-input v-model.number="settingsForm.dreamingIntervalMinutes" outlined type="number" min="1" max="10080" label="Intervall (Minuten)" :disable="memories.settingsLoading" :rules="[integerRangeRule(1, 10080)]" />
                   <q-input v-model.number="settingsForm.dreamingIdleMinutes" outlined type="number" min="1" max="10080" label="Leerlauf (Minuten)" :disable="memories.settingsLoading" :rules="[integerRangeRule(1, 10080)]" />
                   <q-input v-model.number="settingsForm.dreamingMaxBatchMessages" outlined type="number" min="2" max="500" label="Max. Nachrichten pro Lauf" :disable="memories.settingsLoading" :rules="[integerRangeRule(2, 500)]" />
                   <q-input v-model.number="settingsForm.dreamingMaxCandidates" outlined type="number" min="1" max="100" label="Max. Kandidaten" :disable="memories.settingsLoading" :rules="[integerRangeRule(1, 100)]" />
                </div>
              </q-card-section>
              <q-card-actions align="right" class="q-pa-md q-pt-none">
                <q-btn
                  unelevated
                  no-caps
                  color="primary"
                  icon="save"
                  label="Einstellungen speichern"
                  type="submit"
                  :loading="memories.settingsLoading"
                  :disable="!settingsLoaded || !settingsChanged"
                />
              </q-card-actions>
            </q-form>
            <q-inner-loading :showing="memories.settingsLoading && !settingsLoaded" />
          </q-card>

          <q-card flat class="app-surface dream-runs-card">
            <q-card-section>
              <div class="row items-start q-gutter-md">
                <q-avatar rounded color="secondary" text-color="white" icon="play_circle" />
                <div class="col">
                  <div class="text-h6">Verarbeitung</div>
                  <div class="text-body2 app-muted">Dreaming sofort für alle oder eine bestimmte Session anstoßen.</div>
                </div>
              </div>
              <q-banner
                v-if="settingsForm.manualDreamingUnavailableReason"
                rounded
                class="app-surface-tonal q-mt-lg"
              >
                <template #avatar><q-icon name="info_outline" color="warning" /></template>
                {{ manualUnavailableReasonMessage }}
              </q-banner>
              <q-select
                v-model="dreamSessionId"
                outlined
                clearable
                emit-value
                map-options
                options-dense
                label="Session (optional)"
                :options="sessionOptions"
                class="q-mt-lg"
                :behavior="$q.screen.lt.sm ? 'dialog' : 'menu'"
              />
              <q-btn
                unelevated
                no-caps
                color="deep-purple"
                icon="auto_awesome"
                label="Jetzt verarbeiten"
                class="full-width q-mt-md"
                :loading="memories.dreamRunStarting"
                :disable="!canStartDreaming"
                @click="startDreamRun"
              />
            </q-card-section>
            <q-separator />
            <q-card-section>
              <div class="row items-center q-mb-sm">
                <div class="text-subtitle1 text-weight-medium">Letzte Läufe</div>
                <q-space />
                <q-btn flat round dense icon="refresh" aria-label="Dreaming-Läufe aktualisieren" :loading="memories.dreamRunsLoading" @click="refreshDreamRuns" />
              </div>
              <div v-if="!memories.dreamRunsLoading && !memories.dreamRuns.length" class="text-body2 app-muted q-py-md">
                Noch keine Dreaming-Läufe vorhanden.
              </div>
              <q-list v-else separator>
                <q-item v-for="run in memories.dreamRuns" :key="run.id" class="q-px-none">
                  <q-item-section avatar>
                    <q-icon :name="dreamRunMeta(run.status).icon" :color="dreamRunMeta(run.status).color" />
                  </q-item-section>
                  <q-item-section>
                    <q-item-label>{{ dreamRunMeta(run.status).label }}</q-item-label>
                    <q-item-label caption>
                      {{ dreamRunSession(run) }} · {{ formatDate(run.createdAt) }}
                    </q-item-label>
                    <q-item-label v-if="run.error" caption class="text-negative">
                      {{ dreamRunError(run) }}
                    </q-item-label>
                  </q-item-section>
                  <q-item-section v-if="run.status === 'completed' && run.createdMemories != null" side class="items-end">
                    <q-chip dense square>{{ run.createdMemories }} neu</q-chip>
                    <q-chip v-if="run.updatedMemories" dense square color="deep-purple" text-color="white">
                      {{ run.updatedMemories }} aktualisiert
                    </q-chip>
                  </q-item-section>
                </q-item>
              </q-list>
            </q-card-section>
            <q-inner-loading :showing="memories.dreamRunsLoading && !memories.dreamRuns.length" />
          </q-card>
        </div>
      </q-tab-panel>
    </q-tab-panels>

    <q-dialog v-model="memoryDialogOpen" :maximized="$q.screen.lt.sm" :persistent="savingMemory">
      <q-card class="memory-dialog app-surface">
        <q-form ref="memoryFormRef" class="column full-height" @submit.prevent="saveMemory">
          <q-card-section class="row items-start no-wrap q-gutter-md">
            <q-avatar color="primary" text-color="white" :icon="editingMemory ? 'edit' : 'add'" />
            <div class="col">
              <div class="text-h6">{{ editingMemory ? 'Erinnerung bearbeiten' : 'Erinnerung hinzufügen' }}</div>
              <div class="text-body2 app-muted">Inhalt, Kategorie, Vertrauen und Abrufverhalten festlegen.</div>
            </div>
            <q-btn flat round dense icon="close" aria-label="Dialog schließen" :disable="savingMemory" v-close-popup />
          </q-card-section>
          <q-separator />
          <q-card-section class="col scroll">
            <q-input
              v-model="memoryForm.content"
              outlined
              autofocus
              autogrow
              type="textarea"
              label="Inhalt"
              maxlength="20000"
              lazy-rules
              :rules="[requiredRule]"
            />
            <div class="memory-dialog__fields q-mt-sm">
              <q-select
                v-model="memoryForm.category"
                outlined
                emit-value
                map-options
                label="Kategorie"
                :options="categoryOptions"
                lazy-rules
                :rules="[requiredRule]"
              />
              <q-select v-model="memoryForm.trustLevel" outlined emit-value map-options label="Vertrauen" :options="trustLevelOptions" />
              <q-select v-model="memoryForm.retrievalLevel" outlined emit-value map-options label="Abruf" :options="retrievalLevelOptions" />
            </div>
            <div v-if="editingMemory" class="memory-source app-surface-tonal q-pa-md q-mt-md rounded-borders">
              <div class="text-subtitle2">Quelle</div>
              <div class="text-body2 app-muted q-mt-xs">Die Herkunft bleibt unverändert.</div>
              <div class="q-mt-sm">{{ sourceDescription(editingMemory) }}</div>
              <div v-if="editingMemory.sourceMessageIds?.length" class="text-caption app-muted q-mt-xs">
                Nachrichten: {{ editingMemory.sourceMessageIds.join(', ') }}
              </div>
              <div v-if="editingMemory.sourceRunId" class="text-caption app-muted q-mt-xs">
                Run: {{ editingMemory.sourceRunId }}
              </div>
            </div>
          </q-card-section>
          <q-separator />
          <q-card-actions align="right" class="q-pa-md">
            <q-btn flat no-caps label="Abbrechen" :disable="savingMemory" v-close-popup />
            <q-btn unelevated no-caps color="primary" label="Speichern" type="submit" :loading="savingMemory" />
          </q-card-actions>
        </q-form>
      </q-card>
    </q-dialog>
  </section>
</template>

<script setup>
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useQuasar } from 'quasar'
import { useConfirm } from 'src/composables/confirm'
import { useChatStore } from 'stores/chat'
import { useMemoryStore } from 'stores/memories'
import { apiErrorMessage } from 'src/utils/api-errors'

defineOptions({ name: 'MemorySettingsPage' })

const $q = useQuasar()
const { confirm } = useConfirm()
const chat = useChatStore()
const memories = useMemoryStore()
const section = ref('memories')
const page = ref(1)
const pageSize = ref(20)
const memoryDialogOpen = ref(false)
const memoryFormRef = ref(null)
const editingMemory = ref(null)
const savingMemory = ref(false)
const settingsLoaded = ref(false)
const savedSettings = ref(null)
const dreamSessionId = ref(null)
const filters = reactive({ query: '', category: null, sourceType: null, trustLevel: null, retrievalLevel: null })
const memoryForm = reactive({ content: '', category: null, trustLevel: 'public', retrievalLevel: 'retrieve' })
const settingsForm = reactive({
  enabled: false,
  maxContextItems: 1,
  maxContextCharacters: 1,
  automaticDreamingEnabled: false,
  dreamingInstructions: '',
  dreamingIntervalMinutes: 1,
  dreamingIdleMinutes: 1,
  dreamingMaxBatchMessages: 2,
  dreamingMaxCandidates: 1,
  automaticDreamingUnavailableReason: null,
  manualDreamingAvailable: false,
  manualDreamingUnavailableReason: null
})
const sourceTypeOptions = [
  { label: 'Tool-Aufruf', value: 'tool_call' },
  { label: 'Dreaming', value: 'dreaming' },
  { label: 'Manuell', value: 'manual' }
]
const trustLevelOptions = [
  { label: 'Öffentlich', value: 'public' },
  { label: 'Privat', value: 'private' }
]
const retrievalLevelOptions = [
  { label: 'Sofort im Kontext', value: 'instant' },
  { label: 'Bei Bedarf abrufen', value: 'retrieve' }
]
const categoryOptions = [
  { label: 'Kontext', value: 'context', icon: 'notes', color: 'blue-grey-7' },
  { label: 'Fakt', value: 'fact', icon: 'lightbulb', color: 'blue-7' },
  { label: 'Ziel', value: 'goal', icon: 'flag', color: 'teal-7' },
  { label: 'Identität', value: 'identity', icon: 'person', color: 'indigo-7' },
  { label: 'Präferenz', value: 'preference', icon: 'favorite', color: 'deep-purple-7' }
]
const categoryFilterOptions = [{ label: 'Alle', value: null, icon: 'apps' }, ...categoryOptions]
const pageSizeOptions = [10, 20, 50].map((value) => ({ label: String(value), value }))
const sourceTypeMeta = {
  tool_call: { label: 'Tool-Aufruf', icon: 'build' },
  dreaming: { label: 'Dreaming', icon: 'bedtime' },
  manual: { label: 'Manuell', icon: 'edit_note' }
}

const filtersActive = computed(() => Object.values(filters).some(Boolean))
const pageCount = computed(() => Math.max(1, Math.ceil(memories.total / pageSize.value)))
const resultRange = computed(() => {
  if (!memories.total) return '0'
  return `${memories.offset + 1}-${Math.min(memories.offset + memories.items.length, memories.total)}`
})
const sessionOptions = computed(() => chat.sessions.map((session) => ({
  label: session.title,
  value: session.id,
  disable: session.memoryMode !== 'full'
})))
const editableTechnicalSettings = computed(() => ({
  maxContextItems: Number(settingsForm.maxContextItems),
  maxContextCharacters: Number(settingsForm.maxContextCharacters),
  dreamingInstructions: settingsForm.dreamingInstructions,
  dreamingIntervalMinutes: Number(settingsForm.dreamingIntervalMinutes),
  dreamingIdleMinutes: Number(settingsForm.dreamingIdleMinutes),
  dreamingMaxBatchMessages: Number(settingsForm.dreamingMaxBatchMessages),
  dreamingMaxCandidates: Number(settingsForm.dreamingMaxCandidates)
}))
const settingsChanged = computed(() => (
  JSON.stringify(editableTechnicalSettings.value) !== JSON.stringify(savedSettings.value)
))
const canStartDreaming = computed(() => Boolean(
  settingsLoaded.value
  && memories.settings?.manualDreamingAvailable
  && !settingsChanged.value
))
const automaticUnavailableReasonMessage = computed(() => settingsForm.automaticDreamingUnavailableReason
  ? apiErrorMessage({ error: { code: settingsForm.automaticDreamingUnavailableReason } })
  : '')
const manualUnavailableReasonMessage = computed(() => settingsForm.manualDreamingUnavailableReason
  ? apiErrorMessage({ error: { code: settingsForm.manualDreamingUnavailableReason } })
  : '')

function listParams(offset = (page.value - 1) * pageSize.value) {
  return { ...filters, offset, limit: pageSize.value }
}

async function loadMemories(offset) {
  try {
    const result = await memories.fetchMemories(listParams(offset))
    if (!result) return
    page.value = Math.floor(memories.offset / memories.limit) + 1
    const unseenIds = result.items.filter((item) => !item.seenAt).map((item) => item.id)
    if (unseenIds.length) {
      await nextTick()
      void memories.acknowledgeMemories(unseenIds).catch(() => {})
    }
  } catch {
    // Vorhandene Ergebnisse bleiben sichtbar; der zentrale Interceptor zeigt den Fehler.
  }
}

function applyFilters() {
  page.value = 1
  void loadMemories(0)
}

function resetFilters() {
  Object.assign(filters, { query: '', category: null, sourceType: null, trustLevel: null, retrievalLevel: null })
  applyFilters()
}

function setCategoryFilter(value) {
  filters.category = value
  applyFilters()
}

function changePage(value) {
  void loadMemories((value - 1) * pageSize.value)
}

function changePageSize() {
  page.value = 1
  void loadMemories(0)
}

function openCreateDialog() {
  editingMemory.value = null
  Object.assign(memoryForm, { content: '', category: null, trustLevel: 'public', retrievalLevel: 'retrieve' })
  memoryDialogOpen.value = true
}

function openEditDialog(item) {
  editingMemory.value = item
  Object.assign(memoryForm, {
    content: item.content,
    category: item.category,
    trustLevel: item.trustLevel,
    retrievalLevel: item.retrievalLevel
  })
  memoryDialogOpen.value = true
}

async function saveMemory() {
  if (!await memoryFormRef.value?.validate()) return
  if (editingMemory.value?.trustLevel === 'private' && memoryForm.trustLevel === 'public') {
    const accepted = await confirm(
      'Die Erinnerung wird danach auch für öffentliche Sessions und Modelle verfügbar. Wirklich veröffentlichen?',
      'Privatsphäre herabstufen',
      { okLabel: 'Veröffentlichen', okColor: 'warning' }
    )
    if (!accepted) return
  }

  savingMemory.value = true
  try {
    const payload = {
      content: memoryForm.content.trim(),
      category: memoryForm.category,
      trustLevel: memoryForm.trustLevel,
      retrievalLevel: memoryForm.retrievalLevel
    }
    if (editingMemory.value) {
      await memories.updateMemory(editingMemory.value.id, payload)
      page.value = 1
      await loadMemories(0)
      $q.notify({ type: 'positive', message: 'Erinnerung gespeichert' })
    } else {
      await memories.createMemory(payload)
      await loadMemories(0)
      $q.notify({ type: 'positive', message: 'Erinnerung hinzugefügt' })
    }
    memoryDialogOpen.value = false
  } catch {
    // Der zentrale API-Interceptor zeigt den Fehler; der Dialog bleibt geöffnet.
  } finally {
    savingMemory.value = false
  }
}

async function removeMemory(item) {
  const accepted = await confirm(
    'Diese Erinnerung dauerhaft löschen?',
    'Erinnerung löschen',
    { okLabel: 'Löschen' }
  )
  if (!accepted) return
  try {
    await memories.deleteMemory(item.id)
    const targetPage = Math.min(page.value, Math.max(1, Math.ceil(memories.total / pageSize.value)))
    page.value = targetPage
    await loadMemories((targetPage - 1) * pageSize.value)
    $q.notify({ type: 'positive', message: 'Erinnerung gelöscht' })
  } catch {
    // Der zentrale API-Interceptor zeigt den Fehler.
  }
}

function assignSettings(settings) {
  Object.assign(settingsForm, settings)
  savedSettings.value = { ...editableTechnicalSettings.value }
  settingsLoaded.value = true
}

async function loadSettings() {
  try {
    assignSettings(await memories.fetchSettings())
  } catch {
    // Der zentrale API-Interceptor zeigt den Fehler.
  }
}

async function saveSettings() {
  try {
    assignSettings(await memories.saveSettings(editableTechnicalSettings.value))
    $q.notify({ type: 'positive', message: 'Memory-Einstellungen gespeichert' })
  } catch {
    // Der zentrale API-Interceptor zeigt den Fehler.
  }
}

async function toggleMemory(value) {
  await saveToggle('enabled', value, value ? 'Memory aktiviert' : 'Memory deaktiviert')
}

async function toggleAutomaticDreaming(value) {
  await saveToggle(
    'automaticDreamingEnabled',
    value,
    value ? 'Automatisches Dreaming aktiviert' : 'Automatisches Dreaming deaktiviert'
  )
}

async function saveToggle(field, value, message) {
  const previous = settingsForm[field]
  const unsavedTechnicalSettings = { ...editableTechnicalSettings.value }
  settingsForm[field] = value
  try {
    const settings = await memories.saveSettings({ [field]: value })
    Object.assign(settingsForm, settings, unsavedTechnicalSettings)
    $q.notify({ type: 'positive', message })
  } catch {
    settingsForm[field] = previous
  }
}

async function refreshDreamRuns() {
  try {
    await memories.fetchDreamRuns()
  } catch {
    // Der zentrale API-Interceptor zeigt den Fehler.
  }
}

async function startDreamRun() {
  try {
    const result = await memories.startDreamRun(dreamSessionId.value)
    $q.notify({
      type: result.queued > 0 ? 'positive' : 'info',
      message: result.queued > 0
        ? `${result.queued} ${result.queued === 1 ? 'Dreaming-Lauf' : 'Dreaming-Läufe'} eingeplant`
        : 'Keine neue Dreaming-Verarbeitung erforderlich'
    })
  } catch {
    // Der zentrale API-Interceptor zeigt den Fehler.
  }
}

function integerRangeRule(minimum, maximum) {
  return (value) => (
    Number.isInteger(Number(value))
    && Number(value) >= minimum
    && Number(value) <= maximum
  ) || `Bitte gib eine ganze Zahl zwischen ${minimum} und ${maximum} ein.`
}

function requiredRule(value) {
  return Boolean(String(value || '').trim()) || 'Pflichtfeld'
}

function trustLevelLabel(value) {
  return value === 'private' ? 'Privat' : 'Öffentlich'
}

function retrievalLevelLabel(value) {
  return value === 'instant' ? 'Sofort' : 'Bei Bedarf'
}

function sourceDescription(item) {
  const source = sourceMeta(item.sourceType).label
  return item.sourceSession ? `${source} · ${item.sourceSession.title}` : source
}

function sourceMeta(value) {
  return sourceTypeMeta[value] || { label: value || 'Unbekannt', icon: 'help_outline' }
}

function categoryMeta(value) {
  return categoryOptions.find((item) => item.value === value)
    || { label: value || 'Unbekannt', icon: 'help_outline', color: 'grey-7' }
}

function formatDate(value) {
  if (!value) return 'unbekannt'
  return new Intl.DateTimeFormat('de-DE', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
}

function dreamRunMeta(status) {
  return ({
    queued: { label: 'Eingeplant', icon: 'schedule', color: 'grey-7' },
    running: { label: 'Wird verarbeitet', icon: 'sync', color: 'primary' },
    blocked: { label: 'Blockiert', icon: 'block', color: 'warning' },
    completed: { label: 'Abgeschlossen', icon: 'check_circle', color: 'positive' },
    failed: { label: 'Fehlgeschlagen', icon: 'error', color: 'negative' },
    cancelled: { label: 'Abgebrochen', icon: 'cancel', color: 'grey-7' }
  })[status] || { label: status || 'Unbekannt', icon: 'help', color: 'grey-7' }
}

function dreamRunSession(run) {
  return run.sourceSession?.title || run.session?.title || run.sessionId || 'Alle Sessions'
}

function dreamRunError(run) {
  return apiErrorMessage(run.error)
}

watch(section, (value, previous) => {
  if (value === 'memories' && previous !== 'memories') {
    page.value = 1
    void loadMemories(0)
  }
})

onMounted(() => {
  void loadMemories(0)
  void loadSettings()
  void refreshDreamRuns()
  if (!chat.sessions.length) chat.fetchSessions().catch(() => {})
})
</script>

<style scoped>
.memory-page {
  width: min(1240px, 100%);
  margin: 0 auto;
}

.memory-tabs {
  border-bottom: 1px solid var(--app-surface-border);
}

.memory-filter-card,
.memory-list-card,
.memory-access-card,
.dreaming-settings-card,
.dream-runs-card {
  position: relative;
}

.memory-filters {
  display: grid;
  grid-template-columns: minmax(260px, 2fr) repeat(3, minmax(150px, 1fr)) auto;
  align-items: start;
  gap: 12px;
}

.memory-category-filter {
  min-width: 0;
}

.memory-item {
  align-items: flex-start;
  padding: 20px 24px;
}

.memory-item__content {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.55;
}

.memory-item__actions {
  flex-direction: row;
  gap: 4px;
}

.memory-source-link {
  color: var(--q-primary);
  text-decoration: none;
}

.memory-empty {
  padding: 64px 24px;
}

.memory-pagination {
  gap: 12px;
}

.memory-pagination .q-select {
  width: 96px;
}

.dreaming-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.5fr) minmax(320px, 0.8fr);
  align-items: start;
  gap: 20px;
}

.dreaming-number-grid,
.memory-dialog__fields {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px 16px;
}

.memory-dialog {
  width: min(720px, calc(100vw - 32px));
  max-width: 720px;
  max-height: calc(100dvh - 32px);
}

@media (max-width: 1000px) {
  .memory-filters {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .memory-search {
    grid-column: 1 / -1;
  }

  .dreaming-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 700px) {
  .memory-access-header,
  .dreaming-settings-header {
    align-items: flex-start;
    flex-wrap: wrap;
  }

  .memory-access-header .q-toggle,
  .dreaming-settings-header > .column {
    align-items: flex-start;
    width: 100%;
  }

  .memory-filters,
  .dreaming-number-grid,
  .memory-dialog__fields {
    grid-template-columns: minmax(0, 1fr);
  }

  .memory-item {
    flex-wrap: wrap;
    padding: 16px;
  }

  .memory-item__actions {
    width: 100%;
    justify-content: flex-end;
    padding-top: 8px;
  }

  .memory-pagination {
    align-items: stretch;
    flex-direction: column;
  }

  .memory-pagination .q-space {
    display: none;
  }

  .memory-pagination .q-select {
    width: 100%;
  }

  .memory-pagination .q-pagination {
    justify-content: center;
  }

  .memory-dialog .q-card__actions {
    align-items: stretch;
    flex-direction: column;
  }

  .memory-dialog {
    width: 100%;
    max-width: none;
    max-height: none;
    border-radius: 0;
  }
}
</style>
