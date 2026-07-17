<template>
  <q-page class="app-page">
    <AppPageHeader
      title="Einstellungen"
      subtitle="Provider, Modelle, Suche, Memory und Runtime werden hier zentral verwaltet."
    />

    <div class="settings-grid">
      <q-card
        v-for="section in sections"
        :key="section.title"
        flat
        class="app-surface app-interactive-surface"
      >
        <q-card-section class="row no-wrap items-start q-gutter-md">
          <q-avatar color="primary" text-color="white" :icon="section.icon" />
          <div class="col">
            <div class="text-subtitle1 text-weight-bold">{{ section.title }}</div>
            <div class="text-body2 app-muted q-mt-xs">{{ section.description }}</div>
            <q-chip dense square class="app-status-chip q-mt-md">Folgt</q-chip>
          </div>
        </q-card-section>
      </q-card>
    </div>
  </q-page>
</template>

<script setup>
import AppPageHeader from 'components/AppPageHeader.vue'

defineOptions({ name: 'SettingsPage' })

const sections = [
  { title: 'LLM-Provider', icon: 'hub', description: 'OpenAI-kompatible Endpunkte und Codex OAuth verwalten.' },
  { title: 'Modelle', icon: 'smart_toy', description: 'Modelle, Capabilities und Memory-Policies festlegen.' },
  { title: 'Suchanbieter', icon: 'travel_explore', description: 'Externe SearXNG-Instanzen konfigurieren und testen.' },
  { title: 'Memory', icon: 'memory', description: 'Globale Defaults und Kontextlimits verwalten.' },
  { title: 'Agent-Runtime', icon: 'settings_suggest', description: 'Schritt-, Tool- und Zeitlimits kontrollieren.' },
  { title: 'Sicherheit', icon: 'shield', description: 'Credentials, Sessions und Verbindungsstatus pruefen.' }
]
</script>
