import { settingsRoutes } from 'src/features/settings/routes'

export { settingsRoutes } from 'src/features/settings/routes'

const routes = [
  { path: '/login', name: 'login', component: () => import('pages/AuthPage.vue'), meta: { public: true } },
  {
    path: '/print/markdown',
    name: 'markdown-print',
    component: () => import('pages/MarkdownPrintPage.vue')
  },
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
        children: settingsRoutes
      }
    ]
  },
  {
    path: '/:catchAll(.*)*',
    component: () => import('pages/NotFoundPage.vue')
  }
]

export default routes
