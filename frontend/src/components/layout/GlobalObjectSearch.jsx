import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getObjectCatalog } from '../../api/objects'

export default function GlobalObjectSearch({initialValue=''}){
  const navigate=useNavigate(),catalog=useRef(null)
  const [loading,setLoading]=useState(false)
  const [query,setQuery]=useState(initialValue),[items,setItems]=useState([]),[selected,setSelected]=useState(0),[open,setOpen]=useState(false),[error,setError]=useState(false)
  useEffect(()=>{
    let active=true
    const timer=setTimeout(async()=>{
      if(!query.trim()){if(active)setItems([]);return}
      try{
        if(active)setLoading(true)
        catalog.current??=await getObjectCatalog()
        if(active){setItems(catalog.current.items.filter(item=>(item.name+' '+item.id).toLocaleLowerCase('ru').includes(query.trim().toLocaleLowerCase('ru'))).slice(0,8));setSelected(0);setError(false)}
      }catch{if(active)setError(true)}finally{if(active)setLoading(false)}
    },250)
    return()=>{active=false;clearTimeout(timer)}
  },[query])
  const choose=item=>{setOpen(false);navigate(`/objects/${item.id}`)}
  return <div className="topbar-search is-expanded" style={{position:'relative'}}>
    <svg aria-hidden="true" className="topbar-search-icon" fill="none" viewBox="0 0 24 24">
      <circle cx="10.8" cy="10.8" r="6.8" />
      <path d="m16 16 4.3 4.3" />
    </svg>
    <label className="sr-only" htmlFor="global-object-search">Поиск объектов</label>
    <input id="global-object-search" type="search" role="combobox" aria-autocomplete="list" aria-expanded={open&&!!query.trim()} aria-controls="object-search-results" aria-activedescendant={open&&items[selected]?`suggestion-${items[selected].id}`:undefined} placeholder="Поиск объектов" autoComplete="off" value={query} onChange={e=>{setQuery(e.target.value);setItems([]);setSelected(0);setLoading(!!e.target.value.trim());setOpen(true)}} onFocus={()=>setOpen(true)} onBlur={()=>setOpen(false)} onKeyDown={e=>{
      if(e.key==='Escape'){setOpen(false);e.preventDefault()}
      if(e.key==='ArrowDown'){setOpen(true);setSelected(x=>Math.max(0,Math.min(x+1,items.length-1)));e.preventDefault()}
      if(e.key==='ArrowUp'){setSelected(x=>Math.max(0,x-1));e.preventDefault()}
      if(e.key==='Enter'){e.preventDefault();if(open&&items[selected])choose(items[selected])}
    }}/>
    {open&&query.trim()&&<div id="object-search-results" role="listbox" aria-label="Подходящие объекты" className="content-panel" style={{position:'absolute',top:'100%',left:0,right:0,zIndex:1200}}>
      {loading?<p role="status">Ищем объекты…</p>:error?<p role="alert">Поиск временно недоступен.</p>:items.length?items.map((item,index)=><button type="button" role="option" id={`suggestion-${item.id}`} aria-selected={index===selected} key={item.id} onMouseDown={e=>e.preventDefault()} onClick={()=>choose(item)}>{item.name}</button>):<p>Подходящих объектов нет.</p>}
    </div>}
  </div>
}
