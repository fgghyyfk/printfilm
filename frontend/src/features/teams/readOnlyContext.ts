import { createContext, useContext } from 'react'

/** 查看和编辑复用同一页面；只读状态仅限制写操作。 */
export const TeamReadOnlyContext = createContext(false)
export function useTeamReadOnly(): boolean { return useContext(TeamReadOnlyContext) }
