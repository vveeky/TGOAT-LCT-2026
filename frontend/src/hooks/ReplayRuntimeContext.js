import { createContext } from 'react'

const ReplayRuntimeContext = createContext({
  status: null,
  loading: true,
  error: null,
  refresh: async () => null,
})

export default ReplayRuntimeContext
