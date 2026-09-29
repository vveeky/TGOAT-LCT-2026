import { useContext } from 'react'

import ReplayRuntimeContext from './ReplayRuntimeContext'

export function useReplayRuntime() {
  return useContext(ReplayRuntimeContext)
}
