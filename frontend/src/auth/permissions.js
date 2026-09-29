const DISPATCHER_ROLES = new Set(['district_dispatcher', 'central_dispatcher'])
const OPERATIONAL_ROLES = new Set(['technician', ...DISPATCHER_ROLES])
const ROLE_CAPABILITIES = {
  central_dispatcher: new Set([
    'objects.read', 'management.read', 'evidence.read', 'signals.read', 'signals.decide',
    'requests.read', 'requests.create', 'requests.edit', 'requests.assign', 'requests.dispatch',
    'prediction.run', 'prediction.close', 'prediction.review', 'prediction.outcome',
    'notifications.read', 'notifications.update', 'audit.read', 'replay.read', 'replay.control',
  ]),
  district_dispatcher: new Set([
    'objects.read', 'management.read', 'evidence.read', 'signals.read', 'signals.decide',
    'requests.read', 'requests.create', 'requests.edit', 'requests.assign', 'requests.dispatch',
    'prediction.run', 'prediction.close', 'prediction.review', 'prediction.outcome',
    'notifications.read', 'notifications.update', 'audit.read', 'replay.read', 'replay.control',
  ]),
  technician: new Set([
    'objects.read', 'evidence.read', 'requests.read', 'requests.create', 'tasks.execute',
    'prediction.review', 'notifications.read', 'notifications.update', 'replay.read',
  ]),
  system_administrator: new Set(['access.manage', 'audit.read']),
}

function rolesOf(userOrRole) {
  if (typeof userOrRole === 'string') return [userOrRole]
  if (!userOrRole) return []
  return [...new Set([...(userOrRole.roles ?? []), ...(userOrRole.role ? [userOrRole.role] : [])])]
}

export function canReviewOperationalPrediction(userOrRole) {
  return rolesOf(userOrRole).some((role) => OPERATIONAL_ROLES.has(role))
}

export function canRecordOperationalOutcome(userOrRole) {
  return rolesOf(userOrRole).some((role) => DISPATCHER_ROLES.has(role))
}

export function hasRole(user, role) {
  return rolesOf(user).includes(role)
}

export function hasCapability(user, capability) {
  if (Array.isArray(user?.grants)) return user.grants.includes(capability)
  return rolesOf(user).some((role) => ROLE_CAPABILITIES[role]?.has(capability))
}

export function isDispatcher(user) {
  return hasCapability(user, 'signals.read')
}
