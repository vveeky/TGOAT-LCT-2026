import { useContext } from 'react'

import UnreadNotificationCountContext from './UnreadNotificationCountContext'

export function useUnreadNotificationCount() {
  return useContext(UnreadNotificationCountContext)
}
