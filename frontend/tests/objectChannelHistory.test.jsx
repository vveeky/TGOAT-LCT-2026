import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import GlobalObjectSearch from '../src/components/layout/GlobalObjectSearch'
import ChannelPage from '../src/pages/ChannelPage'
import PredictionsPage from '../src/pages/PredictionsPage'
import { moscowWallTimeToUTC } from '../src/dateTime'

const api=vi.hoisted(()=>({catalog:vi.fn(),channel:vi.fn(),history:vi.fn(),object:vi.fn(),journal:vi.fn(),replay:{mode:'normal'},role:'central_dispatcher'}))
vi.mock('../src/hooks/useReplayRuntime',()=>({useReplayRuntime:()=>({status:api.replay})}))
vi.mock('../src/auth/useAuth',()=>({useAuth:()=>({user:{role:api.role}})}))
vi.mock('../src/api/operational',()=>({getUserHistory:api.journal}))
vi.mock('../src/api/objects',()=>({getObjectCatalog:api.catalog,getObject:api.object}))
vi.mock('../src/api/channels',()=>({getChannel:api.channel,getChannelHistory:api.history}))
beforeEach(()=>{
  vi.clearAllMocks()
  api.replay={mode:'normal'};api.role='central_dispatcher'
  api.journal.mockResolvedValue({items:[],total:0})
  api.catalog.mockResolvedValue({items:[{id:28,name:'Объект Альфа'},{id:29,name:'Объект Бета'}]})
  api.channel.mockResolvedValue({id:2866,object_id:28,sensor_type:'КД АВ',latest_value:'Обнаружено движение',latest_alarm:true,last_event_at:'2026-05-05T12:00:00Z'})
  api.object.mockResolvedValue({id:28,name:'Объект Альфа'})
  api.history.mockResolvedValue({events:[]})
})
afterEach(cleanup)
function search(){render(<MemoryRouter><Routes><Route path="/" element={<GlobalObjectSearch/>}/><Route path="/objects/:id" element={<h1>Открытый объект</h1>}/></Routes></MemoryRouter>)}
function channel(){render(<MemoryRouter initialEntries={['/channels/2866']}><Routes><Route path="/channels/:id" element={<ChannelPage/>}/></Routes></MemoryRouter>)}
it('global search is debounced, selects by keyboard and navigates without a submit button',async()=>{
  search()
  const input=screen.getByRole('combobox')
  fireEvent.change(input,{target:{value:'Объект'}})
  expect(api.catalog).not.toHaveBeenCalled()
  await screen.findByRole('option',{name:'Объект Альфа'})
  fireEvent.keyDown(input,{key:'ArrowDown'})
  expect(screen.getByRole('option',{name:'Объект Бета'}).getAttribute('aria-selected')).toBe('true')
  fireEvent.keyDown(input,{key:'Enter'})
  expect(screen.getByRole('heading',{name:'Открытый объект'})).toBeTruthy()
})
it('Escape closes suggestions and the catalog is reused for subsequent queries',async()=>{
  search();const input=screen.getByRole('combobox')
  fireEvent.change(input,{target:{value:'Альфа'}})
  await screen.findByRole('option',{name:'Объект Альфа'})
  fireEvent.keyDown(input,{key:'Escape'})
  expect(screen.queryByRole('listbox')).toBeNull()
  fireEvent.change(input,{target:{value:'Бета'}})
  await screen.findByRole('option',{name:'Объект Бета'})
  expect(api.catalog).toHaveBeenCalledTimes(1)
})
it('channel defaults to a bounded seven-day server query and preserves registered alarm truth',async()=>{
  channel();await screen.findByRole('heading',{name:'КД АВ'})
  await waitFor(()=>expect(api.history).toHaveBeenCalled())
  expect(api.history.mock.calls[0][1]).toEqual({limit:50,offset:0,alarm_only:false,from:'2026-04-28T12:00:00.000Z',to:'2026-05-05T12:00:00.000Z'})
  expect(screen.getByText('Обнаружено движение')).toBeTruthy()
  expect(screen.getByText('Тревога')).toBeTruthy()
  expect(screen.getByRole('link',{name:/Объект Альфа/}).getAttribute('href')).toBe('/objects/28')
})
it('period and alarm filters go to the server, not just the visible rows',async()=>{
  channel();await screen.findByText('За выбранный период событий нет.')
  fireEvent.change(screen.getByLabelText('Период'),{target:{value:'1'}})
  await waitFor(()=>expect(api.history.mock.lastCall[1].from).toBe('2026-05-04T12:00:00.000Z'))
  fireEvent.click(screen.getByRole('checkbox',{name:'Только тревожные'}))
  await waitFor(()=>expect(api.history.mock.lastCall[1].alarm_only).toBe(true))
})
it('history pagination requests the next bounded server page',async()=>{
  api.history.mockResolvedValue({events:Array.from({length:50},(_,i)=>({timestamp:'2026-05-05T12:00:00Z',value:String(i),alarm:false}))})
  channel();await screen.findByText('Событий на странице: 50')
  fireEvent.click(screen.getByRole('button',{name:'Старее'}))
  await waitFor(()=>expect(api.history.mock.lastCall[1]).toMatchObject({offset:50,limit:50}))
})
it('forbidden channel does not offer a retry or expose metadata',async()=>{
  api.channel.mockRejectedValue({status:403});channel()
  await screen.findByRole('alert')
  expect(screen.queryByRole('button',{name:'Повторить'})).toBeNull()
  expect(api.history).not.toHaveBeenCalled()
})

it('replay periods end at virtual time and update with the replay clock',async()=>{
  api.replay={mode:'replay',virtual_time:'2026-05-06T06:00:00Z',session_id:'one'}
  api.channel.mockResolvedValue({id:2866,object_id:28,sensor_type:'КД АВ',latest_value:'Не замкнут',latest_alarm:true,last_event_at:'2026-05-06T05:00:00Z'})
  const ui=<MemoryRouter initialEntries={['/channels/2866']}><Routes><Route path="/channels/:id" element={<ChannelPage/>}/></Routes></MemoryRouter>
  const view=render(ui)
  await screen.findByText('Не замкнут')
  await waitFor(()=>expect(api.history.mock.lastCall[1].to).toBe('2026-05-06T06:00:00.000Z'))
  expect(screen.getByText(/Историческое воспроизведение/)).toBeTruthy()
  expect(screen.queryByText(/21 мая/)).toBeNull()
  api.replay={...api.replay,virtual_time:'2026-05-06T12:00:00Z'}
  view.rerender(<MemoryRouter initialEntries={['/channels/2866']}><Routes><Route path="/channels/:id" element={<ChannelPage/>}/></Routes></MemoryRouter>)
  await waitFor(()=>expect(api.history.mock.lastCall[1].to).toBe('2026-05-06T12:00:00.000Z'))
  expect(api.channel.mock.calls.length).toBeGreaterThan(1)
})

it('Moscow wall time conversion is independent of machine timezone',()=>{
  expect(moscowWallTimeToUTC('2026-05-06T09:00')).toBe('2026-05-06T06:00:00.000Z')
  expect(moscowWallTimeToUTC('2026-01-01T00:30')).toBe('2025-12-31T21:30:00.000Z')
  expect(()=>moscowWallTimeToUTC('bad')).toThrow(RangeError)
})

it('custom channel period sends Moscow inputs as explicit UTC instants',async()=>{
  channel();await screen.findByText('За выбранный период событий нет.')
  fireEvent.change(screen.getByLabelText('Период'),{target:{value:'custom'}})
  fireEvent.change(screen.getByLabelText('С — МСК'),{target:{value:'2026-05-05T09:00'}})
  fireEvent.change(screen.getByLabelText('По — МСК'),{target:{value:'2026-05-06T09:00'}})
  await waitFor(()=>expect(api.history.mock.lastCall[1]).toMatchObject({from:'2026-05-05T06:00:00.000Z',to:'2026-05-06T06:00:00.000Z'}))
})

it('reset hides old-session history while the replacement query is pending',async()=>{
  api.replay={mode:'replay',virtual_time:'2026-05-22T06:00:00Z',session_id:'old'}
  api.history.mockResolvedValue({events:[{timestamp:'2026-05-21T06:00:00Z',value:'Future observation',alarm:false}]})
  const tree=()=> <MemoryRouter initialEntries={['/channels/2866']}><Routes><Route path="/channels/:id" element={<ChannelPage/>}/></Routes></MemoryRouter>
  const view=render(tree())
  await screen.findByText('Future observation')
  api.replay={mode:'replay',virtual_time:'2026-05-06T06:00:00Z',session_id:'new'}
  api.history.mockImplementation(()=>new Promise(()=>{}))
  view.rerender(tree())
  expect(screen.queryByText('Future observation')).toBeNull()
  await waitFor(()=>expect(api.history.mock.lastCall[1].to).toBe('2026-05-06T06:00:00.000Z'))
  expect(screen.queryByText('Future observation')).toBeNull()
})

it.each([['technician','/tasks'],['central_dispatcher','/maintenance']])('history request route is canonical for %s',async(role,route)=>{
  api.role=role
  api.journal.mockResolvedValue({total:1,items:[{identity:'one',object_id:28,object_name:'Объект Альфа',snapshot_ts:'2026-05-06T06:00:00Z',prediction_status:'available',risk_probability:.63,request_id:5,request_status:'assigned'}]})
  render(<MemoryRouter><PredictionsPage/></MemoryRouter>)
  const link=await screen.findByRole('link',{name:/Связанная заявка/})
  expect(link.getAttribute('href')).toBe(`${route}?request_id=5`)
})
