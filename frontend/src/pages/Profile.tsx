import { useState, useEffect, FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import i18n from '../i18n'
import { useAuth } from '../contexts/AuthContext'

interface UserData {
  id: string
  email?: string
  username: string
  first_name: string
  last_name: string
  profile_picture_url: string | null
  preferred_language: string
}

export default function Profile() {
  const { token, userId } = useAuth()
  const { t } = useTranslation()

  const [user, setUser] = useState<UserData | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  // Form fields (only send non-empty ones)
  const [email, setEmail] = useState('')
  const [username, setUsername] = useState('')
  const [firstName, setFirstName] = useState('')
  const [lastName, setLastName] = useState('')
  const [password, setPassword] = useState('')
  const [language, setLanguage] = useState('en')
  const [picFile, setPicFile] = useState<File | null>(null)

  useEffect(() => {
    if (!userId || !token) return
    setLoading(true)
    fetch(`/api/users/${userId}`, {
      headers: { Authorization: `Bearer ${token}` },
      cache: 'no-store',
    })
      .then((r) => r.json())
      .then((data: UserData) => {
        setUser(data)
        if (data.email != null) setEmail(data.email)
        setUsername(data.username)
        setFirstName(data.first_name)
        setLastName(data.last_name)
        setLanguage(data.preferred_language)
        i18n.changeLanguage(data.preferred_language)
      })
      .finally(() => setLoading(false))
  }, [userId, token])

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!userId?.trim()) {
      setError('Not logged in')
      return
    }
    setSaving(true)
    setError('')
    setMessage('')

    const form = new FormData()
    if (email) form.append('email', email)
    if (username) form.append('username', username)
    if (firstName) form.append('first_name', firstName)
    if (lastName) form.append('last_name', lastName)
    if (password) form.append('password', password)
    form.append('preferred_language', language)
    if (picFile) form.append('profile_picture', picFile)

    try {
      const resp = await fetch(`/api/users/${userId}`, {
        method: 'PATCH',
        headers: { Authorization: `Bearer ${token}` },
        body: form,
      })

      if (resp.ok) {
        const updated: UserData = await resp.json()
        setUser(updated)
        setPassword('')
        setPicFile(null)
        i18n.changeLanguage(updated.preferred_language)
        setMessage(t('profile.saved'))
      } else {
        const data = await resp.json()
        setError(data.message || JSON.stringify(data.fields || data))
      }
    } catch {
      setError('Network error')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <p style={{ padding: 20 }}>Loading…</p>
  if (!user) return <p style={{ padding: 20 }}>Not found.</p>

  return (
    <main style={{ maxWidth: 500, margin: '40px auto', fontFamily: 'sans-serif' }}>
      <h1>{t('profile.title')}</h1>
      {message && <p style={{ color: 'green' }}>{message}</p>}
      {error && <p style={{ color: 'red' }}>{error}</p>}

      {user.profile_picture_url && (
        <div style={{ marginBottom: 16 }}>
          <p>{t('profile.current_picture')}:</p>
          <img
            src={user.profile_picture_url}
            alt="profile"
            style={{ width: 80, height: 80, borderRadius: '50%', objectFit: 'cover' }}
          />
        </div>
      )}

      <form onSubmit={handleSubmit} method="post" action="#" noValidate>
        <div style={{ marginBottom: 10 }}>
          <label>{t('profile.email')}</label>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            style={{ display: 'block', width: '100%' }}
          />
        </div>
        <div style={{ marginBottom: 10 }}>
          <label>{t('profile.username')}</label>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            style={{ display: 'block', width: '100%' }}
          />
        </div>
        <div style={{ marginBottom: 10 }}>
          <label>{t('profile.first_name')}</label>
          <input
            type="text"
            value={firstName}
            onChange={(e) => setFirstName(e.target.value)}
            style={{ display: 'block', width: '100%' }}
          />
        </div>
        <div style={{ marginBottom: 10 }}>
          <label>{t('profile.last_name')}</label>
          <input
            type="text"
            value={lastName}
            onChange={(e) => setLastName(e.target.value)}
            style={{ display: 'block', width: '100%' }}
          />
        </div>
        <div style={{ marginBottom: 10 }}>
          <label>{t('profile.new_password')}</label>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            style={{ display: 'block', width: '100%' }}
          />
        </div>
        <div style={{ marginBottom: 10 }}>
          <label>{t('profile.language')}</label>
          <select
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            style={{ display: 'block' }}
          >
            <option value="en">{t('profile.lang_en')}</option>
            <option value="pt">{t('profile.lang_pt')}</option>
          </select>
        </div>
        <div style={{ marginBottom: 16 }}>
          <label>{t('profile.profile_picture')}</label>
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp"
            onChange={(e) => setPicFile(e.target.files?.[0] ?? null)}
            style={{ display: 'block' }}
          />
        </div>
        <button type="submit" disabled={saving}>
          {saving ? t('profile.saving') : t('profile.save')}
        </button>
      </form>
    </main>
  )
}
