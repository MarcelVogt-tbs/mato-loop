import { describe, expect, it } from 'vitest'

import routes, { settingsRoutes } from '../src/router/routes'

describe('MatoLoop frontend scaffold', () => {
  it('exposes chat and the complete settings navigation', () => {
    const appRoute = routes.find((route) => Array.isArray(route.children))
    const routeNames = appRoute.children.map((route) => route.name)
    const settingsRoute = appRoute.children.find((route) => route.name === 'settings')

    expect(routeNames).toEqual(['chat', 'settings'])
    expect(settingsRoute.children.map((route) => route.name)).toEqual([
      'settings-providers',
      'settings-default-models',
      'settings-integrations',
      'settings-tools',
      'settings-approvals',
       'settings-execution-agents',
       'settings-skills',
       'settings-automations',
      'settings-persona',
      'settings-memory',
      'settings-users',
      'settings-instance-limits'
    ])
    expect(
      settingsRoute.children.find((route) => route.name === 'settings-integrations').children
        .map((route) => route.name)
    ).toEqual(['settings-integration-access'])
    expect(routes.find((route) => route.name === 'markdown-print')?.path).toBe('/print/markdown')
    expect(settingsRoutes.find((route) => route.name === 'settings-users')?.meta.requiresAdmin).toBe(true)
    expect(settingsRoutes.find((route) => route.name === 'settings-instance-limits')?.meta.requiresAdmin).toBe(true)
    expect(settingsRoutes.some((route) => route.name === 'settings-account')).toBe(false)
    expect(settingsRoutes.find((route) => route.name === 'settings-approvals')?.redirect).toEqual({
      name: 'chat', query: { controlCenter: 'approvals' }
    })
    expect(settingsRoutes
      .filter((route) => ['settings-providers', 'settings-default-models', 'settings-integrations', 'settings-tools'].includes(route.name))
      .every((route) => route.meta.requiresAdmin === undefined)).toBe(true)
  })
})
