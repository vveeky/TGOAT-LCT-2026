const WIDGET_MODE_LIMITS = {
  compactMinWidth: 80,
  compactMinHeight: 126,
  fullLandscapeWidth: 520,
  fullLandscapeHeight: 300,
  fullPortraitWidth: 240,
  fullPortraitHeight: 520,
  tallRatio: 1.25,
  wideRatio: 1.6,
  compactWidth: 160,
  compactHeight: 200,
}

export function getWidgetLayoutMode(width, height) {
  const limits = WIDGET_MODE_LIMITS
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return 'compact'
  if (width < limits.compactMinWidth || height < limits.compactMinHeight) return 'compact'
  if (
    (width >= limits.fullLandscapeWidth && height >= limits.fullLandscapeHeight)
    || (width >= limits.fullPortraitWidth && height >= limits.fullPortraitHeight)
  ) return 'full'
  if (height < limits.compactHeight) return 'compact'
  if (height > width * limits.tallRatio) return 'tall'
  if (width > height * limits.wideRatio) return 'wide'
  if (width < limits.compactWidth) return 'compact'
  return 'square'
}
