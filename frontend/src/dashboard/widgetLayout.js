export const widgetIds = ['status', 'attention', 'predictions', 'activeRequests']
export const layoutPresets = {
  rows: 'Две строки',
  columns: 'Две колонки',
  'three-rows': 'Три строки',
  'feature-top': 'Широкая сверху',
  'feature-bottom': 'Широкая снизу',
  single: 'Одна колонка',
}
export function normalizeWidgetLayout(value) {
  const order = [...new Set((Array.isArray(value?.order) ? value.order : []).filter(id => widgetIds.includes(id)))]
  return {
    preset: Object.hasOwn(layoutPresets, value?.preset) ? value.preset : 'rows',
    order: [...order, ...widgetIds.filter(id => !order.includes(id))],
  }
}
export function moveWidget(layout, source, target) {
  if (source === target) return layout
  if (!widgetIds.includes(source) || !widgetIds.includes(target)) return layout
  const order = layout.order.filter(id => id !== source)
  order.splice(order.indexOf(target), 0, source)
  return { ...layout, order }
}
