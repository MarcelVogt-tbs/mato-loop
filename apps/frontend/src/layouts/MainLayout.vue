<template>
  <q-layout view="hHh Lpr fFf" class="app-shell">
    <q-header class="app-header text-dark">
      <q-toolbar class="app-toolbar">
        <q-btn
          flat
          round
          dense
          icon="menu"
          aria-label="Navigation umschalten"
          @click="drawerOpen = !drawerOpen"
        />

        <router-link to="/" class="app-brand row no-wrap items-center q-gutter-sm">
          <q-avatar rounded color="primary" text-color="white" icon="all_inclusive" size="36px" />
          <div>
            <div class="app-brand__name">MatoLoop</div>
            <div class="app-brand__tagline gt-xs">Personal Agent Workspace</div>
          </div>
        </router-link>

        <q-space />

        <q-chip dense square class="app-status-chip" icon="dns">Scaffold</q-chip>
        <q-btn
          flat
          round
          dense
          :icon="$q.dark.isActive ? 'light_mode' : 'dark_mode'"
          :aria-label="$q.dark.isActive ? 'Hellen Modus aktivieren' : 'Dunklen Modus aktivieren'"
          @click="$q.dark.toggle()"
        />
      </q-toolbar>
    </q-header>

    <q-drawer
      v-model="drawerOpen"
      show-if-above
      :width="288"
      :breakpoint="900"
      class="app-drawer"
    >
      <div class="column full-height no-wrap">
        <div class="q-pa-md">
          <q-btn
            color="primary"
            unelevated
            no-caps
            class="full-width"
            icon="add"
            label="Neue Session"
            disable
          >
            <q-tooltip>Session-Verwaltung folgt im naechsten Slice</q-tooltip>
          </q-btn>
        </div>

        <q-separator />

        <q-scroll-area class="col">
          <q-list padding>
            <q-item-label header>Sessions</q-item-label>
            <q-item class="app-empty-session" dense>
              <q-item-section avatar>
                <q-icon name="forum" />
              </q-item-section>
              <q-item-section>
                <q-item-label>Noch keine Sessions</q-item-label>
                <q-item-label caption>Deine Chats erscheinen spaeter hier.</q-item-label>
              </q-item-section>
            </q-item>
          </q-list>
        </q-scroll-area>

        <q-separator />

        <q-list padding>
          <q-item clickable v-ripple to="/settings" active-class="app-nav-item--active">
            <q-item-section avatar>
              <q-icon name="tune" />
            </q-item-section>
            <q-item-section>Einstellungen</q-item-section>
          </q-item>
        </q-list>
      </div>
    </q-drawer>

    <q-page-container>
      <router-view />
    </q-page-container>
  </q-layout>
</template>

<script setup>
import { ref } from 'vue'

defineOptions({ name: 'MainLayout' })

const drawerOpen = ref(true)
</script>
