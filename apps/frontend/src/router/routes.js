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
      { path: '', alias: '/chat', name: 'chat', component: () => import('src/features/chat/ChatPage.vue') },
      { path: 'dashboard', redirect: (to) => ({ name: 'chat', query: to.query, hash: to.hash }) },
      { path: 'personal-variables', name: 'personal-variables', component: () => import('src/features/personal-variables/PersonalVariablesPage.vue') },
      { path: 'archive', name: 'archive', component: () => import('src/features/archive/ArchivePage.vue') },
      {
        path: 'settings',
        name: 'settings',
        component: () => import('src/features/settings/SettingsPage.vue'),
        redirect: { name: 'settings-personalization' },
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
