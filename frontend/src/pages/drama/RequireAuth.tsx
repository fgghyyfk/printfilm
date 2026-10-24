/** Require login for drama routes; redirect to /auth when missing token. */
import { Navigate, useLocation } from 'react-router-dom'
import type { ReactNode } from 'react'

export default function RequireAuth({ children }: { children: ReactNode }) {
  const location = useLocation()
  const token = localStorage.getItem('token')
  if (!token) {
    return <Navigate to={`/auth?next=${encodeURIComponent(location.pathname + location.search)}`} replace state={{ from: location.pathname + location.search }} />
  }
  return children
}
