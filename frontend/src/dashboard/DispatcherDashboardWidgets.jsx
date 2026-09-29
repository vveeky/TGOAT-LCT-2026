import { cloneElement, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { createPortal, flushSync } from 'react-dom'
import { createLayout } from 'animejs'
import { Link } from 'react-router-dom'

import { getDashboard } from '../api/dashboard'
import { getMaintenanceRequests } from '../api/maintenance'
import { formatMoscowDateTime, formatMoscowTime } from '../dateTime'
import { riskText, signalLabels } from '../api/signals'
import { useReplayRuntime } from '../hooks/useReplayRuntime'
import RiskDistributionWidget from '../components/ui/risk-distribution-widget'
import { getWidgetLayoutMode } from './widgetSizing'
import { widgetIds } from './widgetLayout'

const RISK_ORDER = ['critical', 'high', 'medium', 'low', 'no-data']
const WIDGET_ORDER_KEY = 'tgoat.dashboard.dispatcher-widget-layout.v1'
const LEGACY_WIDGET_ORDER_KEY = 'tgoat.dashboard.functional-layout.v1'
const WIDGET_IDS = [...widgetIds]
const FIXED_WIDGET_ID = 'status'
const ADJUSTABLE_WIDGET_IDS = WIDGET_IDS.filter(widgetId => widgetId !== FIXED_WIDGET_ID)
const WIDGET_TITLES = {
  status: 'Распределение рисков',
  attention: 'Требуют внимания',
  predictions: 'Последние прогнозы',
  activeRequests: 'Активные заявки',
}
const WIDGET_DESCRIPTIONS = {
  status: 'Количество объектов по уровням риска',
  attention: 'Список объектов из текущей сводки',
  predictions: 'Недавние прогнозы из API',
  activeRequests: 'Последние заявки из системы с фильтром по статусу',
}
const DASHBOARD_LAYOUT_PRESETS = [
  {
    id: 'feature-top',
    title: 'Широкая сверху',
    capacity: 3,
    areas: '"a a" "b c"',
    columns: 'repeat(2, minmax(0, 1fr))',
    rows: 'minmax(200px, 0.7fr) minmax(140px, 1.3fr)',
  },
  {
    id: 'feature-bottom',
    title: 'Широкая снизу',
    capacity: 3,
    areas: '"a b" "c c"',
    columns: 'repeat(2, minmax(0, 1fr))',
    rows: 'minmax(140px, 1.3fr) minmax(200px, 0.7fr)',
  },
  {
    id: 'three-rows',
    title: 'Три строки',
    capacity: 3,
    areas: '"a" "b" "c"',
    columns: 'minmax(0, 1fr)',
    rows: 'repeat(3, minmax(80px, 1fr))',
  },
  {
    id: 'rows',
    title: 'Две строки',
    capacity: 2,
    areas: '"a" "b"',
    columns: 'minmax(0, 1fr)',
    rows: 'minmax(140px, 1fr) minmax(140px, 1fr)',
  },
  {
    id: 'single',
    title: 'Одна ячейка на всю панель',
    capacity: 1,
    areas: '"a"',
    columns: 'minmax(0, 1fr)',
    rows: 'minmax(560px, 1fr)',
  },
  {
    id: 'columns',
    title: 'Две вертикальные ячейки',
    capacity: 2,
    areas: '"a b"',
    columns: 'repeat(2, minmax(0, 1fr))',
    rows: 'minmax(340px, 1fr)',
  },
]
const DASHBOARD_LAYOUT_ROWS = {
  'feature-top': 'minmax(200px, 0.7fr) minmax(140px, 1.3fr)',
  'feature-bottom': 'minmax(140px, 1.3fr) minmax(200px, 0.7fr)',
  'three-rows': 'repeat(3, minmax(80px, 1fr))',
  rows: 'clamp(220px, 28vh, 260px) minmax(0, 1fr)',
  single: 'minmax(560px, 1fr)',
  columns: 'minmax(340px, 1fr)',
}
const LAYOUT_PICKER_HOVER_DELAY_MS = 2000
const LAYOUT_PICKER_CLOSE_DELAY_MS = 4000
const DEFAULT_WIDGET_ORDERS = {
  'feature-top': ['attention', 'predictions', 'activeRequests'],
  'feature-bottom': ['predictions', 'activeRequests', 'attention'],
  'three-rows': ['attention', 'activeRequests', 'predictions'],
  rows: ['attention', 'predictions', 'activeRequests'],
  single: ['attention', 'predictions', 'activeRequests'],
  columns: ['predictions', 'attention', 'activeRequests'],
}
const DEFAULT_WIDGET_LAYOUT = {
  presetId: 'rows',
  ordersByPreset: Object.fromEntries(
    Object.entries(DEFAULT_WIDGET_ORDERS).map(([presetId, order]) => [presetId, [...order]]),
  ),
}
const RISK_LABELS = {
  critical: 'Критический',
  high: 'Высокий',
  medium: 'Средний',
  low: 'Низкий',
  'no-data': 'Нет оценки',
}
const ACTIVE_REQUEST_STATUSES = ['draft', 'new', 'assigned', 'in_progress', 'awaiting_review']
const REQUEST_STATUS_LABELS = {
  draft: 'Черновик',
  new: 'Новая',
  assigned: 'Назначена',
  in_progress: 'В работе',
  awaiting_review: 'Ожидает приёмки',
}
const RISK_SHORT_LABELS = {
  critical: 'Критический',
  high: 'Высокий',
  medium: 'Средний',
  low: 'Низкий',
  normal: 'Низкий',
}

function predictionTone(level) {
  return ['critical', 'high', 'medium', 'low'].includes(level) ? level : 'no-data'
}

function formatRequestDate(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (!Number.isFinite(date.getTime())) return '—'
  return new Intl.DateTimeFormat('ru-RU', {
    day: 'numeric',
    month: 'short',
    timeZone: 'Europe/Moscow',
  }).format(date)
}

function normalizeWidgetOrder(order, presetId = 'rows') {
  const selected = Array.isArray(order) ? order : []
  const unique = [...new Set(selected.filter(id => ADJUSTABLE_WIDGET_IDS.includes(id)))]
  const presetDefaults = DEFAULT_WIDGET_ORDERS[presetId] ?? DEFAULT_WIDGET_ORDERS.rows
  return [
    ...unique,
    ...presetDefaults.filter(id => !unique.includes(id)),
    ...ADJUSTABLE_WIDGET_IDS.filter(id => !unique.includes(id) && !presetDefaults.includes(id)),
  ]
}

function readWidgetLayout() {
  try {
    const saved = localStorage.getItem(WIDGET_ORDER_KEY)
      ?? localStorage.getItem(LEGACY_WIDGET_ORDER_KEY)
    if (!saved) return DEFAULT_WIDGET_LAYOUT
    const layout = JSON.parse(saved)
    const presetId = DASHBOARD_LAYOUT_PRESETS.some(preset => preset.id === layout?.presetId)
      ? layout.presetId
      : 'rows'
    const legacyOrder = normalizeWidgetOrder(
      Array.isArray(layout) ? layout : layout?.order,
      presetId,
    )
    const savedOrders = layout?.ordersByPreset ?? {}
    return {
      presetId,
      ordersByPreset: Object.fromEntries(DASHBOARD_LAYOUT_PRESETS.map(preset => [
        preset.id,
        normalizeWidgetOrder(
          savedOrders[preset.id] ?? (preset.id === presetId ? legacyOrder : DEFAULT_WIDGET_ORDERS[preset.id]),
          preset.id,
        ),
      ])),
    }
  } catch {
    return DEFAULT_WIDGET_LAYOUT
  }
}

function placeWidgetAt(order, widgetId, position) {
  const source = order.indexOf(widgetId)
  if (source < 0 || source === position) return order
  const next = [...order]
  next.splice(source, 1)
  next.splice(position, 0, widgetId)
  return next
}

function WidgetGrip({
  active,
  buttonRef,
  controls,
  label,
  widgetId,
  onClick,
  onDragEnd,
  onDragStart,
  onKeyDown,
  onPointerEnter,
  onPointerLeave,
}) {
  return (
    <button
      aria-controls={controls}
      aria-expanded={active}
      aria-haspopup="dialog"
      aria-label={`Выбрать или переместить виджет «${label}»`}
      className="dashboard-widget-drag-handle"
      data-layout-picker-trigger={widgetId}
      data-widget-picker-trigger={widgetId}
      draggable="true"
      ref={buttonRef}
      onClick={onClick}
      onDragEnd={onDragEnd}
      onDragStart={onDragStart}
      onKeyDown={onKeyDown}
      onPointerEnter={onPointerEnter}
      onPointerLeave={onPointerLeave}
      title="Наведите, чтобы выбрать позицию · Нажмите, чтобы выбрать виджет"
      type="button"
    >
      <svg aria-hidden="true" fill="none" viewBox="0 0 16 16">
        <circle cx="5" cy="4" r="1" />
        <circle cx="11" cy="4" r="1" />
        <circle cx="5" cy="8" r="1" />
        <circle cx="11" cy="8" r="1" />
        <circle cx="5" cy="12" r="1" />
        <circle cx="11" cy="12" r="1" />
      </svg>
    </button>
  )
}

export default function DispatcherDashboardWidgets({
  attentionSignals = [],
  attentionStatus = 'loading',
  onKpiData,
  onRetryAttention,
}) {
  const [dashboard, setDashboard] = useState(null)
  const [dashboardError, setDashboardError] = useState(null)
  const [dashboardRetry, setDashboardRetry] = useState(0)
  const [requestCount, setRequestCount] = useState(null)
  const [activeRequests, setActiveRequests] = useState([])
  const [requestError, setRequestError] = useState(null)
  const [requestRetry, setRequestRetry] = useState(0)
  const [widgetLayout, setWidgetLayout] = useState(readWidgetLayout)
  const [widgetLayoutModes, setWidgetLayoutModes] = useState({})
  const widgetOrder = widgetLayout.ordersByPreset[widgetLayout.presetId]
    ?? DEFAULT_WIDGET_ORDERS[widgetLayout.presetId]
  const layoutPreset = DASHBOARD_LAYOUT_PRESETS.find(preset => preset.id === widgetLayout.presetId)
    ?? DASHBOARD_LAYOUT_PRESETS.find(preset => preset.id === 'rows')
  const [layoutPickerOpen, setLayoutPickerOpen] = useState(false)
  const [openWidgetPickerFor, setOpenWidgetPickerFor] = useState(null)
  const [layoutPickerPosition, setLayoutPickerPosition] = useState(null)
  const [widgetPickerPosition, setWidgetPickerPosition] = useState(null)
  const [layoutPlacementTarget, setLayoutPlacementTarget] = useState(null)
  const [selectedWidgetForMove, setSelectedWidgetForMove] = useState(null)
  const [draggedWidget, setDraggedWidget] = useState(null)
  const [dropTarget, setDropTarget] = useState(null)
  const [layoutAnnouncement, setLayoutAnnouncement] = useState('')
  const widgetGridRef = useRef(null)
  const widgetPanelRef = useRef(null)
  const layoutPickerRef = useRef(null)
  const widgetPickerRef = useRef(null)
  const widgetGripRefs = useRef(new Map())
  const layoutPickerTimerRef = useRef(null)
  const layoutPickerCloseTimerRef = useRef(null)
  const layoutPickerHoverRef = useRef(false)
  const layoutPickerTriggerHoverRef = useRef(false)
  const widgetLayoutAnimationRef = useRef(null)
  const { status } = useReplayRuntime()

  useEffect(() => {
    let active = true
    const refresh = () => getDashboard()
      .then(value => {
        if (!active) return
        setDashboard(value)
        setDashboardError(null)
      })
      .catch(error => { if (active) setDashboardError(error) })
    refresh()
    const timer = window.setInterval(refresh, status?.mode === 'replay' ? 1500 : 30_000)
    return () => { active = false; window.clearInterval(timer) }
  }, [dashboardRetry, status?.mode])

  useEffect(() => {
    let active = true
    const refresh = () => Promise.all(ACTIVE_REQUEST_STATUSES.map(status =>
      getMaintenanceRequests({ status, limit: 3 }),
    )).then(results => {
        if (!active) return
        setRequestCount(results.reduce((sum, result) => sum + result.total, 0))
        setActiveRequests(results.flatMap(result => result.items ?? [])
          .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
          .slice(0, 3))
        setRequestError(null)
      })
      .catch(error => {
        if (!active) return
        setRequestCount(null)
        setActiveRequests([])
        setRequestError(error)
      })
    refresh()
    const timer = window.setInterval(refresh, 30_000)
    return () => { active = false; window.clearInterval(timer) }
  }, [requestRetry])

  useEffect(() => {
    onKpiData?.({
      objectCount: dashboard?.objects_count ?? null,
      activeRequestsCount: requestCount,
      snapshotKey: dashboard?.snapshot_ts,
    })
  }, [dashboard?.objects_count, dashboard?.snapshot_ts, requestCount, onKpiData])

  useEffect(() => {
    try {
      window.localStorage.setItem(WIDGET_ORDER_KEY, JSON.stringify({
        order: widgetOrder,
        presetId: layoutPreset.id,
        ordersByPreset: widgetLayout.ordersByPreset,
      }))
    } catch {
      // The arrangement remains available until the page is closed.
    }
  }, [layoutPreset.id, widgetOrder, widgetLayout.ordersByPreset])

  useEffect(() => {
    const root = widgetGridRef.current
    if (!root) return undefined
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const layout = createLayout(root, {
      children: '.dashboard-widget-slot',
      duration: reduceMotion ? 0 : 340,
      ease: 'out(3)',
      enterFrom: {
        transform: 'translateY(10px) scale(.985)',
        opacity: 0,
        duration: reduceMotion ? 0 : 220,
        ease: 'out(3)',
      },
    })
    widgetLayoutAnimationRef.current = layout
    return () => {
      layout.revert()
      widgetLayoutAnimationRef.current = null
    }
  }, [])

  useEffect(() => {
    const root = widgetPanelRef.current
    if (!root || typeof ResizeObserver === 'undefined') return undefined
    const slots = [...root.querySelectorAll('[data-widget-slot-id][data-widget-id]')]
    const activeSlotIds = new Set(slots.map(slot => slot.dataset.widgetSlotId))
    const observer = new ResizeObserver(entries => {
      setWidgetLayoutModes(previousModes => {
        const nextModes = Object.fromEntries(
          Object.entries(previousModes).filter(([slotId]) => activeSlotIds.has(slotId)),
        )
        let changed = Object.keys(previousModes).length !== Object.keys(nextModes).length
        entries.forEach(entry => {
          const { widgetSlotId, widgetId } = entry.target.dataset
          if (!widgetSlotId || !widgetId) return
          const mode = getWidgetLayoutMode(entry.contentRect.width, entry.contentRect.height)
          if (nextModes[widgetSlotId] !== mode) {
            nextModes[widgetSlotId] = mode
            changed = true
          }
        })
        return changed ? nextModes : previousModes
      })
    })
    slots.forEach(slot => observer.observe(slot))
    return () => observer.disconnect()
  }, [widgetLayout])

  const closeLayoutPicker = useCallback(() => {
    if (layoutPickerTimerRef.current) window.clearTimeout(layoutPickerTimerRef.current)
    if (layoutPickerCloseTimerRef.current) window.clearTimeout(layoutPickerCloseTimerRef.current)
    layoutPickerTimerRef.current = null
    layoutPickerCloseTimerRef.current = null
    layoutPickerHoverRef.current = false
    layoutPickerTriggerHoverRef.current = false
    setLayoutPickerOpen(false)
    setLayoutPlacementTarget(null)
    setSelectedWidgetForMove(null)
  }, [])

  const closeWidgetPicker = useCallback(() => setOpenWidgetPickerFor(null), [])

  useEffect(() => () => {
    if (layoutPickerTimerRef.current) window.clearTimeout(layoutPickerTimerRef.current)
    if (layoutPickerCloseTimerRef.current) window.clearTimeout(layoutPickerCloseTimerRef.current)
  }, [])

  useEffect(() => {
    if (!openWidgetPickerFor) return undefined
    const closeOnOutsidePointer = event => {
      if (widgetPickerRef.current?.contains(event.target)) return
      if (event.target.closest?.(`[data-widget-picker-trigger="${openWidgetPickerFor}"]`)) return
      closeWidgetPicker()
    }
    const closeOnEscape = event => {
      if (event.key === 'Escape') closeWidgetPicker()
    }
    document.addEventListener('pointerdown', closeOnOutsidePointer, true)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsidePointer, true)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [closeWidgetPicker, openWidgetPickerFor])

  useEffect(() => {
    if (!layoutPickerOpen) return undefined
    const closeOnOutsidePointer = event => {
      if (layoutPickerRef.current?.contains(event.target)) return
      if (event.target.closest?.('[data-layout-picker-trigger]')) return
      closeLayoutPicker()
    }
    const closeOnEscape = event => {
      if (event.key === 'Escape') closeLayoutPicker()
    }
    document.addEventListener('pointerdown', closeOnOutsidePointer, true)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsidePointer, true)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [closeLayoutPicker, layoutPickerOpen])

  useLayoutEffect(() => {
    if (!layoutPickerOpen) return undefined
    const updatePosition = () => {
      const bounds = widgetGridRef.current?.getBoundingClientRect()
      if (!bounds) return
      const viewportPadding = 16
      const width = Math.min(680, window.innerWidth - viewportPadding * 2, Math.max(300, bounds.width))
      const centerX = Math.min(
        window.innerWidth - width / 2 - viewportPadding,
        Math.max(width / 2 + viewportPadding, bounds.left + bounds.width / 2),
      )
      const availableAbove = Math.max(0, Math.round(bounds.top - 28))
      const fitsAboveWidgetList = availableAbove >= 96
      const nextPosition = {
        left: Math.round(centerX) + 'px',
        top: fitsAboveWidgetList ? Math.round(bounds.top - 12) + 'px' : viewportPadding + 'px',
        width: Math.round(width) + 'px',
        maxHeight: fitsAboveWidgetList
          ? Math.min(window.innerHeight - viewportPadding * 2, availableAbove) + 'px'
          : 'calc(100dvh - ' + viewportPadding * 2 + 'px)',
        transform: fitsAboveWidgetList ? 'translate(-50%, -100%)' : 'translateX(-50%)',
      }
      setLayoutPickerPosition(current => (
        current && Object.keys(nextPosition).every(key => current[key] === nextPosition[key])
          ? current
          : nextPosition
      ))
    }
    updatePosition()
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    return () => {
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [layoutPickerOpen])

  useLayoutEffect(() => {
    if (!openWidgetPickerFor) return undefined

    const updatePosition = () => {
      const trigger = widgetGripRefs.current.get(openWidgetPickerFor)
      if (!trigger) return
      const bounds = trigger.getBoundingClientRect()
      const viewportPadding = 16
      const width = Math.min(310, window.innerWidth - viewportPadding * 2)
      const left = Math.min(
        window.innerWidth - width - viewportPadding,
        Math.max(viewportPadding, bounds.right - width),
      )
      const spaceAbove = Math.max(0, bounds.top - viewportPadding - 8)
      const spaceBelow = Math.max(0, window.innerHeight - bounds.bottom - viewportPadding - 8)
      const openBelow = spaceBelow >= Math.min(230, spaceAbove) || spaceBelow >= spaceAbove
      const availableSpace = openBelow ? spaceBelow : spaceAbove
      const nextPosition = {
        position: 'fixed',
        left: Math.round(left) + 'px',
        top: openBelow ? Math.round(bounds.bottom + 8) + 'px' : Math.round(bounds.top - 8) + 'px',
        width: Math.round(width) + 'px',
        maxHeight: Math.max(120, Math.min(340, Math.round(availableSpace))) + 'px',
        transform: openBelow ? 'none' : 'translateY(-100%)',
      }
      setWidgetPickerPosition(current => (
        current?.widgetId === openWidgetPickerFor
        && Object.keys(nextPosition).every(key => current.style[key] === nextPosition[key])
          ? current
          : { widgetId: openWidgetPickerFor, style: nextPosition }
      ))
    }

    updatePosition()
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    return () => {
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [openWidgetPickerFor])

  const riskSummary = useMemo(() => {
    const distribution = dashboard?.risk_distribution ?? {}
    return RISK_ORDER.map(key => ({
      key,
      label: RISK_LABELS[key],
      value: key === 'no-data'
        ? (dashboard?.no_prediction_count ?? 0) + (dashboard?.degraded_count ?? 0)
        : distribution[key] ?? 0,
    }))
  }, [dashboard])
  const objectNames = useMemo(() => new Map(
    (dashboard?.objects ?? []).map(object => [object.id, object.name]),
  ), [dashboard])

  const commitLayout = useCallback((nextOrder, nextPresetId, message) => {
    const targetOrder = widgetLayout.ordersByPreset[nextPresetId] ?? DEFAULT_WIDGET_ORDERS[nextPresetId]
    const normalizedNextOrder = normalizeWidgetOrder(nextOrder, nextPresetId)
    const orderChanged = normalizedNextOrder.some((id, index) => id !== targetOrder[index])
    const presetChanged = nextPresetId !== widgetLayout.presetId
    if (!orderChanged && !presetChanged) {
      closeLayoutPicker()
      return
    }
    const applyLayout = () => flushSync(() => setWidgetLayout(current => ({
      presetId: nextPresetId,
      ordersByPreset: {
        ...current.ordersByPreset,
        [nextPresetId]: normalizedNextOrder,
      },
    })))
    if (widgetLayoutAnimationRef.current) {
      widgetLayoutAnimationRef.current.update(applyLayout)
    } else {
      applyLayout()
    }
    setDropTarget(null)
    closeLayoutPicker()
    setLayoutAnnouncement(message)
  }, [closeLayoutPicker, widgetLayout])

  const openLayoutPicker = widgetId => {
    if (layoutPickerTimerRef.current) window.clearTimeout(layoutPickerTimerRef.current)
    if (layoutPickerCloseTimerRef.current) window.clearTimeout(layoutPickerCloseTimerRef.current)
    layoutPickerTimerRef.current = null
    layoutPickerCloseTimerRef.current = null
    closeWidgetPicker()
    setSelectedWidgetForMove(widgetId)
    setLayoutPlacementTarget(null)
    setLayoutPickerOpen(true)
    setLayoutAnnouncement("Выберите ячейку, куда переместить виджет.")
  }

  const scheduleLayoutPickerClose = () => {
    if (layoutPickerCloseTimerRef.current) window.clearTimeout(layoutPickerCloseTimerRef.current)
    layoutPickerCloseTimerRef.current = window.setTimeout(() => {
      layoutPickerCloseTimerRef.current = null
      if (layoutPickerHoverRef.current || layoutPickerTriggerHoverRef.current) return
      closeLayoutPicker()
    }, LAYOUT_PICKER_CLOSE_DELAY_MS)
  }

  const scheduleLayoutPicker = widgetId => {
    layoutPickerTriggerHoverRef.current = true
    if (layoutPickerCloseTimerRef.current) window.clearTimeout(layoutPickerCloseTimerRef.current)
    layoutPickerCloseTimerRef.current = null
    if (layoutPickerOpen && selectedWidgetForMove === widgetId) return
    if (layoutPickerTimerRef.current) window.clearTimeout(layoutPickerTimerRef.current)
    layoutPickerTimerRef.current = window.setTimeout(() => {
      layoutPickerTimerRef.current = null
      if (!layoutPickerTriggerHoverRef.current) return
      setOpenWidgetPickerFor(null)
      setSelectedWidgetForMove(widgetId)
      setLayoutPlacementTarget(null)
      setLayoutPickerOpen(true)
      setLayoutAnnouncement("Выберите ячейку, куда переместить виджет.")
      scheduleLayoutPickerClose()
    }, LAYOUT_PICKER_HOVER_DELAY_MS)
  }

  const handleLayoutPickerTriggerLeave = () => {
    layoutPickerTriggerHoverRef.current = false
    if (layoutPickerTimerRef.current) window.clearTimeout(layoutPickerTimerRef.current)
    layoutPickerTimerRef.current = null
    if (layoutPickerOpen) scheduleLayoutPickerClose()
  }

  const handleLayoutPickerEnter = () => {
    layoutPickerHoverRef.current = true
    if (layoutPickerCloseTimerRef.current) window.clearTimeout(layoutPickerCloseTimerRef.current)
    layoutPickerCloseTimerRef.current = null
  }

  const handleLayoutPickerLeave = event => {
    if (event.currentTarget.contains(event.relatedTarget)) return
    layoutPickerHoverRef.current = false
    scheduleLayoutPickerClose()
  }

  const moveWidget = (widgetId, position, presetId = layoutPreset.id) => {
    const sourceOrder = widgetLayout.ordersByPreset[presetId] ?? DEFAULT_WIDGET_ORDERS[presetId]
    const nextOrder = placeWidgetAt(sourceOrder, widgetId, position)
    const presetTitle = DASHBOARD_LAYOUT_PRESETS.find(preset => preset.id === presetId)?.title ?? "раскладке"
    commitLayout(
      nextOrder,
      presetId,
      "Виджет «" + WIDGET_TITLES[widgetId] + "» перемещён. Раскладка: " + presetTitle + ".",
    )
  }

  const chooseWidgetForSlot = (slotWidgetId, chosenWidgetId) => {
    const targetIndex = widgetOrder.indexOf(slotWidgetId)
    const sourceIndex = widgetOrder.indexOf(chosenWidgetId)
    if (targetIndex < 0 || sourceIndex < 0 || targetIndex === sourceIndex) {
      closeWidgetPicker()
      return
    }
    const nextOrder = [...widgetOrder]
    ;[nextOrder[targetIndex], nextOrder[sourceIndex]] = [nextOrder[sourceIndex], nextOrder[targetIndex]]
    commitLayout(
      nextOrder,
      layoutPreset.id,
      `Виджеты «${WIDGET_TITLES[slotWidgetId]}» и «${WIDGET_TITLES[chosenWidgetId]}» поменялись местами.`,
    )
    closeWidgetPicker()
  }

  const toggleWidgetPicker = widgetId => {
    closeLayoutPicker()
    setOpenWidgetPickerFor(current => current === widgetId ? null : widgetId)
  }

  const applyLayoutPickerChoice = (presetId, cellIndex, widgetId = selectedWidgetForMove) => {
    if (!widgetId) return
    moveWidget(widgetId, cellIndex, presetId)
  }

  const resetWidgetLayout = () => {
    const applyLayout = () => flushSync(() => setWidgetLayout(DEFAULT_WIDGET_LAYOUT))
    if (widgetLayoutAnimationRef.current) {
      widgetLayoutAnimationRef.current.update(applyLayout)
    } else {
      applyLayout()
    }
    setDropTarget(null)
    closeLayoutPicker()
    setLayoutAnnouncement("Исходные раскладки виджетов восстановлены.")
  }

  const handleWidgetDrop = useCallback((event, targetId) => {
    const widgetId = event.dataTransfer.getData("text/plain") || draggedWidget
    if (!widgetId) return
    event.preventDefault()
    const targetPosition = widgetOrder.indexOf(targetId)
    if (widgetId !== targetId && targetPosition >= 0) {
      const nextOrder = placeWidgetAt(widgetOrder, widgetId, targetPosition)
      commitLayout(nextOrder, layoutPreset.id, "Виджет «" + WIDGET_TITLES[widgetId] + "» перемещён.")
    }
    setDraggedWidget(null)
    setDropTarget(null)
  }, [commitLayout, draggedWidget, layoutPreset.id, widgetOrder])

  const widgetDropHandlers = useMemo(() => Object.fromEntries(
    ADJUSTABLE_WIDGET_IDS.map(widgetId => [widgetId, event => handleWidgetDrop(event, widgetId)]),
  ), [handleWidgetDrop])

  const handleLayoutCellDragOver = (event, presetId, cellIndex) => {
    if (!draggedWidget) return
    event.preventDefault()
    event.stopPropagation()
    event.dataTransfer.dropEffect = "move"
    const target = { presetId, cellIndex }
    setLayoutPlacementTarget(current => (
      current?.presetId === presetId && current?.cellIndex === cellIndex ? current : target
    ))
  }

  const handleLayoutCellDrop = (event, presetId, cellIndex) => {
    event.preventDefault()
    event.stopPropagation()
    const widgetId = event.dataTransfer.getData("text/plain") || draggedWidget
    setDraggedWidget(null)
    if (widgetId) applyLayoutPickerChoice(presetId, cellIndex, widgetId)
  }

  const widgetInteractions = widgetId => ({
    "data-widget-id": widgetId,
    "data-widget-slot-id": widgetId,
    className: "info-section dashboard-widget-slot is-" + widgetId
      + (draggedWidget === widgetId ? " is-drag-source" : "")
      + (dropTarget === widgetId ? " is-drop-target" : ""),
    onDragOver: event => {
      if (!draggedWidget || draggedWidget === widgetId) return
      event.preventDefault()
      event.dataTransfer.dropEffect = "move"
      setDropTarget(widgetId)
    },
    onDragLeave: event => {
      if (!event.currentTarget.contains(event.relatedTarget)) setDropTarget(null)
    },
    onDrop: widgetDropHandlers[widgetId],
  })

  const renderWidgetPicker = widgetId => openWidgetPickerFor === widgetId && createPortal(
    <div
      aria-label="Выбор виджета"
      className="dashboard-widget-picker"
      id={`dashboard-widget-picker-${widgetId}`}
      ref={widgetPickerRef}
      role="dialog"
      style={{
        ...(widgetPickerPosition?.widgetId === widgetId ? widgetPickerPosition.style : {}),
        visibility: widgetPickerPosition?.widgetId === widgetId ? 'visible' : 'hidden',
      }}
    >
      <div className="dashboard-widget-picker-heading">
        <div>
          <span>ИЗМЕНИТЬ ВИДЖЕТ</span>
          <strong>Выберите виджет</strong>
        </div>
        <button
          aria-label="Закрыть список виджетов"
          className="dashboard-widget-picker-close"
          onClick={closeWidgetPicker}
          type="button"
        >
          ×
        </button>
      </div>
      <div className="dashboard-widget-picker-options">
        {ADJUSTABLE_WIDGET_IDS.filter(optionId => optionId !== widgetId).map(optionId => (
          <button
            className="dashboard-widget-picker-option"
            key={optionId}
            onClick={() => chooseWidgetForSlot(widgetId, optionId)}
            type="button"
          >
            <span aria-hidden="true">＋</span>
            <span>
              <strong>{WIDGET_TITLES[optionId]}</strong>
              <small>{WIDGET_DESCRIPTIONS[optionId]}</small>
            </span>
            <b aria-hidden="true">›</b>
          </button>
        ))}
      </div>
    </div>,
    document.body,
  )

  const renderWidgetControl = widgetId => (
    <div className="dashboard-widget-control">
      <WidgetGrip
        active={(
          (layoutPickerOpen && selectedWidgetForMove === widgetId)
          || openWidgetPickerFor === widgetId
        )}
        controls={openWidgetPickerFor === widgetId
          ? `dashboard-widget-picker-${widgetId}`
          : "dashboard-layout-picker"}
        buttonRef={element => {
          if (element) widgetGripRefs.current.set(widgetId, element)
          else widgetGripRefs.current.delete(widgetId)
        }}
        label={WIDGET_TITLES[widgetId]}
        widgetId={widgetId}
        onClick={() => toggleWidgetPicker(widgetId)}
        onPointerEnter={() => scheduleLayoutPicker(widgetId)}
        onPointerLeave={handleLayoutPickerTriggerLeave}
        onDragEnd={() => {
          setDraggedWidget(null)
          setDropTarget(null)
          closeLayoutPicker()
          closeWidgetPicker()
        }}
        onDragStart={event => {
          event.dataTransfer.effectAllowed = "move"
          event.dataTransfer.setData("text/plain", widgetId)
          setDraggedWidget(widgetId)
          closeWidgetPicker()
          openLayoutPicker(widgetId)
          setSelectedWidgetForMove(widgetId)
          setLayoutPlacementTarget(null)
          setLayoutAnnouncement("Перемещение виджета «" + WIDGET_TITLES[widgetId] + "». Отпустите его на нужном месте.")
        }}
        onKeyDown={event => {
          if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return
          event.preventDefault()
          const position = widgetOrder.indexOf(widgetId)
          const target = Math.max(0, Math.min(layoutPreset.capacity - 1, position + (event.key === "ArrowUp" ? -1 : 1)))
          if (target === position) {
            setLayoutAnnouncement("Виджет «" + WIDGET_TITLES[widgetId] + "» уже в крайней позиции.")
            return
          }
          moveWidget(widgetId, target)
        }}
      />
      {renderWidgetPicker(widgetId)}
    </div>
  )

  const statusWidget = (
    <section
      key="status"
      className="info-section dashboard-widget-slot is-status risk-distribution-section"
      data-layout-mode={widgetLayoutModes.status ?? 'square'}
      data-widget-id={FIXED_WIDGET_ID}
      data-widget-slot-id={FIXED_WIDGET_ID}
    >
      <section aria-label="Распределение рисков" className="dashboard-side-card dashboard-status-card">
        <div className="dashboard-side-card-heading">
          <h2 aria-label="Распределение рисков" data-widget-part="header-title">
            Распределение рисков
          </h2>
          <div className="dashboard-status-heading-actions">
            <Link
              aria-label="Подробнее об объектах"
              className="dashboard-risk-details"
              data-widget-part="header-action"
              to="/objects"
            >
              Детали
              <svg aria-hidden="true" fill="none" viewBox="0 0 20 20">
                <path d="M6 14 14 6M7 6h7v7" />
              </svg>
            </Link>
          </div>
        </div>
        {dashboardError ? (
          <div className="dashboard-risk-error-state" role="alert">
            <span>Сводка временно недоступна</span>
            <button
              className="dashboard-risk-retry"
              onClick={() => setDashboardRetry(value => value + 1)}
              type="button"
            >
              Повторить
            </button>
          </div>
        ) : !dashboard ? (
          <p className="dashboard-empty-state" role="status">Загружаем распределение рисков…</p>
        ) : (
          <RiskDistributionWidget
            data={riskSummary.map(risk => ({ ...risk, fill: `var(--risk-${risk.key})` }))}
            hasRiskDistribution={dashboard?.risk_distribution != null}
            hasStats={Boolean(dashboard)}
          />
        )}
      </section>
    </section>
  )

  const predictionsWidget = (
    <section key="predictions" {...widgetInteractions('predictions')}>
      <div className="info-section-heading">
        <span>02</span>
        <h2 aria-label="Последние прогнозы" className="info-section-title">
          <span className="dashboard-widget-title-full">Последние прогнозы</span>
          <span aria-hidden="true" className="dashboard-widget-title-compact">Прогнозы</span>
        </h2>
        <Link aria-label="Все прогнозы" className="dashboard-widget-action" to="/predictions">
          Все
          <svg aria-hidden="true" fill="none" viewBox="0 0 20 20">
            <path d="M6 14 14 6M7 6h7v7" />
          </svg>
        </Link>
        {renderWidgetControl('predictions')}
      </div>
      {dashboardError ? (
        <p className="dashboard-empty-state">История прогнозов временно недоступна.</p>
      ) : !dashboard ? (
        <p className="dashboard-empty-state" role="status">Загружаем прогнозы…</p>
      ) : dashboard.recent_predictions?.length ? (
        <div className="prediction-mini">
          {dashboard.recent_predictions.slice(0, 3).map(prediction => (
            <Link
              className={`prediction-mini-item is-${predictionTone(prediction.risk_level)}`}
              key={prediction.id}
              to={`/objects/${prediction.object_id}`}
            >
              <span aria-hidden="true" className="dashboard-prediction-marker">!</span>
              <div className="dashboard-prediction-content">
                <div className="dashboard-prediction-heading">
                  <strong>{objectNames.get(prediction.object_id) ?? `Объект ${prediction.object_id}`}</strong>
                  <strong>{Number.isFinite(prediction.risk_score) ? `${Math.round(prediction.risk_score * 100)}%` : '—'}</strong>
                </div>
                <small>
                  {RISK_SHORT_LABELS[prediction.risk_level] ?? 'Оценка недоступна'}
                  {' · '}{formatMoscowTime(prediction.created_at)}
                  {' · '}{prediction.forecast_from_hours ?? '—'}–{prediction.forecast_to_hours ?? '—'} ч
                </small>
                <span aria-hidden="true" className="dashboard-prediction-track">
                  <i style={{ width: Number.isFinite(prediction.risk_score) ? `${Math.max(0, Math.min(100, Math.round(prediction.risk_score * 100)))}%` : '0%' }} />
                </span>
              </div>
            </Link>
          ))}
        </div>
      ) : (
        <p className="dashboard-empty-state">Нет последних прогнозов.</p>
      )}
    </section>
  )

  const activeRequestsWidget = (
    <section key="activeRequests" {...widgetInteractions('activeRequests')}>
      <div className="info-section-heading">
        <h2 aria-label="Активные заявки" className="info-section-title">
          <span className="dashboard-widget-title-full">Активные заявки</span>
          <span aria-hidden="true" className="dashboard-widget-title-compact">Заявки</span>
        </h2>
        <Link aria-label="Все активные заявки" className="dashboard-widget-action" to="/maintenance">
          Все
          <svg aria-hidden="true" fill="none" viewBox="0 0 20 20">
            <path d="M6 14 14 6M7 6h7v7" />
          </svg>
        </Link>
        {renderWidgetControl('activeRequests')}
      </div>
      <div className="dashboard-active-requests-table-wrap">
        <table aria-label="Список активных заявок" className="dashboard-active-requests-table">
          <thead><tr><th scope="col">Дата</th><th scope="col">Заявка</th><th scope="col">Статус</th></tr></thead>
          <tbody>
            {requestError ? (
              <tr><td colSpan="3">
                <span role="alert">Не удалось загрузить заявки.</span>{' '}
                <button onClick={() => setRequestRetry(value => value + 1)} type="button">Повторить</button>
              </td></tr>
            ) : activeRequests.length ? activeRequests.map(request => (
              <tr key={request.id}>
                <td>{formatRequestDate(request.created_at)}</td>
                <td>
                  {request.object_id != null ? (
                    <Link className="dashboard-active-request-object" to={`/objects/${request.object_id}`}>
                      <strong>{objectNames.get(request.object_id) ?? `Объект ${request.object_id}`}</strong>
                      <span>{request.reason}</span>
                    </Link>
                  ) : (
                    <span className="dashboard-active-request-object">
                      <strong>Объект не указан</strong>
                      <span>{request.reason}</span>
                    </span>
                  )}
                </td>
                <td><Link className="dashboard-active-request-status" to={`/maintenance?request_id=${request.id}`}>
                  {REQUEST_STATUS_LABELS[request.status] ?? 'Статус уточняется'}
                </Link></td>
              </tr>
            )) : (
              <tr><td className="dashboard-active-requests-empty" colSpan="3">
                {requestCount === null ? 'Загружаем заявки…' : 'Активных заявок нет'}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )

  const attentionWidgetInteraction = widgetInteractions('attention')
  const attentionWidget = (
    <section
      aria-labelledby="dispatcher-attention-title"
      key="attention"
      {...attentionWidgetInteraction}
      className={`${attentionWidgetInteraction.className} dispatcher-attention-widget`}
    >
      <div className="dispatcher-attention-heading">
        <h2 id="dispatcher-attention-title">Требуют внимания</h2>
        <div className="dispatcher-attention-heading-actions">
          <Link aria-label="Детали ситуаций, требующих внимания" className="dashboard-risk-details dispatcher-attention-details" to="/signals">
            Детали
            <svg aria-hidden="true" fill="none" viewBox="0 0 20 20">
              <path d="M6 14 14 6M7 6h7v7" />
            </svg>
          </Link>
          {renderWidgetControl('attention')}
        </div>
      </div>
      {attentionStatus === 'loading' ? (
        <p className="dispatcher-attention-empty" role="status">Загружаем ситуации…</p>
      ) : attentionStatus === 'error' ? (
        <div className="dispatcher-attention-empty" role="status">
          <span>Ситуации временно недоступны.</span>{' '}
          <button onClick={onRetryAttention} type="button">Повторить</button>
        </div>
      ) : attentionSignals.length ? (
        <ul className="record-list dispatcher-attention-list">{attentionSignals.map(signal => <li className="record-card dispatcher-attention-card" key={signal.card_id}>
          <div className="dispatcher-attention-card-heading">
            <h3>{signal.object_name}</h3>
            <span className="dispatcher-attention-state">{signalLabels[signal.state]}</span>
          </div>
          <p className="dispatcher-attention-risk">{riskText(signal)}</p>
          <div className="dispatcher-attention-meta">
            <span>Обновлено: {formatMoscowDateTime(signal.last_model_update)}</span>
            <Link to={`/objects/${signal.object_id}?signal=${encodeURIComponent(signal.card_id)}`}>Открыть <span aria-hidden="true">→</span></Link>
          </div>
        </li>)}</ul>
      ) : (
        <p className="dispatcher-attention-empty">Нет ситуаций, требующих внимания по выбранным фильтрам.</p>
      )}
    </section>
  )

  const widgetContent = {
    status: statusWidget,
    attention: attentionWidget,
    predictions: predictionsWidget,
    activeRequests: activeRequestsWidget,
  }

  const renderPresetCell = cellIndex => {
    const widgetId = widgetOrder[cellIndex]
    const cell = String.fromCharCode(97 + cellIndex)
    return cloneElement(widgetContent[widgetId], {
      key: widgetId,
      'data-layout-mode': widgetLayoutModes[widgetId] ?? 'compact',
      style: { gridArea: cell },
    })
  }

  const hasCustomWidgetLayout = layoutPreset.id !== "rows"
    || widgetOrder.some((widgetId, index) => widgetId !== DEFAULT_WIDGET_ORDERS[layoutPreset.id][index])

  return (
    <div
      aria-label="Сводные виджеты"
      className="dashboard-configurable-widgets"
      ref={widgetPanelRef}
    >
      {statusWidget}
      <section aria-label="Панель виджетов" className="dashboard-widget-region dashboard-side-card">
        <div
          aria-label="Область виджетов"
          className="dashboard-widget-stack dashboard-adjustable-widget-layout"
          data-layout-preset={layoutPreset.id}
          ref={widgetGridRef}
          style={{
            gridTemplateAreas: layoutPreset.areas,
            gridTemplateColumns: layoutPreset.columns,
            gridTemplateRows: DASHBOARD_LAYOUT_ROWS[layoutPreset.id] ?? layoutPreset.rows,
          }}
        >
          {Array.from({ length: layoutPreset.capacity }, (_, cellIndex) => renderPresetCell(cellIndex))}
        </div>
      </section>
      {layoutPickerOpen && createPortal(
        <div
          aria-label="Куда переместить виджет"
          className="dashboard-layout-picker is-drag-mode"
          id="dashboard-layout-picker"
          onPointerEnter={handleLayoutPickerEnter}
          onPointerLeave={handleLayoutPickerLeave}
          ref={layoutPickerRef}
          role="dialog"
          style={{
            ...(layoutPickerPosition ?? {}),
            visibility: layoutPickerPosition ? "visible" : "hidden",
          }}
        >
          <div className="dashboard-layout-picker-heading">
            <div><strong>Куда переместить виджет?</strong></div>
            {hasCustomWidgetLayout && (
              <button className="dashboard-widget-reset-button" onClick={resetWidgetLayout} type="button">
                Сбросить
              </button>
            )}
          </div>
          <div className="dashboard-layout-picker-options">
            {DASHBOARD_LAYOUT_PRESETS.map(preset => (
              <div className="dashboard-layout-preset" key={preset.id}>
                <div
                  aria-label={preset.title}
                  className={`dashboard-layout-preview is-${preset.id}`}
                  style={{
                    gridTemplateAreas: preset.areas,
                    gridTemplateColumns: preset.columns,
                    gridTemplateRows: preset.rows.replace(/minmax\(\d+px,/g, "minmax(0px,"),
                  }}
                >
                  {Array.from({ length: preset.capacity }, (_, cellIndex) => (
                    <button
                      aria-label={preset.title + ", ячейка " + (cellIndex + 1)}
                      className={
                        "dashboard-layout-preview-cell"
                        + (layoutPlacementTarget?.presetId === preset.id
                          && layoutPlacementTarget?.cellIndex === cellIndex ? " is-hovered" : "")
                      }
                      key={cellIndex}
                      onClick={() => applyLayoutPickerChoice(preset.id, cellIndex)}
                      onDragOver={event => handleLayoutCellDragOver(event, preset.id, cellIndex)}
                      onDrop={event => handleLayoutCellDrop(event, preset.id, cellIndex)}
                      style={{ gridArea: String.fromCharCode(97 + cellIndex) }}
                      type="button"
                    >
                      <span aria-hidden="true">{String.fromCharCode(65 + cellIndex)}</span>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>,
        document.body,
      )}
      <span aria-live="polite" className="dashboard-layout-announcement" role="status">
        {layoutAnnouncement}
      </span>
    </div>
  )
}
