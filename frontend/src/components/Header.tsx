import { useAuth } from '../contexts/AuthContext'

export function Header() {
  const { token, logout } = useAuth()

  if (!token) return null

  return (
    <header
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: '10px 20px',
        backgroundColor: '#1a1a2e',
        color: 'white',
      }}
    >
      <a href="/" style={{ color: 'white', textDecoration: 'none', fontSize: 20 }}>
        Hypertube
      </a>
      <button
        onClick={logout}
        style={{
          color: 'white',
          background: 'transparent',
          border: '1px solid white',
          padding: '6px 14px',
          cursor: 'pointer',
          borderRadius: 4,
        }}
      >
        Logout
      </button>
    </header>
  )
}
