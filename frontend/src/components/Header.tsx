import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../contexts/AuthContext'

export function Header() {
  const { token, logout } = useAuth()
  const { t } = useTranslation()
  const navigate = useNavigate()

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
      <a
        href="/"
        style={{ color: 'white', textDecoration: 'none', fontSize: 20 }}
      >
        {t('nav.hypertube')}
      </a>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
        <button
          onClick={() => navigate('/')}
          style={{
            color: 'white',
            background: 'transparent',
            border: '1px solid white',
            padding: '6px 14px',
            cursor: 'pointer',
            borderRadius: 4,
          }}
        >
          {t('nav.library')}
        </button>
        <button
          onClick={() => navigate('/profile')}
          style={{
            color: 'white',
            background: 'transparent',
            border: '1px solid white',
            padding: '6px 14px',
            cursor: 'pointer',
            borderRadius: 4,
          }}
        >
          {t('nav.profile')}
        </button>
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
          {t('nav.logout')}
        </button>
      </div>
    </header>
  )
}
