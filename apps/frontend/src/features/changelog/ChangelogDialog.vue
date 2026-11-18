<template>
  <q-dialog v-model="changelog.open" :maximized="$q.screen.lt.sm" aria-labelledby="changelog-title">
    <q-card class="changelog-dialog app-surface column no-wrap" :class="{ 'changelog-dialog--mobile': $q.screen.lt.sm }">
      <q-card-section class="row items-start no-wrap q-gutter-md">
        <div class="col">
          <div id="changelog-title" class="text-h6">Changelog</div>
          <div class="text-body2 app-muted">{{ changelog.comparison ? 'Neuerungen seit deinem zuletzt geladenen Stand.' : 'Neuerungen gegenüber deiner installierten Instanz.' }}</div>
        </div>
        <q-btn flat round dense icon="close" aria-label="Changelog schließen" @click="changelog.open = false" />
      </q-card-section>
      <q-separator />
      <q-card-section class="col scroll">
        <q-select
          v-if="!changelog.comparison" v-model="changelog.channel" outlined dense emit-value map-options
          label="Vergleichsziel" :options="channels" class="q-mb-md" @update:model-value="changelog.fetchChanges()"
        />
        <div v-if="changelog.loading" class="row items-center q-gutter-sm" role="status">
          <q-spinner color="primary" /><span>Changelog wird geladen …</span>
        </div>
        <template v-else-if="result">
          <dl class="q-ma-none text-body2">
            <dt class="app-muted">{{ changelog.comparison ? 'Bisher geladen' : 'Installierte Instanz' }}</dt>
            <dd class="q-ml-none q-mb-sm">{{ versionLabel(result.base_version, result.base_revision) }}</dd>
            <dt class="app-muted">{{ changelog.comparison ? 'Jetzt geladen' : 'Neuester verfügbarer Stand' }}</dt>
            <dd class="q-ml-none q-mb-md">{{ versionLabel(result.target_version, result.target_revision) }}</dd>
          </dl>
          <p role="status">{{ statusText }}</p>
          <q-banner v-if="result.incomplete && result.status === 'ok'" rounded class="app-surface-tonal q-mb-md">
            Die Änderungsliste ist unvollständig. Den vollständigen Vergleich findest du auf GitHub.
          </q-banner>
          <section v-for="group in groups" :key="group.category" class="q-mb-md">
            <h2 class="text-subtitle1 text-weight-medium q-my-sm">{{ group.label }}</h2>
            <ul class="q-my-none q-pl-lg">
              <li v-for="(note, index) in group.notes" :key="index" class="q-mb-xs changelog-note">{{ note.text }}</li>
            </ul>
          </section>
        </template>
        <p v-else-if="changelog.failed" role="status">Der Changelog konnte nicht geladen werden.</p>
        <q-btn
          v-if="changelog.failed || result?.status === 'unavailable'" flat no-caps color="primary"
          label="Erneut versuchen" @click="changelog.fetchChanges()"
        />
      </q-card-section>
      <q-separator />
      <q-card-actions align="right" class="q-pa-md">
        <div class="full-width text-caption app-muted q-mb-sm">{{ changelog.comparison ? 'Die Weboberfläche wurde neu geladen. Hier wird kein Server-Update installiert.' : 'Nur zur Information. Hier wird kein Update installiert.' }}</div>
        <q-btn v-if="result?.compare_url" flat no-caps color="primary" label="Auf GitHub ansehen" :href="result.compare_url" target="_blank" rel="noopener noreferrer" />
        <q-btn flat no-caps label="Schließen" @click="changelog.open = false" />
      </q-card-actions>
    </q-card>
  </q-dialog>
</template>

<script setup>
import { computed } from 'vue'
import { useChangelogStore } from './store'

const changelog = useChangelogStore()
const result = computed(() => changelog.result)
const channels = [{ label: 'Neueste Veröffentlichung', value: 'stable' }, { label: 'Entwicklungsstand (develop)', value: 'develop' }]
const categories = { Added: 'Neu', Changed: 'Geändert', Fixed: 'Behoben', Security: 'Sicherheit' }
const groups = computed(() => Object.entries(categories)
  .map(([category, label]) => ({ category, label, notes: result.value?.notes.filter(note => note.category === category) || [] }))
  .filter(group => group.notes.length))
const statusText = computed(() => {
  const value = result.value
  if (value.status === 'unavailable') return 'Der Vergleich ist momentan nicht verfügbar. Prüfe die Verbindung zu GitHub oder versuche es später erneut.'
  if (value.status === 'unknown_build') return 'Der installierte Stand ist unbekannt oder enthält lokale Änderungen. Ein genauer Vergleich ist nicht möglich.'
  if (value.state === 'identical') return 'Beide Stände sind identisch.'
  if (value.state === 'ahead') return 'Dein Ausgangsstand liegt vor dem Vergleichsziel. Es gibt keine neueren Änderungen in diesem Ziel.'
  if (value.state === 'diverged') return 'Die Stände sind auseinanderentwickelt. Angezeigt werden nur Änderungen auf der Zielseite, kein vollständiger Update-Pfad.'
  if (value.state === 'unknown') return 'Die Stände konnten nicht eindeutig verglichen werden.'
  return value.notes.length ? 'Diese Neuerungen sind hinzugekommen:' : 'Für diese Commit-Spanne sind keine Release Notes hinterlegt.'
})

function versionLabel(version, revision) {
  return version || revision?.slice(0, 8) || 'Unbekannt'
}
</script>

<style scoped>
.changelog-dialog {
  width: 720px;
  max-width: 100%;
  max-height: 90dvh;
}
.changelog-dialog--mobile { max-height: 100dvh; }
.changelog-note { overflow-wrap: anywhere; }
</style>
