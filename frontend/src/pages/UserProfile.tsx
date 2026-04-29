import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../contexts/AuthContext'

// Public profile shape: email is intentionally NOT part of this type.
// The backend GET /api/users/:id already excludes it; we also never render
// it here, so even if a future bug leaked it, it would not appear in the UI.
interface PublicUser {
  id: string
  username: string
  first_name: string
  last_name: string
  profile_picture_url: string | null
  preferred_language: string
}

type LoadState = 'loading' | 'ok' | 'not_found' | 'error'

export default function UserProfile() {
  const { id } = useParams<{ id: string }>()
  const { token } = useAuth()
  const { t } = useTranslation()
  const navigate = useNavigate()

  const [user, setUser] = useState<PublicUser | null>(null)
  const [state, setState] = useState<LoadState>('loading')

  useEffect(() => {
    if (!token) {
      navigate('/login')
      return
    }
    if (!id) return

    setState('loading')
    fetch(`/api/users/${encodeURIComponent(id)}`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: 'no-store',
    })
      .then(async (r) => {
        if (r.status === 404) {
          setState('not_found')
          return
        }
        if (!r.ok) {
          setState('error')
          return
        }
        const data = (await r.json()) as PublicUser & { email?: string }
        // Defense in depth: never trust the server payload to be email-free.
        // Strip it before storing in component state.
        // eslint-disable-next-line @typescript-eslint/no-unused-vars
        const { email: _ignored, ...publicOnly } = data
        setUser(publicOnly)
        setState('ok')
      })
      .catch(() => setState('error'))
  }, [id, token, navigate])

  return (
    <div
      style={{
        minHeight: '100vh',
        backgroundColor: '#0f0f1a',
        color: 'white',
      }}
    >
      <div style={{ padding: '16px 20px' }}>
        <button
          onClick={() => navigate(-1)}
          style={backBtnStyle}
          data-testid="user-profile-back"
        >
          ← {t('user_profile.back', 'Back')}
        </button>
      </div>

      <main
        style={{
          maxWidth: 560,
          margin: '0 auto',
          padding: '24px 20px',
        }}
        data-testid="user-profile-page"
      >
        <h1 style={{ margin: '0 0 24px', fontSize: 24 }}>
          {t('user_profile.title')}
        </h1>

        {state === 'loading' && (
          <p style={{ color: '#aaa' }}>{t('movie_details.loading')}</p>
        )}

        {state === 'not_found' && (
          <p style={{ color: '#aaa' }} data-testid="user-profile-not-found">
            {t('user_profile.not_found')}
          </p>
        )}

        {state === 'error' && (
          <p style={{ color: '#ff8c7c' }}>
            {t('search.error_loading')}
          </p>
        )}

        {state === 'ok' && user && (
          <section
            style={{
              backgroundColor: '#1a1a2e',
              borderRadius: 8,
              padding: 24,
              border: '1px solid #2a2a3e',
            }}
          >
            <div
              style={{
                display: 'flex',
                gap: 20,
                alignItems: 'center',
                marginBottom: 24,
                flexWrap: 'wrap',
              }}
            >
              <img
                src={user.profile_picture_url ?? '/uploads/default-avatar.svg'}
                alt={user.username}
                onError={(e) => {
                  ;(e.target as HTMLImageElement).style.visibility = 'hidden'
                }}
                style={{
                  width: 96,
                  height: 96,
                  borderRadius: '50%',
                  objectFit: 'cover',
                  backgroundColor: '#2a2a3e',
                  flexShrink: 0,
                }}
                data-testid="user-profile-picture"
              />
              <div>
                <h2
                  style={{ margin: '0 0 4px', fontSize: 22 }}
                  data-testid="user-profile-fullname"
                >
                  {user.first_name} {user.last_name}
                </h2>
                <p
                  style={{ margin: 0, color: '#aaa', fontSize: 14 }}
                  data-testid="user-profile-username"
                >
                  @{user.username}
                </p>
              </div>
            </div>

            <dl
              style={{
                display: 'grid',
                gridTemplateColumns: '160px 1fr',
                gap: '8px 16px',
                margin: 0,
                fontSize: 14,
              }}
            >
              <dt style={{ color: '#888' }}>{t('user_profile.username')}</dt>
              <dd style={{ margin: 0 }}>{user.username}</dd>

              <dt style={{ color: '#888' }}>{t('user_profile.first_name')}</dt>
              <dd style={{ margin: 0 }}>{user.first_name}</dd>

              <dt style={{ color: '#888' }}>{t('user_profile.last_name')}</dt>
              <dd style={{ margin: 0 }}>{user.last_name}</dd>

              <dt style={{ color: '#888' }}>{t('user_profile.language')}</dt>
              <dd style={{ margin: 0 }}>
                {user.preferred_language === 'pt'
                  ? t('profile.lang_pt')
                  : t('profile.lang_en')}
              </dd>
            </dl>
          </section>
        )}
      </main>
    </div>
  )
}

const backBtnStyle: React.CSSProperties = {
  padding: '8px 14px',
  borderRadius: 6,
  border: '1px solid #444',
  backgroundColor: '#1a1a2e',
  color: 'white',
  cursor: 'pointer',
  fontSize: 14,
}
