import { describe, expect, it } from 'vitest'

import routes from '../src/router/routes'

describe('MatoLoop frontend scaffold', () => {
  it('exposes chat and the complete settings navigation', () => {
    const appRoute = routes.find((route) => Array.isArray(route.children))
    const routeNames = appRoute.children.map((route) => route.name)
    const settingsRoute = appRoute.children.find((route) => route.name === 'settings')

    expect(routeNames).toEqual(['chat', 'settings'])
    expect(settingsRoute.children.map((route) => route.name)).toEqual([
      'settings-providers',
      'settings-default-models',
      'settings-persona',
      'settings-integrations',
      'settings-tools',
      'settings-skills',
      'settings-memory'
    ])
    expect(
      settingsRoute.children.find((route) => route.name === 'settings-integrations').children
        .map((route) => route.name)
    ).toEqual(['settings-integration-access'])
  })
})
