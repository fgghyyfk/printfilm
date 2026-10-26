import { useEffect, useState } from 'react'
import { request } from './api'

export function useCategories() {
  const [names, setNames] = useState<string[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    let active = true
    request<{ names: string[] }>('/categories').then(value => { if (active) setNames(value.names) })
      .catch(e => { if (active) setError(`分类加载失败：${e.message}，请刷新重试。`) })
    return () => { active = false }
  }, [])
  return { names, error }
}
