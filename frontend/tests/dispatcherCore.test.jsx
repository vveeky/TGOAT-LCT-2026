import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import DispatcherObjectPage from '../src/pages/DispatcherObjectPage'
import SignalDecision from '../src/components/SignalDecision'
import SignalsPage from '../src/pages/SignalsPage'
import Sidebar from '../src/components/layout/Sidebar'
import MaintenancePage from '../src/pages/MaintenancePage'
import { monitoringPopulation, recentAttentionSignals } from '../src/dashboard/dispatcherMonitoring'
import { riskText, signalLabels } from '../src/api/signals'
import { mergeSignalContext } from '../src/dashboard/mergeSignalContext'

const mocks = vi.hoisted(() => ({ decide: vi.fn(), detail: vi.fn(), list: vi.fn(), maintenance: vi.fn(), objects: vi.fn(), create: vi.fn(), object: vi.fn(), channels: vi.fn(), plannedWork: vi.fn(), risk: vi.fn(), history: vi.fn() }))
vi.mock('../src/api/signals', async original => ({ ...(await original()), decideSignal: mocks.decide, getSignal: mocks.detail, getSignals: mocks.list }))
vi.mock('../src/hooks/useReplayRuntime', () => ({ useReplayRuntime: () => ({ status: { mode: 'replay' } }) }))
vi.mock('../src/hooks/useUnreadNotificationCount', () => ({ useUnreadNotificationCount: () => ({ count: 2, error: null }) }))
vi.mock('../src/auth/useAuth', () => ({ useAuth: () => ({ user: { role: 'central_dispatcher' } }) }))
vi.mock('../src/api/maintenance', () => ({ getMaintenanceRequests: mocks.maintenance, createMaintenanceRequest: mocks.create, updateMaintenanceRequest: vi.fn() }))
vi.mock('../src/api/objects', () => ({ getObjects: mocks.objects, getObject: mocks.object, getObjectChannels: mocks.channels, getObjectPlannedWork: mocks.plannedWork }))
vi.mock('../src/api/operational', () => ({ getOperationalObject: mocks.risk, getOperationalRiskHistory: mocks.history, getUserHistory:vi.fn().mockResolvedValue({items:[],total:0}) }))

const signal = { card_id: 'card-a', object_id: 1, object_name: 'Объект Альфа', state: 'new', work_version: 0,
  prediction_identity: 'p-a', assessment_status: 'available', risk_probability: .63,
  last_model_update: '2026-05-05T11:59:59+03:00', active_request: null, last_decision: null }
function mounted(ui) { return render(<MemoryRouter>{ui}</MemoryRouter>) }
beforeEach(() => { vi.clearAllMocks(); mocks.maintenance.mockResolvedValue({items:[],total:0});mocks.plannedWork.mockResolvedValue({source_status:'not_provided',unmapped_records:0,active:[],upcoming:[],recent:[]});mocks.list.mockResolvedValue({ items: [signal], total: 1, counts: { new: 1, monitoring: 0, inspection_requested: 0, resolved: 0 } }) })
afterEach(cleanup)

it('current 34% is distinguished from the open signal basis 64%, and decision keeps that exact context', async () => {
  const a = {...signal, object_id:28, risk_probability:.64, first_detected: signal.last_model_update}
  const b = {...a, card_id:'card-b', prediction_identity:'p-b', risk_probability:.95, last_model_update:'2026-05-06T11:59:59+03:00'}
  mocks.list.mockResolvedValue({items:[a,b],total:2,counts:{new:2}})
  mounted(<SignalsPage />)
  await screen.findByText(/64%/)
  expect(screen.getByText(/95%/)).toBeTruthy()
  const cards = screen.getAllByRole('link',{name:/^Открыть(?: сигнал)?$/})
  expect(cards[0].closest('li').textContent).toContain('64%')
  expect(cards[1].closest('li').textContent).toContain('95%')
  cleanup()
  mocks.object.mockResolvedValue({name:'Объект 28',channels_count:0})
  mocks.channels.mockResolvedValue({items:[]})
  mocks.history.mockResolvedValue([])
  mocks.risk.mockResolvedValue({status:'available',risk_probability:.34,snapshot_ts:'2026-05-05T17:59:59+03:00'})
  mocks.decide.mockResolvedValue({signal:{...a,state:'monitoring',work_version:1}})
  render(<MemoryRouter initialEntries={['/objects/28?signal=card-a']}><Routes><Route path="/objects/:id" element={<DispatcherObjectPage />} /></Routes></MemoryRouter>)
  await screen.findByText('Сигнал создан при оценке риска 64%')
  expect(screen.getByRole('heading',{name:'Текущая оценка'}).closest('section').textContent).toContain('34%')
  expect(screen.getByText(/Текущая оценка снизилась до 34%, но сигнал остаётся открытым до решения диспетчера/)).toBeTruthy()
  expect(screen.getByText(/Основание сигнала/).closest('.decision-basis-card').textContent).toContain('64%')
  await userEvent.click(screen.getByRole('button',{name:'Оставить под наблюдением'}))
  await userEvent.click(screen.getByRole('button',{name:'Подтвердить решение'}))
  await waitFor(() => expect(mocks.decide).toHaveBeenCalledWith('card-a', expect.objectContaining({prediction_identity:'p-a'})))
})

it('decision UI shows only the fields relevant to the selected action', async () => {
  mounted(<SignalDecision signal={signal} onUpdate={() => {}} />)
  expect(screen.getByLabelText('Причина проверки')).toBeTruthy()
  expect(screen.getByLabelText('Приоритет')).toBeTruthy()
  await userEvent.click(screen.getByRole('button',{name:'Оставить под наблюдением'}))
  expect(screen.queryByLabelText('Причина проверки')).toBeNull()
  expect(screen.queryByLabelText('Приоритет')).toBeNull()
  expect(screen.getByLabelText('Комментарий')).toBeTruthy()
})

it('object channels render together in one honeycomb without pagination', async () => {
  mocks.object.mockResolvedValue({id:28,name:'Объект Альфа',channels_count:268})
  mocks.channels.mockResolvedValue({items:Array.from({length:25},(_,index)=>({
    id:index+1,sensor_type:`Датчик ${index+1}`,latest_value:index===1?null:'Состояние зарегистрировано',
    latest_alarm:index===0,last_event_at:'2026-05-05T12:00:00Z',
  }))})
  mocks.list.mockResolvedValue({items:[],total:0,counts:{}})
  mocks.risk.mockResolvedValue({status:'available',risk_probability:.34,snapshot_ts:'2026-05-05T12:00:00Z'})
  mocks.history.mockResolvedValue([])
  mocks.maintenance.mockResolvedValue({items:[],total:0})
  render(<MemoryRouter initialEntries={['/objects/28']}><Routes><Route path="/objects/:id" element={<DispatcherObjectPage/>}/></Routes></MemoryRouter>)
  await screen.findByRole('heading',{name:'Объект Альфа', level:1})
  await screen.findAllByText('Датчик 1')
  expect(document.querySelectorAll('.channel-risk-hex')).toHaveLength(25)
  expect(screen.getAllByText('Тревога').length).toBeGreaterThan(0)
  expect(screen.getAllByText('Нет актуального значения').length).toBeGreaterThan(0)
  expect(screen.getByText('268')).toBeTruthy()
  expect(screen.queryByRole('navigation', { name: 'Страницы каналов' })).toBeNull()
  expect(document.querySelectorAll('.object-channel-row')).toHaveLength(25)
})

it('filters a large honeycomb to attention channels and distinguishes model risk from sensor state', async () => {
  const channels = Array.from({ length: 919 }, (_, index) => ({
    id: index === 0 ? 2866 : index + 1,
    sensor_type: `Датчик ${index + 1}`,
    latest_value: 'Норма',
    latest_alarm: false,
    risk_level: index < 4 ? 'high' : 'low',
    risk_score: index < 4 ? .91 : .12,
    risk_score_kind: 'ml',
    last_event_at: '2026-05-03T12:00:00Z',
  }))
  mocks.object.mockResolvedValue({ id: 28, name: 'Объект Вита', channels_count: 919 })
  mocks.channels.mockResolvedValue({ items: channels })
  mocks.list.mockResolvedValue({ items: [], total: 0, counts: {} })
  mocks.risk.mockResolvedValue({ status: 'available', risk_probability: .34, snapshot_ts: '2026-05-05T12:00:00Z' })
  mocks.history.mockResolvedValue([])
  mocks.maintenance.mockResolvedValue({ items: [], total: 0 })

  const view = render(<MemoryRouter initialEntries={['/objects/28']}><Routes><Route path="/objects/:id" element={<DispatcherObjectPage />} /></Routes></MemoryRouter>)
  await screen.findByRole('heading', { name: 'Объект Вита', level: 1 })
  const firstHex = view.container.querySelector('.channel-risk-hex')
  fireEvent.click(firstHex)
  expect(screen.getAllByText('Высокая оценка риска').length).toBeGreaterThan(0)
  expect(screen.getByText('Последнее состояние датчика')).toBeTruthy()
  expect(screen.getByText('Последнее состояние датчика').nextElementSibling.textContent).toBe('Норма')

  await userEvent.click(screen.getByRole('checkbox', { name: /Только требующие внимания/ }))
  expect(document.querySelectorAll('.channel-risk-hex')).toHaveLength(4)
  expect(view.container.textContent).toContain('4 из 919')

  await userEvent.click(screen.getByRole('checkbox', { name: /Только требующие внимания/ }))
  expect(document.querySelectorAll('.channel-risk-hex')).toHaveLength(919)
  expect(document.querySelectorAll('.object-channel-row')).toHaveLength(919)
  expect(screen.queryByRole('spinbutton', { name: 'Перейти на страницу каналов' })).toBeNull()
})

it('visible decision basis stays aligned with frozen command while same-card polling updates risk', async () => {
  mocks.decide.mockResolvedValue({signal:{...signal,state:'monitoring',work_version:1}})
  const view = mounted(<SignalDecision signal={signal} onUpdate={() => {}} />)
  await userEvent.click(screen.getByRole('button',{name:'Оставить под наблюдением'}))
  view.rerender(<MemoryRouter><SignalDecision signal={{...signal,prediction_identity:'p-next',risk_probability:.71}} onUpdate={() => {}} /></MemoryRouter>)
  expect(screen.getByText(/Основание сигнала/).closest('.decision-basis-card').textContent).toContain('63%')
  expect(screen.queryByText(/71%/)).toBeNull()
  await userEvent.click(screen.getByRole('button',{name:'Подтвердить решение'}))
  await waitFor(() => expect(mocks.decide).toHaveBeenCalled())
  expect(mocks.decide.mock.calls[0][1].prediction_identity).toBe('p-a')
})

describe('dispatcher decision behavior', () => {
  it('offers exactly three human actions and no confirmed primary action', () => {
    mounted(<SignalDecision signal={signal} onUpdate={() => {}} />)
    expect(screen.getByRole('button', {name: 'Запросить проверку'})).toBeTruthy()
    expect(screen.getByRole('button', {name: 'Оставить под наблюдением'})).toBeTruthy()
    expect(screen.getByRole('button', {name: 'Основание не подтвердилось'})).toBeTruthy()
    expect(screen.queryByRole('button', {name: 'Подтвердить'})).toBeNull()
  })
  it('inspection command contains exact context/version and success links real request', async () => {
    const result = { ...signal, state: 'inspection_requested', work_version: 1, active_request: {id: 42} }
    mocks.decide.mockResolvedValue({signal: result})
    const update = vi.fn()
    const view = mounted(<SignalDecision signal={signal} onUpdate={update} />)
    await userEvent.type(screen.getByLabelText('Причина проверки'), 'Проверить датчик')
    await userEvent.click(screen.getByRole('button', {name: 'Подтвердить решение'}))
    await waitFor(() => expect(update).toHaveBeenCalledWith(result))
    expect(mocks.decide.mock.calls[0][1]).toMatchObject({ expected_version: 0, prediction_identity: 'p-a', request: { reason: 'Проверить датчик', priority: 'normal' } })
    view.rerender(<MemoryRouter><SignalDecision signal={result} onUpdate={update} /></MemoryRouter>)
    expect(screen.getByRole('link',{name: /Открыть заявку №42/}).getAttribute('href')).toBe('/maintenance?request_id=42')
  })
  it.each([['Оставить под наблюдением','monitoring'],['Основание не подтвердилось','false_alarm']])('%s sends no request payload', async (label, action) => {
    mocks.decide.mockResolvedValue({signal: {...signal, state: action === 'monitoring' ? 'monitoring' : 'resolved'}})
    mounted(<SignalDecision signal={signal} onUpdate={() => {}} />)
    await userEvent.click(screen.getByRole('button',{name: label}))
    if (action === 'false_alarm') {
      await userEvent.type(screen.getByLabelText('Основание решения'), 'Основание проверки не выявило неисправность')
    }
    await userEvent.click(screen.getByRole('button',{name: 'Подтвердить решение'}))
    await waitFor(() => expect(mocks.decide).toHaveBeenCalled())
    expect(mocks.decide.mock.calls[0][1]).toMatchObject({ decision: action, request: null })
  })
  it('409 preserves reason/comment, refreshes, and never automatically resends', async () => {
    mocks.decide.mockRejectedValue({status: 409})
    mocks.detail.mockResolvedValue({...signal, state: 'monitoring', work_version: 1})
    const update = vi.fn()
    mounted(<SignalDecision signal={signal} onUpdate={update} />)
    await userEvent.type(screen.getByLabelText('Причина проверки'), 'Причина B')
    await userEvent.type(screen.getByLabelText('Комментарий'), 'Комментарий B')
    await userEvent.click(screen.getByRole('button',{name: 'Подтвердить решение'}))
    await waitFor(() => expect(update).toHaveBeenCalled())
    expect(screen.getByLabelText('Комментарий').value).toBe('Комментарий B')
    expect(screen.getByLabelText('Причина проверки').value).toBe('Причина B')
    expect(screen.getByRole('status').textContent).toContain('другим диспетчером')
    expect(mocks.decide).toHaveBeenCalledTimes(1)
  })
  it('lost response retries same key and original version even if polling updates signal', async () => {
    mocks.decide.mockRejectedValueOnce({status: 0}).mockResolvedValueOnce({signal: {...signal, state:'monitoring',work_version:1}})
    const view = mounted(<SignalDecision signal={signal} onUpdate={() => {}} />)
    await userEvent.click(screen.getByRole('button',{name:'Оставить под наблюдением'}))
    await userEvent.click(screen.getByRole('button',{name:'Подтвердить решение'}))
    await screen.findByText(/Не удалось сохранить решение/)
    view.rerender(<MemoryRouter><SignalDecision signal={{...signal,work_version:1}} onUpdate={() => {}} /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button',{name:'Подтвердить решение'}))
    await waitFor(() => expect(mocks.decide).toHaveBeenCalledTimes(2))
    expect(mocks.decide.mock.calls[1][1]).toEqual(mocks.decide.mock.calls[0][1])
  })
  it('editing freezes expected version so background polling cannot silently overwrite another decision', async () => {
    mocks.decide.mockRejectedValue({status:409})
    mocks.detail.mockResolvedValue({...signal,work_version:1,state:'monitoring'})
    const view = mounted(<SignalDecision signal={signal} onUpdate={() => {}} />)
    await userEvent.type(screen.getByLabelText('Комментарий'),'Текст B')
    view.rerender(<MemoryRouter><SignalDecision signal={{...signal,work_version:1,state:'monitoring'}} onUpdate={() => {}} /></MemoryRouter>)
    await userEvent.click(screen.getByRole('button',{name:'Оставить под наблюдением'}))
    await userEvent.click(screen.getByRole('button',{name:'Подтвердить решение'}))
    await waitFor(() => expect(mocks.decide).toHaveBeenCalled())
    expect(mocks.decide.mock.calls[0][1].expected_version).toBe(0)
    expect(screen.getByLabelText('Комментарий').value).toBe('Текст B')
  })
})

it('dispatcher sidebar routes the alert icon to forecasts without a notifications entry', () => {
  mounted(<Sidebar collapsed={false} onToggle={() => {}} />)
  for (const name of ['Мониторинг','Прогнозы','Объекты','Заявки','История прогнозов','Сценарии']) expect(screen.getByRole('link',{name})).toBeTruthy()
  expect(screen.queryByText('Округа · состояние · риск')).toBeNull()
  expect(screen.queryByRole('link',{name:'Сигналы'})).toBeNull()
  expect(screen.queryByRole('link',{name:'Уведомления'})).toBeNull()
  expect(screen.getByRole('link',{name:'Прогнозы'}).textContent).not.toContain('2')
})
it('signals queue keeps one card row and opening does not mutate', async () => {
  mounted(<SignalsPage />)
  await screen.findByText('Объект Альфа')
  expect(screen.getAllByRole('link',{name:/^Открыть(?: сигнал)?$/})).toHaveLength(1)
  fireEvent.click(screen.getByRole('link',{name:/^Открыть(?: сигнал)?$/}))
  expect(mocks.decide).not.toHaveBeenCalled()
})
it('signals empty and errors do not claim system health', async () => {
  mocks.list.mockResolvedValueOnce({items:[],total:0,counts:{}})
  const view = mounted(<SignalsPage />)
  await screen.findByText('Нет ситуаций, требующих внимания по выбранным фильтрам.')
  view.unmount()
  mocks.list.mockRejectedValue({status: 503})
  mounted(<SignalsPage />)
  expect((await screen.findByRole('alert')).textContent).toContain('Не удалось загрузить')
  expect(screen.queryByText('Система исправна')).toBeNull()
})
it('unknown risk is never presented as zero; primary copy contains no internal jargon', () => {
  expect(riskText({...signal,assessment_status:'degraded',risk_probability: null})).toBe('Оценка временно недоступна')
  expect(riskText(signal)).toContain('63%')
  expect(Object.values(signalLabels).join(' ')).not.toMatch(/ML|snapshot|available|frozen/)
})
it('same scoped population drives map, list and counts; default excludes resolved and no-signal objects', () => {
  const features = [{type:'Feature',properties:{code:'A'},geometry:{type:'Polygon',coordinates:[[[0,0],[1,0],[1,1],[0,1],[0,0]]]}},
    {type:'Feature',properties:{code:'B'},geometry:{type:'Polygon',coordinates:[[[2,0],[3,0],[3,1],[2,1],[2,0]]]}}]
  const data = {objects:[{id:1},{id:2},{id:3}],items:[signal,{...signal,object_id:2,card_id:'b',state:'resolved'}]}
  expect(monitoringPopulation(data,features,null).objects.map(o=>o.id)).toEqual([1])
  const selected = monitoringPopulation(data,features,'A',true)
  const ids = new Set(selected.objects.map(o=>o.id))
  expect(selected.signals.every(s=>ids.has(s.object_id))).toBe(true)
  expect(selected.counts.attention).toBe(selected.signals.length)
  expect(monitoringPopulation(data,[],null).signals).toHaveLength(1)
})
it('attention list is limited to the three newest signals after area scoping', () => {
  const signals = [
    {...signal, card_id:'oldest', last_model_update:'2026-05-01T00:00:00Z'},
    {...signal, card_id:'newest', last_model_update:'2026-05-04T00:00:00Z'},
    {...signal, card_id:'middle', last_model_update:'2026-05-03T00:00:00Z'},
    {...signal, card_id:'second', last_model_update:'2026-05-02T00:00:00Z'},
  ]
  expect(recentAttentionSignals(signals).map(item=>item.card_id)).toEqual(['newest','middle','second'])
  expect(recentAttentionSignals(signals, 2).map(item=>item.card_id)).toEqual(['newest','middle'])
})
it('late read cannot revert a committed human decision but can update model context', () => {
  const committed = {...signal,state:'inspection_requested',work_version:2,active_request:{id:1}}
  const incoming = {...signal,last_model_update:'new-time',risk_probability:.8}
  const merged = mergeSignalContext([incoming],[committed])[0]
  expect(merged.state).toBe('inspection_requested')
  expect(merged.work_version).toBe(2)
  expect(merged.active_request.id).toBe(1)
  expect(merged.risk_probability).toBe(.8)
})
it('manual request uses searchable object choice and persists draft, no arbitrary status selector', async () => {
  mocks.objects.mockResolvedValue({items:[{id:1,name:'Объект Альфа'},{id:2,name:'Объект Бета'}]})
  mocks.maintenance.mockResolvedValue({items:[],total:0})
  mocks.create.mockResolvedValue({id:1,status:'draft'})
  render(<MemoryRouter initialEntries={['/maintenance?create=1']}><MaintenancePage/></MemoryRouter>)
  await screen.findByText('Заявок: 0')
  await userEvent.type(screen.getByPlaceholderText('Поиск объекта по названию'),'Бета')
  expect(screen.queryByRole('button',{name:'Объект Альфа'})).toBeNull()
  await userEvent.click(screen.getByRole('button',{name:'Объект Бета'}))
  await userEvent.type(screen.getByLabelText('Причина'),'Ручная проверка')
  await userEvent.click(screen.getByRole('button',{name:'Сохранить черновик'}))
  await waitFor(() => expect(mocks.create).toHaveBeenCalledWith(expect.objectContaining({object_id:2,reason:'Ручная проверка'})))
  expect(screen.queryByLabelText(/Статус заявки/)).toBeNull()
})
