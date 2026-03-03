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
const TOKEN_KEY = 'hypertube_token'

function parseJwtPayload(token: string): { user_id?: string; username?: string } {
  try {
    const payload = token.split('.')[1]
    const decoded = atob(payload.replace(/-/g, '+').replace(/_/g, '/'))
    return JSON.parse(decoded)
  } catch {
    return {}
  }
}

function readStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => readStoredToken())
  const payload = token ? parseJwtPayload(token) : {}
  const userId = payload.user_id ?? null
  const username = payload.username ?? null
  const navigate = useNavigate()

  function login(t: string) {
    try {
      localStorage.setItem(TOKEN_KEY, t)
    } catch {
      // ignore
    }
    setToken(t)
  }

  function logout() {
    try {
      localStorage.removeItem(TOKEN_KEY)
    } catch {
      // ignore
    }
    setToken(null)
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
