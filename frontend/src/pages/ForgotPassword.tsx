import { useState, FormEvent } from 'react'

export default function ForgotPassword() {
  const [message, setMessage] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setLoading(true)
    setMessage('')

    const email = (e.currentTarget.elements.namedItem('email') as HTMLInputElement).value

    try {
      await fetch('/api/auth/forgot-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      })
      setMessage('If your email is registered, you will receive a reset link shortly.')
    } catch {
      setMessage('Network error. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main style={{ maxWidth: 400, margin: '40px auto', fontFamily: 'sans-serif' }}>
      <h1>Forgot Password</h1>
      <p>Enter your email address and we'll send you a reset link.</p>
      {message && <p style={{ color: 'green' }}>{message}</p>}
      <form onSubmit={handleSubmit}>
        <div style={{ marginBottom: 12 }}>
          <label htmlFor="email">Email</label>
          <input id="email" name="email" type="email" required style={{ display: 'block' }} />
        </div>
        <button type="submit" disabled={loading}>
          {loading ? 'Sending…' : 'Send reset link'}
        </button>
      </form>
      <p style={{ marginTop: 16 }}>
        <a href="/login">Back to Login</a>
      </p>
    </main>
  )
}
