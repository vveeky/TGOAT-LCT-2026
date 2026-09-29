import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import RequestExecutionPage, { RequestDetail } from '../src/pages/RequestExecutionPage'
import Sidebar from '../src/components/layout/Sidebar'

const mocks=vi.hoisted(()=>({role:'central_dispatcher',detail:vi.fn(),list:vi.fn(),eligible:vi.fn(),command:vi.fn()}))
vi.mock('../src/api/maintenance',()=>({getMaintenanceRequest:mocks.detail,getMaintenanceRequests:mocks.list,getEligibleTechnicians:mocks.eligible,executeMaintenanceCommand:mocks.command}))
vi.mock('../src/auth/useAuth',()=>({useAuth:()=>({user:{role:mocks.role,user_id:mocks.role==='technician'?'tech':'central'}})}))
vi.mock('../src/hooks/useUnreadNotificationCount',()=>({useUnreadNotificationCount:()=>({count:0})}))
vi.mock('../src/components/layout/ProfileMenu',()=>({default:()=>null}))
const base={id:1,object_id:28,reason:'Проверить датчик',priority:'normal',status:'new',version:1,results:[],timeline:[],historical_basis:{risk_probability:.63,snapshot_ts:'2024-01-01T00:00:00Z'},object_current:{risk_probability:.95,prediction_status:'available'}}
function mount(component){return render(<MemoryRouter>{component}</MemoryRouter>)}
beforeEach(()=>{vi.clearAllMocks();mocks.role='central_dispatcher';mocks.detail.mockResolvedValue(base);mocks.eligible.mockResolvedValue([{user_id:'eligible',username:'Допустимый техник'}]);mocks.list.mockResolvedValue({items:[],total:0})})
afterEach(()=>{cleanup();vi.useRealTimers();vi.restoreAllMocks()})

function LocationProof(){const location=useLocation();return <output data-testid="location">{location.pathname}{location.search}</output>}
function draftPage(){return render(<MemoryRouter initialEntries={['/tasks?request_id=1']}><LocationProof/><RequestExecutionPage/></MemoryRouter>)}
it.each([['submit','Отправить заявку','new','Заявка отправлена диспетчеру на назначение.'],['cancel','Отменить черновик','cancelled','Черновик отменён.']])('technician draft %s hands off without inaccessible detail polling',async(action,label,status,message)=>{
  const timers=vi.spyOn(globalThis,'setInterval'),cleared=vi.spyOn(globalThis,'clearInterval')
  mocks.role='technician';mocks.detail.mockResolvedValue({...base,status:'draft',created_by_user_id:'tech'})
  mocks.command.mockResolvedValue({...base,status,version:2})
  draftPage();await screen.findByRole('button',{name:'Отправить заявку'})
  const detailTimer=timers.mock.results[0].value
  if(action==='cancel')await userEvent.type(screen.getByLabelText('Причина отмены'),'Не требуется')
  await userEvent.click(screen.getByRole('button',{name:label}))
  await screen.findByText(message);await screen.findByText('Сейчас назначенных вам задач нет.')
  expect(screen.getByTestId('location').textContent).toBe('/tasks')
  expect(screen.queryByText('Заявка не найдена или недоступна.')).toBeNull()
  expect(cleared).toHaveBeenCalledWith(detailTimer)
  const reads=mocks.detail.mock.calls.length
  vi.useFakeTimers();await vi.advanceTimersByTimeAsync(30000)
  expect(mocks.detail).toHaveBeenCalledTimes(reads)
})
it('dispatcher sidebar keeps forecasts and exposes notifications in the topbar',()=>{
  mount(<Sidebar collapsed={false} onToggle={()=>{}}/>)
  for(const name of ['Мониторинг','Прогнозы','Объекты','Заявки','История прогнозов','Сценарии'])expect(screen.getByRole('link',{name,exact:true})).toBeTruthy()
  expect(screen.queryByText('Округа · состояние · риск')).toBeNull()
  expect(screen.queryByRole('link',{name:'Сигналы',exact:true})).toBeNull()
  expect(screen.queryByRole('link',{name:'Уведомления',exact:true})).toBeNull()
})
it('technician task list highlights assignments and returned work',async()=>{
  mocks.role='technician'
  mocks.list.mockResolvedValue({items:[
    {...base,status:'assigned',latest_event:'assigned',assignee_user_id:'tech'},
    {...base,id:2,status:'in_progress',latest_event:'returned',assignee_user_id:'tech'},
  ],total:2})
  mount(<RequestExecutionPage/>)
  expect(await screen.findByText('Новое назначение: 1')).toBeTruthy()
  expect(screen.getByText('Возвращена на доработку: 1')).toBeTruthy()
  expect(screen.getByText('Новое назначение')).toBeTruthy()
  expect(screen.getAllByText('Возвращена на доработку').length).toBeGreaterThan(0)
})
it.each([500,409])('failed draft handoff %s preserves form and does not navigate',async(status)=>{
  mocks.role='technician';mocks.detail.mockResolvedValue({...base,status:'draft'});mocks.command.mockRejectedValue({status})
  draftPage();await userEvent.type(await screen.findByLabelText('Причина отмены'),'Сохранить комментарий')
  await userEvent.click(screen.getByRole('button',{name:'Отправить заявку'}))
  await screen.findByText(/Введённый текст сохранён/)
  expect(screen.getByTestId('location').textContent).toBe('/tasks?request_id=1')
  expect(screen.getByLabelText('Причина отмены').value).toBe('Сохранить комментарий')
  expect(mocks.command).toHaveBeenCalledTimes(1)
})
it('lost-response draft retry retains the original idempotency key',async()=>{
  mocks.role='technician';mocks.detail.mockResolvedValue({...base,status:'draft'})
  mocks.command.mockRejectedValueOnce({status:0}).mockResolvedValue({...base,status:'new',version:2})
  draftPage();await userEvent.click(await screen.findByRole('button',{name:'Отправить заявку'}))
  await screen.findByText(/Не удалось сохранить действие/)
  await userEvent.click(screen.getByRole('button',{name:'Отправить заявку'}))
  await screen.findByText('Заявка отправлена диспетчеру на назначение.')
  expect(mocks.command.mock.calls[0][2]).toEqual(mocks.command.mock.calls[1][2])
})
it.each(['central_dispatcher','technician'])('%s list uses canonical object name',async(role)=>{
  mocks.role=role;mocks.list.mockResolvedValue({items:[{...base,object_name:'объект Вита'}],total:1})
  mount(<RequestExecutionPage/>);await screen.findByRole('heading',{name:'объект Вита'})
  expect(screen.queryByRole('heading',{name:'Объект 28'})).toBeNull()
})
it('dispatcher keeps accessible detail after publishing own draft',async()=>{
  mocks.detail.mockResolvedValue({...base,status:'draft'});mocks.command.mockResolvedValue({...base,status:'new',version:2})
  draftPage();await userEvent.click(await screen.findByRole('button',{name:'Отправить заявку'}))
  await screen.findByText('Действие сохранено.')
  expect(screen.getByTestId('location').textContent).toBe('/tasks?request_id=1')
})
it('handoff flash is consumed from navigation state and not restored on remount',async()=>{
  mocks.role='technician'
  function FlashProof(){const location=useLocation();return <output data-testid="flash-state">{JSON.stringify(location.state)}</output>}
  const view=render(<MemoryRouter initialEntries={[{pathname:'/tasks',state:{requestHandoff:'submitted'}}]}><FlashProof/><RequestExecutionPage/></MemoryRouter>)
  await screen.findByText('Заявка отправлена диспетчеру на назначение.')
  await waitFor(()=>expect(screen.getByTestId('flash-state').textContent).toBe('null'))
  view.unmount();mount(<RequestExecutionPage/>)
  await screen.findByText('Заявок: 0')
  expect(screen.queryByText('Заявка отправлена диспетчеру на назначение.')).toBeNull()
})

it('technician landing and sidebar show own tasks, not dispatcher monitoring',async()=>{
  mocks.role='technician'
  mount(<><Sidebar collapsed={false} onToggle={()=>{}}/><RequestExecutionPage/></>)
  await screen.findByText('Заявок: 0')
  expect(screen.getByRole('heading',{name:'Мои задачи'})).toBeTruthy()
  expect(screen.queryByRole('link',{name:'Мониторинг'})).toBeNull()
  expect(screen.queryByRole('link',{name:'Прогнозы'})).toBeNull()
})
it('dispatcher selects only server-provided eligible technicians',async()=>{
  mocks.command.mockResolvedValue({...base,status:'assigned',version:2,assignee_user_id:'eligible'})
  mount(<RequestDetail id={1}/>)
  await screen.findByRole('option',{name:'Допустимый техник'})
  await userEvent.selectOptions(screen.getByLabelText('Исполнитель'),'eligible')
  await userEvent.click(screen.getByRole('button',{name:'Назначить',exact:true}))
  await waitFor(()=>expect(mocks.command).toHaveBeenCalledWith(1,'assign',expect.objectContaining({expected_version:1,assignee_user_id:'eligible'})))
})
it('assigned technician starts but cannot assign or accept',async()=>{
  mocks.role='technician';mocks.detail.mockResolvedValue({...base,status:'assigned',assignee_user_id:'tech'})
  mocks.command.mockResolvedValue({...base,status:'in_progress',assignee_user_id:'tech',version:2})
  mount(<RequestDetail id={1}/>)
  await userEvent.click(await screen.findByRole('button',{name:'Начать проверку'}))
  await screen.findByLabelText('Описание результата')
  expect(screen.queryByRole('button',{name:'Принять результат'})).toBeNull()
})
it('technician submits a required description and sees read-only submitted result',async()=>{
  mocks.role='technician';mocks.detail.mockResolvedValue({...base,status:'in_progress',version:3})
  mocks.command.mockResolvedValue({...base,status:'awaiting_review',version:4,results:[{id:1,revision:1,result_code:'no_issue_found',summary:'Проверено',technician_user_id:'tech'}]})
  mount(<RequestDetail id={1}/>)
  await screen.findByLabelText('Результат проверки')
  expect(screen.getByRole('button',{name:'Отправить результат'}).disabled).toBe(true)
  await userEvent.selectOptions(await screen.findByLabelText('Результат проверки'),'no_issue_found')
  await userEvent.type(await screen.findByLabelText('Описание результата'),'Проверено')
  await userEvent.click(screen.getByRole('button',{name:'Отправить результат'}))
  await screen.findByText('Проверено')
  expect(screen.queryByLabelText('Результат проверки')).toBeNull()
  expect(mocks.command.mock.calls[0][2]).toMatchObject({summary:'Проверено',result_code:'no_issue_found',expected_version:3})
})
it('409 preserves technician text and never auto-resends',async()=>{
  mocks.role='technician';mocks.detail.mockResolvedValue({...base,status:'in_progress',version:3});mocks.command.mockRejectedValue({status:409})
  mount(<RequestDetail id={1}/>)
  await userEvent.selectOptions(await screen.findByLabelText('Результат проверки'),'no_issue_found')
  await userEvent.type(await screen.findByLabelText('Описание результата'),'Сохранить описание')
  await userEvent.click(screen.getByRole('button',{name:'Отправить результат'}))
  await screen.findByText(/Введённый текст сохранён/)
  expect(screen.getByLabelText('Описание результата').value).toBe('Сохранить описание')
  expect(mocks.command).toHaveBeenCalledTimes(1)
})
it.each([['Принять результат','accept'],['Вернуть на доработку','return']])('dispatcher %s uses explicit command',async(label,action)=>{
  mocks.detail.mockResolvedValue({...base,status:'awaiting_review',version:4,results:[{id:1,revision:1,result_code:'issue_found',summary:'Результат техника'}]})
  mocks.command.mockResolvedValue({...base,status:action==='accept'?'closed':'in_progress',version:5})
  mount(<RequestDetail id={1}/>)
  await userEvent.type(await screen.findByLabelText('Комментарий диспетчера'),'Комментарий приёмки')
  await userEvent.click(screen.getByRole('button',{name:label}))
  await waitFor(()=>expect(mocks.command).toHaveBeenCalledWith(1,action,expect.objectContaining({expected_version:4,comment:'Комментарий приёмки'})))
})
it('returned task retains previous revision and return explanation',async()=>{
  mocks.role='technician';mocks.detail.mockResolvedValue({...base,status:'in_progress',results:[{id:1,revision:1,result_code:'unable_to_verify',summary:'Не был доступен объект'}],timeline:[{id:1,event:'returned',comment:'Повторить с доступом'}]})
  mount(<RequestDetail id={1}/>)
  await screen.findByText('Требуется доработка: Повторить с доступом')
  expect(screen.getByText('Не был доступен объект')).toBeTruthy()
})
it('detail distinctly labels immutable 63% basis and current 95% object risk',async()=>{
  mount(<RequestDetail id={1}/>)
  await screen.findByText(/Основание проверки на момент решения диспетчера: 63%/)
  expect(screen.getByText('Текущая оценка объекта: 95%')).toBeTruthy()
})
it('closed and unavailable tasks do not offer mutation controls',async()=>{
  mocks.detail.mockResolvedValue({...base,status:'closed',accepted_by_user_id:'central',accepted_at:'2024-01-02T00:00:00Z'})
  const view=mount(<RequestDetail id={1}/>)
  await screen.findByText(/Принято: central/)
  expect(screen.queryByRole('button',{name:'Назначить',exact:true})).toBeNull()
  view.unmount();mocks.detail.mockRejectedValue({status:404})
  mount(<RequestDetail id={2}/>)
  await screen.findByText('Заявка не найдена или недоступна.')
})
