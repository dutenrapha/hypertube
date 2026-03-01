import { useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../contexts/AuthContext'

interface PublicUser {
  id: string
  username: string
  first_name: string
  last_name: string
  profile_picture_url: string | null
  preferred_language: string
}

export default function UserProfile() {
  const { id } = useParams<{ id: string }>()
  const { token } = useAuth()
  const { t } = useTranslation()
  const [user, setUser] = useState<PublicUser | null>(null)
  const [notFound, setNotFound] = useState(false)

  useEffect(() => {
    if (!id || !token) return
    fetch(`/api/users/${id}`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: 'no-store',
    }).then(async (r) => {
      if (r.status === 404) {
        setNotFound(true)
        return
      }
      setUser(await r.json())
    })
  }, [id, token])

  if (notFound) {
    return (
      <main style={{ maxWidth: 500, margin: '40px auto', fontFamily: 'sans-serif' }}>
        <p>{t('user_profile.not_found')}</p>
      </main>
    )
  }

  if (!user) return <p style={{ padding: 20 }}>Loading…</p>

  return (
    <main style={{ maxWidth: 500, margin: '40px auto', fontFamily: 'sans-serif' }}>
      <h1>{t('user_profile.title')}</h1>

      {user.profile_picture_url && (
        <img
          src={user.profile_picture_url}
          alt="profile"
          style={{ width: 80, height: 80, borderRadius: '50%', objectFit: 'cover', marginBottom: 16 }}
        />
      )}

      <p><strong>{t('user_profile.username')}:</strong> {user.username}</p>
      <p><strong>{t('user_profile.first_name')}:</strong> {user.first_name}</p>
      <p><strong>{t('user_profile.last_name')}:</strong> {user.last_name}</p>
      <p><strong>{t('user_profile.language')}:</strong> {user.preferred_language}</p>
    </main>
  )
}
