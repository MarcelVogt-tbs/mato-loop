const routes = [
  { path: '/login', name: 'login', component: () => import('pages/AuthPage.vue'), meta: { public: true } },
  {
    path: '/',
    component: () => import('layouts/MainLayout.vue'),
    children: [
      { path: '', name: 'chat', component: () => import('pages/ChatPage.vue') },
      {
        path: 'settings',
        name: 'settings',
        component: () => import('pages/SettingsPage.vue'),
        redirect: { name: 'settings-providers' },
        children: [
          {
            path: 'providers',
            name: 'settings-providers',
            component: () => import('pages/settings/ProviderSettingsPage.vue')
          },
          {
            path: 'default-models',
            name: 'settings-default-models',
            component: () => import('pages/settings/DefaultModelSettingsPage.vue')
          },
          {
            path: 'persona',
            name: 'settings-persona',
            component: () => import('pages/settings/AgentIdentitySettingsPage.vue')
          },
          {
            path: 'integrations',
            name: 'settings-integrations',
            component: () => import('pages/settings/IntegrationSettingsPage.vue'),
            children: [
              {
                path: ':integrationId/access',
                name: 'settings-integration-access',
                component: () => import('pages/settings/IntegrationAccessPage.vue')
              }
            ]
          },
          {
            path: 'tools',
            name: 'settings-tools',
            component: () => import('pages/settings/ToolSettingsPage.vue')
          },
          {
            path: 'memory',
            name: 'settings-memory',
            component: () => import('pages/settings/MemorySettingsPage.vue')
          }
        ]
      }
    ]
  },
  {
    path: '/:catchAll(.*)*',
    component: () => import('pages/NotFoundPage.vue')
  }
]

export default routes
