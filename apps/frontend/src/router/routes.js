import { adminRoutes, settingsRoutes } from 'src/features/settings/routes'

export { adminRoutes, settingsRoutes } from 'src/features/settings/routes'

const routes = [
  { path: '/login', name: 'login', component: () => import('src/features/auth/AuthPage.vue'), meta: { public: true } },
  {
    path: '/print/markdown',
    name: 'markdown-print',
    component: () => import('src/features/markdown-print/MarkdownPrintPage.vue')
  },
  {
    path: '/',
    component: () => import('src/app/MainLayout.vue'),
    children: [
      { path: '', name: 'chat', component: () => import('src/features/chat/ChatPage.vue') },
      {
        path: 'settings',
        name: 'settings',
        component: () => import('src/features/settings/SettingsPage.vue'),
        redirect: { name: 'settings-providers' },
        children: settingsRoutes
      },
      {
        path: 'admin',
        name: 'admin',
        component: () => import('src/features/settings/SettingsPage.vue'),
        props: { admin: true },
        meta: { requiresAdmin: true },
        redirect: { name: 'admin-users' },
        children: adminRoutes
      }
    ]
  },
  {
    path: '/:catchAll(.*)*',
    component: () => import('src/app/NotFoundPage.vue')
  }
]

export default routes
