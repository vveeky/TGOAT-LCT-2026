import { apiFetch } from './client'

export function getObjectCatalog(){return apiFetch('/objects/catalog')}

export function getObjects(params = {}) {
  const query = new URLSearchParams(
    Object.entries(params).filter(([, value]) => value !== undefined && value !== '')
  )
  const suffix = query.toString() ? `?${query.toString()}` : ''
  return apiFetch(`/objects${suffix}`)
}

export function getObject(objectId) {
  return apiFetch(`/objects/${objectId}`)
}

export function getObjectChannels(objectId) {
  return apiFetch(`/objects/${objectId}/channels`)
}

export function getObjectPlannedWork(objectId, asOf) {
  const query = new URLSearchParams(asOf ? { as_of: asOf } : {})
  return apiFetch(`/objects/${objectId}/planned-work${query.size ? `?${query}` : ''}`)
}
