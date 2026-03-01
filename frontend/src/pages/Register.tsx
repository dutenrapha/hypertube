import { useState, FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'

type FieldErrors = Record<string, string>

export default function Register() {
  const navigate = useNavigate()
  const [errors, setErrors] = useState<FieldErrors>({})
  const [globalError, setGlobalError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    setErrors({})
    setGlobalError('')
    setLoading(true)

    const form = e.currentTarget
    const data = new FormData(form)

    try {
      const resp = await fetch('/api/auth/register', {
        method: 'POST',
        body: data,
      })

      const body = await resp.json()

      if (resp.status === 201) {
        navigate('/login')
        return
      }

      if (resp.status === 422 && body.fields) {
        setErrors(body.fields)
      } else if (resp.status === 409) {
        const field = body.field ?? 'email'
        setErrors({ [field]: body.message })
      } else {
        setGlobalError(body.message ?? 'Registration failed. Please try again.')
      }
    } catch {
      setGlobalError('Network error. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  const field = (
    name: string,
    label: string,
    type = 'text',
    extra?: React.InputHTMLAttributes<HTMLInputElement>,
  ) => (
    <div style={{ marginBottom: 12 }}>
      <label htmlFor={name}>{label}</label>
      <input id={name} name={name} type={type} {...extra} style={{ display: 'block' }} />
      {errors[name] && <span style={{ color: 'red', fontSize: 12 }}>{errors[name]}</span>}
    </div>
  )

  return (
    <main style={{ maxWidth: 400, margin: '40px auto', fontFamily: 'sans-serif' }}>
      <h1>Create account</h1>
      {globalError && <p style={{ color: 'red' }}>{globalError}</p>}
      <form onSubmit={handleSubmit} encType="multipart/form-data">
        {field('email', 'Email', 'email')}
        {field('username', 'Username')}
        {field('first_name', 'First name')}
        {field('last_name', 'Last name')}
        {field('password', 'Password', 'password')}
        <div style={{ marginBottom: 12 }}>
          <label htmlFor="profile_picture">Profile picture (optional)</label>
          <input id="profile_picture" name="profile_picture" type="file"
            accept="image/jpeg,image/png,image/webp" style={{ display: 'block' }} />
          {errors['profile_picture'] && (
            <span style={{ color: 'red', fontSize: 12 }}>{errors['profile_picture']}</span>
          )}
        </div>
        <button type="submit" disabled={loading}>
          {loading ? 'Registering…' : 'Register'}
        </button>
      </form>
      <p>Already have an account? <a href="/login">Login</a></p>
    </main>
  )
}
