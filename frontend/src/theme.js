export const APPEARANCE_THEME_STORAGE_KEY = 'tgoat.appearance-theme.v1'
export const CLASSIC_THEME = 'classic'
export const GRAPHITE_THEME = 'graphite'

function getStorage(storage) {
  if (storage) return storage
  if (typeof window === 'undefined') return null
  return window.localStorage
}

export function readAppearanceTheme(storage) {
  try {
    return getStorage(storage)?.getItem(APPEARANCE_THEME_STORAGE_KEY) === GRAPHITE_THEME
      ? GRAPHITE_THEME
      : CLASSIC_THEME
  } catch {
    return CLASSIC_THEME
  }
}

export function persistAppearanceTheme(theme, storage) {
  try {
    getStorage(storage)?.setItem(
      APPEARANCE_THEME_STORAGE_KEY,
      theme === GRAPHITE_THEME ? GRAPHITE_THEME : CLASSIC_THEME,
    )
  } catch {
    // Keep the in-memory selection even when browser storage is unavailable.
  }
}

export function nextAppearanceTheme(theme) {
  return theme === GRAPHITE_THEME ? CLASSIC_THEME : GRAPHITE_THEME
}
