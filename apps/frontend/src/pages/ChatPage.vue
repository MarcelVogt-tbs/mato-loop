<template>
  <q-page class="chat-page column no-wrap">
    <div class="chat-page__content col column items-center justify-center q-pa-lg">
      <div class="chat-welcome text-center">
        <q-avatar rounded color="primary" text-color="white" icon="all_inclusive" size="72px" />
        <h1 class="text-h4 text-weight-bold q-mb-sm">Willkommen bei MatoLoop</h1>
        <p class="text-body1 app-muted q-mx-auto">
          Deine selbst gehostete Basis fuer persistente AI-Sessions, kontrolliertes Memory und nachvollziehbare Websuche.
        </p>

        <div class="row justify-center q-gutter-sm q-mt-lg">
          <q-chip outline color="primary" icon="forum">Persistente Sessions</q-chip>
          <q-chip outline color="primary" icon="memory">Memory-Policy</q-chip>
          <q-chip outline color="primary" icon="travel_explore">SearXNG</q-chip>
          <q-chip outline color="primary" icon="key">Codex OAuth</q-chip>
        </div>
      </div>
    </div>

    <div class="chat-composer q-pa-md">
      <div class="chat-composer__controls row items-center q-gutter-x-md q-mb-sm">
        <q-toggle v-model="memoryEnabled" dense label="Memory verwenden" disable>
          <q-tooltip>Memory folgt in einem spaeteren Slice</q-tooltip>
        </q-toggle>
        <q-toggle v-model="forceWebSearch" dense label="Websuche erzwingen" disable>
          <q-tooltip>Die SearXNG-Anbindung folgt in einem spaeteren Slice</q-tooltip>
        </q-toggle>
        <q-space />
        <q-chip dense square class="app-status-chip" icon="smart_toy">Kein Modell konfiguriert</q-chip>
      </div>

      <q-card flat class="app-surface chat-composer__card">
        <q-form class="row items-end no-wrap q-gutter-x-sm">
          <q-input
            v-model="message"
            borderless
            autogrow
            type="textarea"
            rows="1"
            class="col"
            placeholder="Eine Nachricht an MatoLoop senden"
            disable
          />
          <q-btn
            round
            unelevated
            color="primary"
            icon="arrow_upward"
            aria-label="Nachricht senden"
            disable
          />
        </q-form>
      </q-card>
    </div>
  </q-page>
</template>

<script setup>
import { ref } from 'vue'

defineOptions({ name: 'ChatPage' })

const message = ref('')
const memoryEnabled = ref(false)
const forceWebSearch = ref(false)
</script>
