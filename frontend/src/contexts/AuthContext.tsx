import { createContext, useContext, useState, ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'

interface AuthContextType {
  token: string | null
  userId: string | null
  username: string | null
  login: (token: string) => void
  logout: () => void
}

const AuthContext = createContext<AuthContextType | null>(null)

function parseJwtPayload(token: string): { user_id?: string; username?: string } {
  try {
    const payload = token.split('.')[1]
    const decoded = atob(payload.replace(/-/g, '+').replace(/_/g, '/'))
    return JSON.parse(decoded)
  } catch {
    return {}
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(null)
  const [userId, setUserId] = useState<string | null>(null)
  const [username, setUsername] = useState<string | null>(null)
  const navigate = useNavigate()

  function login(t: string) {
    const payload = parseJwtPayload(t)
    setToken(t)
    setUserId(payload.user_id ?? null)
    setUsername(payload.username ?? null)
  }

  function logout() {
    setToken(null)
    setUserId(null)
    setUsername(null)
    navigate('/login')
  }

  return (
    <AuthContext.Provider value={{ token, userId, username, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
