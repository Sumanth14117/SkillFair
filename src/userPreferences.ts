const PREFIX = 'skillfair.preferences.v1.'
const DISPLAY_NAME_LIMIT = 50

export type UserPreferences = { displayName: string }

function key(ownerId: string): string {
  return `${PREFIX}${encodeURIComponent(ownerId)}`
}

export function readUserPreferences(ownerId: string): UserPreferences {
  if (!ownerId) return { displayName: '' }
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(key(ownerId)) ?? 'null')
    if (!parsed || typeof parsed !== 'object') return { displayName: '' }
    const name = (parsed as Record<string, unknown>).displayName
    return { displayName: typeof name === 'string' ? name.trim().slice(0, DISPLAY_NAME_LIMIT) : '' }
  } catch { return { displayName: '' } }
}

export function saveDisplayNamePreference(ownerId: string, value: string): UserPreferences {
  const displayName = value.trim().slice(0, DISPLAY_NAME_LIMIT)
  if (!ownerId) return { displayName: '' }
  try {
    const current = readUserPreferences(ownerId)
    window.localStorage.setItem(key(ownerId), JSON.stringify({ ...current, displayName }))
  } catch { /* Preferences are optional and browser-local. */ }
  return { displayName }
}
