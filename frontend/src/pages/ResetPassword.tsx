import { useState, FormEvent } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'

export default function ResetPassword() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const token = searchParams.get('token') || ''

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setError('')
    setLoading(true)

    const form = e.currentTarget
    const newPassword = (form.elements.namedItem('new_password') as HTMLInputElement).value
    const confirmPassword = (form.elements.namedItem('confirm_password') as HTMLInputElement).value

    if (newPassword !== confirmPassword) {
      setError('Passwords do not match')
      setLoading(false)
      return
    }

    try {
      const resp = await fetch('/api/auth/reset-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, new_password: newPassword }),
      })

      if (resp.ok) {
        navigate('/login')
        return
      }

      const data = await resp.json()
      setError(data.message || 'Failed to reset password')
    } catch {
      setError('Network error. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  if (!token) {
    return (
      <main style={{ maxWidth: 400, margin: '40px auto', fontFamily: 'sans-serif' }}>
        <h1>Reset Password</h1>
        <p style={{ color: 'red' }}>Invalid or missing reset token.</p>
        <a href="/forgot-password">Request a new reset link</a>
      </main>
    )
  }

  return (
    <main style={{ maxWidth: 400, margin: '40px auto', fontFamily: 'sans-serif' }}>
      <h1>Reset Password</h1>
      {error && <p style={{ color: 'red' }}>{error}</p>}
      <form onSubmit={handleSubmit}>
        <div style={{ marginBottom: 12 }}>
          <label htmlFor="new_password">New password</label>
          <input id="new_password" name="new_password" type="password" required style={{ display: 'block' }} />
        </div>
        <div style={{ marginBottom: 12 }}>
          <label htmlFor="confirm_password">Confirm new password</label>
          <input id="confirm_password" name="confirm_password" type="password" required style={{ display: 'block' }} />
        </div>
        <button type="submit" disabled={loading}>
          {loading ? 'Resetting…' : 'Reset password'}
        </button>
      </form>
    </main>
  )
}
