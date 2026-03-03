import { useState, useEffect } from 'react'
import { useParams, useLocation, useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../contexts/AuthContext'

interface MovieDetail {
  id: string
  title: string
  year: number | null
  imdb_rating: string | null
  genre: string | null
  summary: string | null
  director: string | null
  cast: string[]
  cover_url: string | null
  length_minutes: number | null
  available_subtitles: string[]
  video_url: string | null
  comments_count: number
}

interface Comment {
  id: string
  content: string
  created_at: string | null
  username: string
  profile_picture_url: string | null
}

export default function MovieDetails() {
  const { id } = useParams<{ id: string }>()
  const location = useLocation()
  const navigate = useNavigate()
  const { token } = useAuth()
  const { t } = useTranslation()

  const [movie, setMovie] = useState<MovieDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [comments, setComments] = useState<Comment[]>([])
  const [commentText, setCommentText] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // State passed from Search (title, year, cover_url)
  const passedState = (location.state as { title?: string; year?: string; cover_url?: string } | null) ?? {}

  useEffect(() => {
    if (!token) {
      navigate('/login')
      return
    }
    if (!id) return

    const params = new URLSearchParams()
    if (passedState.title)     params.set('title',     passedState.title)
    if (passedState.year)      params.set('year',      passedState.year)
    if (passedState.cover_url) params.set('cover_url', passedState.cover_url)

    const url = `/api/movies/${encodeURIComponent(id)}${params.size ? '?' + params.toString() : ''}`

    fetch(url, { headers: { Authorization: `Bearer ${token}` } })
      .then(r => r.json())
      .then((data: MovieDetail) => setMovie(data))
      .catch(() => {})
      .finally(() => setLoading(false))

    fetch(`/api/movies/${encodeURIComponent(id)}/comments`, {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(r => r.json())
      .then((data: { comments: Comment[] }) => setComments(data.comments ?? []))
      .catch(() => {})
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, token])

  const handleCommentSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!commentText.trim() || !token || !id) return
    setSubmitting(true)
    try {
      const resp = await fetch(`/api/movies/${encodeURIComponent(id)}/comments`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ content: commentText.trim() }),
      })
      if (resp.ok) {
        setCommentText('')
        // Refresh comments
        const updated = await fetch(`/api/movies/${encodeURIComponent(id)}/comments`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        const data = await updated.json()
        setComments(data.comments ?? [])
        if (movie) setMovie({ ...movie, comments_count: movie.comments_count + 1 })
      }
    } catch {
      // ignore
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) {
    return (
      <div style={{ minHeight: '100vh', backgroundColor: '#0f0f1a', color: 'white', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <p>{t('movie_details.loading')}</p>
      </div>
    )
  }

  if (!movie) {
    return (
      <div style={{ minHeight: '100vh', backgroundColor: '#0f0f1a', color: 'white', padding: 40 }}>
        <button onClick={() => navigate(-1)} style={backBtnStyle}>{t('movie_details.back')}</button>
        <p style={{ color: '#aaa', marginTop: 20 }}>{t('movie_details.not_found')}</p>
      </div>
    )
  }

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0f0f1a', color: 'white' }}>
      {/* Back button */}
      <div style={{ padding: '16px 20px' }}>
        <button onClick={() => navigate(-1)} style={backBtnStyle}>
          ← {t('movie_details.back')}
        </button>
      </div>

      {/* Hero section */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 24,
          padding: '0 20px 24px',
          borderBottom: '1px solid #333',
        }}
      >
        {movie.cover_url && (
          <img
            src={movie.cover_url}
            alt={movie.title}
            style={{
              width: 200,
              aspectRatio: '2/3',
              objectFit: 'cover',
              borderRadius: 8,
              flexShrink: 0,
              backgroundColor: '#2a2a3e',
            }}
            onError={e => { (e.target as HTMLImageElement).style.display = 'none' }}
          />
        )}
        <div style={{ flex: '1 1 260px' }}>
          <h1 style={{ margin: '0 0 8px', fontSize: 28 }}>{movie.title}</h1>
          <p style={{ margin: '0 0 4px', color: '#aaa', fontSize: 14 }}>
            {movie.year ?? '—'}
            {movie.imdb_rating && ` · ★ ${movie.imdb_rating}`}
            {movie.length_minutes && ` · ${movie.length_minutes} min`}
          </p>
          {movie.genre && (
            <p style={{ margin: '0 0 12px', color: '#888', fontSize: 13 }}>{movie.genre}</p>
          )}
          {movie.director && (
            <p style={{ margin: '0 0 4px', fontSize: 13 }}>
              <strong>{t('movie_details.director')}:</strong> {movie.director}
            </p>
          )}
          {movie.cast.length > 0 && (
            <p style={{ margin: '0 0 12px', fontSize: 13 }}>
              <strong>{t('movie_details.cast')}:</strong> {movie.cast.join(', ')}
            </p>
          )}
          {movie.summary && (
            <p style={{ margin: 0, fontSize: 14, lineHeight: 1.6, color: '#ddd', maxWidth: 600 }}>
              {movie.summary}
            </p>
          )}
        </div>
      </div>

      {/* Video player */}
      {movie.video_url && (
        <div style={{ padding: '24px 20px' }}>
          <h2 style={{ margin: '0 0 12px', fontSize: 18 }}>{t('movie_details.watch')}</h2>
          <video
            controls
            style={{ width: '100%', maxWidth: 900, borderRadius: 8, backgroundColor: '#000' }}
            crossOrigin="anonymous"
          >
            <source src={movie.video_url} />
            {movie.available_subtitles.map((sub, i) => (
              <track key={i} kind="subtitles" src={sub} />
            ))}
            {t('movie_details.video_not_supported')}
          </video>
        </div>
      )}

      {/* Subtitles list (if no video player shown) */}
      {!movie.video_url && movie.available_subtitles.length > 0 && (
        <div style={{ padding: '24px 20px' }}>
          <h3 style={{ margin: '0 0 8px', fontSize: 16 }}>{t('movie_details.subtitles')}</h3>
          <ul style={{ margin: 0, paddingLeft: 20, color: '#aaa', fontSize: 13 }}>
            {movie.available_subtitles.map((sub, i) => (
              <li key={i}>
                <a href={sub} target="_blank" rel="noopener noreferrer" style={{ color: '#7c8cff' }}>
                  {sub.split('/').pop()}
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Comments section */}
      <div style={{ padding: '24px 20px', maxWidth: 760 }}>
        <h2 style={{ margin: '0 0 16px', fontSize: 18 }}>
          {t('movie_details.comments')} ({movie.comments_count})
        </h2>

        {/* Comment form */}
        <form onSubmit={handleCommentSubmit} style={{ marginBottom: 24 }}>
          <textarea
            value={commentText}
            onChange={e => setCommentText(e.target.value)}
            placeholder={t('movie_details.comment_placeholder')}
            rows={3}
            style={{
              width: '100%',
              padding: '10px 12px',
              borderRadius: 6,
              border: '1px solid #444',
              backgroundColor: '#1e1e2e',
              color: 'white',
              fontSize: 14,
              resize: 'vertical',
              boxSizing: 'border-box',
            }}
          />
          <button
            type="submit"
            disabled={submitting || !commentText.trim()}
            data-testid="comment-submit"
            style={{
              marginTop: 8,
              padding: '8px 16px',
              borderRadius: 6,
              border: '1px solid #444',
              backgroundColor: submitting ? '#555' : '#333',
              color: 'white',
              cursor: submitting ? 'not-allowed' : 'pointer',
            }}
          >
            {submitting ? t('movie_details.submitting') : t('movie_details.submit_comment')}
          </button>
        </form>

        {/* Comments list */}
        {comments.map(c => (
          <div
            key={c.id}
            style={{
              marginBottom: 16,
              padding: '12px 16px',
              backgroundColor: '#1a1a2e',
              borderRadius: 8,
              borderLeft: '3px solid #444',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
              {c.profile_picture_url && (
                <img
                  src={c.profile_picture_url}
                  alt={c.username}
                  style={{ width: 28, height: 28, borderRadius: '50%', objectFit: 'cover' }}
                />
              )}
              <strong style={{ fontSize: 13 }}>{c.username}</strong>
              {c.created_at && (
                <span style={{ fontSize: 11, color: '#888' }}>
                  {new Date(c.created_at).toLocaleDateString()}
                </span>
              )}
            </div>
            <p style={{ margin: 0, fontSize: 14, color: '#ddd' }}>{c.content}</p>
          </div>
        ))}

        {comments.length === 0 && (
          <p style={{ color: '#888', fontSize: 14 }}>{t('movie_details.no_comments')}</p>
        )}
      </div>
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
