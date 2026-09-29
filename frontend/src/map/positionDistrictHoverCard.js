const HOVER_CARD_WIDTH = 280
const HOVER_CARD_HEIGHT = 120
const HOVER_CARD_GAP = 12

export function positionDistrictHoverCard(point, containerSize) {
  const width = Math.max(0, containerSize.width ?? 0)
  const cardWidth = Math.min(HOVER_CARD_WIDTH, Math.max(0, width - 24))

  return {
    x: point.x,
    y: point.y,
    horizontal: point.x + cardWidth + HOVER_CARD_GAP * 2 > width ? 'left' : 'right',
    vertical: point.y < HOVER_CARD_HEIGHT + HOVER_CARD_GAP ? 'below' : 'above',
  }
}
