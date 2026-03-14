import { useState, useEffect, useRef } from 'react'
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
  torrent_magnet: string | null
  comments_count: number
}

interface Comment {
  id: string
  content: string
  created_at: string | null
  username: string
  profile_picture_url: string | null
}

type StreamStatus = 'not_started' | 'downloading' | 'converting' | 'ready'

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

  // Streaming state
  const [streamStatus, setStreamStatus] = useState<StreamStatus>('not_started')
  const [streamProgress, setStreamProgress] = useState(0)
  const [streamStarting, setStreamStarting] = useState(false)
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // State passed from Search
  const passedState = (location.state as {
    title?: string
    year?: string
    cover_url?: string
    magnet?: string | null
  } | null) ?? {}

  // Get the magnet from navigation state or movie details
  const magnet = passedState.magnet ?? movie?.torrent_magnet ?? null

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

  // Stop polling when component unmounts
  useEffect(() => {
    return () => {
      if (pollIntervalRef.current) clearInterval(pollIntervalRef.current)
    }
  }, [])

  const pollStatus = () => {
    if (!id || !token) return
    if (pollIntervalRef.current) clearInterval(pollIntervalRef.current)

    pollIntervalRef.current = setInterval(async () => {
      try {
        const resp = await fetch(`/api/movies/${encodeURIComponent(id)}/status`, {
          headers: { Authorization: `Bearer ${token}` },
        })
        const data = await resp.json()
        setStreamProgress(data.progress ?? 0)
        if (data.status === 'ready') {
          setStreamStatus('ready')
          if (pollIntervalRef.current) {
            clearInterval(pollIntervalRef.current)
            pollIntervalRef.current = null
          }
        } else if (data.status === 'converting') {
          setStreamStatus('converting')
        } else if (data.status === 'not_started') {
          setStreamStatus('not_started')
          if (pollIntervalRef.current) {
            clearInterval(pollIntervalRef.current)
            pollIntervalRef.current = null
          }
        }
      } catch {
        // ignore
      }
    }, 2000)
  }

  const handleWatch = async () => {
    if (!id || !token || streamStarting) return
    const magnetToUse = magnet
    if (!magnetToUse) {
      alert('No magnet link available for this movie.')
      return
    }

    setStreamStarting(true)
    try {
      const resp = await fetch(`/api/movies/${encodeURIComponent(id)}/stream`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ magnet: magnetToUse }),
      })

      if (resp.ok) {
        const data = await resp.json()
        if (data.status === 'ready') {
          setStreamStatus('ready')
        } else if (data.status === 'converting') {
          setStreamStatus('converting')
          pollStatus()
        } else {
          setStreamStatus('downloading')
          pollStatus()
        }
      }
    } catch {
      // ignore
    } finally {
      setStreamStarting(false)
    }
  }

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

  const streamUrl = `/api/movies/${encodeURIComponent(id ?? '')}/stream`

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

      {/* Streaming section */}
      <div style={{ padding: '24px 20px' }}>
        <h2 style={{ margin: '0 0 16px', fontSize: 18 }}>{t('movie_details.watch')}</h2>

        {/* Video player — shown when ready */}
        {streamStatus === 'ready' && (
          <video
            controls
            autoPlay
            data-testid="stream-player"
            style={{ width: '100%', maxWidth: 900, borderRadius: 8, backgroundColor: '#000', display: 'block', marginBottom: 16 }}
            crossOrigin="anonymous"
          >
            <source
              src={`${streamUrl}?t=${Date.now()}`}
              type={
                movie.torrent_magnet?.includes('.mkv') ? 'video/x-matroska' : 'video/mp4'
              }
            />
            {movie.available_subtitles.map((sub, i) => (
              <track key={i} kind="subtitles" src={sub} />
            ))}
            {t('movie_details.video_not_supported')}
          </video>
        )}

        {/* Converting indicator — shown while FFmpeg converts MKV→MP4 */}
        {streamStatus === 'converting' && (
          <div style={{ marginBottom: 16 }}>
            <p style={{ margin: '0 0 8px', color: '#aaa', fontSize: 14 }}>
              {t('movie_details.converting')}
            </p>
            <div style={{ width: '100%', maxWidth: 500, height: 8, backgroundColor: '#333', borderRadius: 4, overflow: 'hidden' }}>
              <div
                data-testid="convert-progress"
                style={{
                  height: '100%',
                  width: '100%',
                  background: 'linear-gradient(90deg, #7c8cff 0%, #ff8c7c 100%)',
                  borderRadius: 4,
                  animation: 'pulse 1.5s ease-in-out infinite',
                }}
              />
            </div>
          </div>
        )}

        {/* Progress bar — shown while downloading */}
        {streamStatus === 'downloading' && (
          <div style={{ marginBottom: 16 }}>
            <p style={{ margin: '0 0 8px', color: '#aaa', fontSize: 14 }}>
              {t('movie_details.downloading')} {streamProgress}%
            </p>
            <div style={{ width: '100%', maxWidth: 500, height: 8, backgroundColor: '#333', borderRadius: 4, overflow: 'hidden' }}>
              <div
                data-testid="stream-progress"
                style={{
                  height: '100%',
                  width: `${streamProgress}%`,
                  backgroundColor: '#7c8cff',
                  borderRadius: 4,
                  transition: 'width 0.5s ease',
                }}
              />
            </div>
          </div>
        )}

        {/* Watch / torrent button */}
        {streamStatus === 'not_started' && magnet && (
          <button
            onClick={handleWatch}
            disabled={streamStarting}
            data-testid="watch-button"
            style={{
              padding: '10px 24px',
              borderRadius: 6,
              border: 'none',
              backgroundColor: streamStarting ? '#555' : '#7c8cff',
              color: 'white',
              cursor: streamStarting ? 'not-allowed' : 'pointer',
              fontSize: 15,
              fontWeight: 600,
            }}
          >
            {streamStarting ? t('movie_details.starting') : t('movie_details.watch_torrent')}
          </button>
        )}

        {/* Fallback: archive.org video via backend proxy (avoids CORS) */}
        {streamStatus === 'not_started' && !magnet && movie.video_url && (
          <video
            controls
            style={{ width: '100%', maxWidth: 900, borderRadius: 8, backgroundColor: '#000' }}
          >
            <source
              src={`/api/movies/${encodeURIComponent(id ?? '')}/stream/archive?token=${encodeURIComponent(token ?? '')}`}
              type="video/mp4"
            />
            {movie.available_subtitles.map((sub, i) => (
              <track key={i} kind="subtitles" src={sub} />
            ))}
            {t('movie_details.video_not_supported')}
          </video>
        )}
      </div>

      {/* Subtitles list (when no player shown) */}
      {streamStatus === 'not_started' && !magnet && !movie.video_url && movie.available_subtitles.length > 0 && (
        <div style={{ padding: '0 20px 24px' }}>
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
