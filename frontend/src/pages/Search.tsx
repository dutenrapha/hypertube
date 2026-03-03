import { useState, useEffect, useRef, useMemo, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../contexts/AuthContext'
import './Search.css'

interface Movie {
  id: string
  title: string
  year: string | null
  cover_url: string | null
  imdb_rating: string | null
  genre: string | null
  watched: boolean
  source: string
}

interface SearchResponse {
  movies: Movie[]
  page: number
  limit: number
  total: number
  has_next: boolean
}

type SortBy = 'rating' | 'year' | 'name'

const LIMIT = 20

export default function Search() {
  const { token } = useAuth()
  const { t } = useTranslation()
  const navigate = useNavigate()

  const [inputValue, setInputValue] = useState('')
  const [query, setQuery] = useState('')
  const [movies, setMovies] = useState<Movie[]>([])
  const [page, setPage] = useState(1)
  const [hasNext, setHasNext] = useState(false)
  const [loading, setLoading] = useState(false)
  const [sortBy, setSortBy] = useState<SortBy>('rating')
  const [genreFilter, setGenreFilter] = useState('')
  const [yearMin, setYearMin] = useState('')
  const [yearMax, setYearMax] = useState('')

  const sentinelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!token) navigate('/login')
  }, [token, navigate])

  const runSearch = useCallback(() => {
    setQuery(inputValue)
    setPage(1)
    setMovies([])
    setHasNext(false)
  }, [inputValue])

  const fetchMovies = useCallback(async (q: string, p: number, append: boolean) => {
    if (!token) return
    const url = `/api/search?q=${encodeURIComponent(q)}&page=${p}&limit=${LIMIT}`
    setLoading(true)
    try {
      const resp = await fetch(url, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!resp.ok) return
      const data: SearchResponse = await resp.json()
      setMovies(prev => (append ? [...prev, ...data.movies] : data.movies))
      setHasNext(data.has_next)
      setPage(p)
    } catch {
      // network error — ignore
    } finally {
      setLoading(false)
    }
  }, [token])

  // Fetch on mount (initial list) and when query changes (user clicked Search)
  useEffect(() => {
    fetchMovies(query, 1, false)
  }, [query, fetchMovies])

  // Infinite scroll via IntersectionObserver
  useEffect(() => {
    const sentinel = sentinelRef.current
    if (!sentinel) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && hasNext && !loading) {
          fetchMovies(query, page + 1, true)
        }
      },
      { threshold: 0.1 }
    )
    observer.observe(sentinel)
    return () => observer.disconnect()
  }, [hasNext, loading, page, query, fetchMovies])

  // Collect unique genres from loaded movies
  const genreOptions = useMemo(() => {
    const set = new Set<string>()
    movies.forEach(m => {
      m.genre?.split(',').forEach(g => {
        const trimmed = g.trim()
        if (trimmed) set.add(trimmed)
      })
    })
    return Array.from(set).sort()
  }, [movies])

  // Client-side sort and filter
  const displayMovies = useMemo(() => {
    let result = [...movies]

    if (genreFilter) {
      result = result.filter(m =>
        m.genre?.toLowerCase().includes(genreFilter.toLowerCase())
      )
    }

    if (yearMin) {
      result = result.filter(m => parseInt(m.year ?? '0') >= parseInt(yearMin))
    }
    if (yearMax) {
      result = result.filter(m => parseInt(m.year ?? '9999') <= parseInt(yearMax))
    }

    if (sortBy === 'name') {
      result.sort((a, b) => a.title.localeCompare(b.title))
    } else if (sortBy === 'year') {
      result.sort((a, b) => parseInt(b.year ?? '0') - parseInt(a.year ?? '0'))
    } else {
      result.sort((a, b) =>
        parseFloat(b.imdb_rating ?? '0') - parseFloat(a.imdb_rating ?? '0')
      )
    }

    return result
  }, [movies, sortBy, genreFilter, yearMin, yearMax])

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#0f0f1a', color: 'white' }}>
      {/* Controls bar */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 12,
          padding: '16px 20px',
          backgroundColor: '#1a1a2e',
          borderBottom: '1px solid #333',
        }}
      >
        <form
          onSubmit={e => {
            e.preventDefault()
            runSearch()
          }}
          style={{ display: 'flex', flexWrap: 'wrap', gap: 12, flex: '1 1 200px' }}
        >
          <input
            type="text"
            placeholder={t('search.placeholder')}
            value={inputValue}
            onChange={e => setInputValue(e.target.value)}
            style={{
              flex: '1 1 200px',
              minWidth: 120,
              padding: '8px 12px',
              borderRadius: 6,
              border: '1px solid #444',
              backgroundColor: '#0f0f1a',
              color: 'white',
            }}
          />
          <button
            type="submit"
            data-testid="search-button"
            style={{
              padding: '8px 16px',
              borderRadius: 6,
              border: '1px solid #444',
              backgroundColor: '#333',
              color: 'white',
              cursor: 'pointer',
            }}
          >
            {t('search.button')}
          </button>
        </form>
        <select
          value={sortBy}
          onChange={e => setSortBy(e.target.value as SortBy)}
          data-testid="sort-select"
          style={{
            padding: '8px 12px',
            borderRadius: 6,
            border: '1px solid #444',
            backgroundColor: '#0f0f1a',
            color: 'white',
          }}
        >
          <option value="rating">{t('search.sort_rating')}</option>
          <option value="year">{t('search.sort_year')}</option>
          <option value="name">{t('search.sort_name')}</option>
        </select>
        {genreOptions.length > 0 && (
          <select
            value={genreFilter}
            onChange={e => setGenreFilter(e.target.value)}
            data-testid="genre-select"
            style={{
              padding: '8px 12px',
              borderRadius: 6,
              border: '1px solid #444',
              backgroundColor: '#0f0f1a',
              color: 'white',
            }}
          >
            <option value="">{t('search.all_genres')}</option>
            {genreOptions.map(g => (
              <option key={g} value={g}>{g}</option>
            ))}
          </select>
        )}
        <input
          type="number"
          placeholder={t('search.year_from')}
          value={yearMin}
          onChange={e => setYearMin(e.target.value)}
          style={{
            width: 100,
            padding: '8px 12px',
            borderRadius: 6,
            border: '1px solid #444',
            backgroundColor: '#0f0f1a',
            color: 'white',
          }}
        />
        <input
          type="number"
          placeholder={t('search.year_to')}
          value={yearMax}
          onChange={e => setYearMax(e.target.value)}
          style={{
            width: 100,
            padding: '8px 12px',
            borderRadius: 6,
            border: '1px solid #444',
            backgroundColor: '#0f0f1a',
            color: 'white',
          }}
        />
      </div>

      {/* Movie grid */}
      <div className="movie-grid">
        {displayMovies.map(movie => (
          <div
            key={movie.id}
            onClick={() =>
              navigate(`/movies/${encodeURIComponent(movie.id)}`, {
                state: { title: movie.title, year: movie.year, cover_url: movie.cover_url },
              })
            }
            style={{
              position: 'relative',
              borderRadius: 8,
              overflow: 'hidden',
              backgroundColor: '#1e1e2e',
              cursor: 'pointer',
            }}
          >
            {movie.watched && (
              <span
                style={{
                  position: 'absolute',
                  top: 8,
                  left: 8,
                  zIndex: 1,
                  backgroundColor: '#4caf50',
                  color: 'white',
                  fontSize: 10,
                  padding: '2px 6px',
                  borderRadius: 4,
                  fontWeight: 700,
                  letterSpacing: 1,
                }}
              >
                {t('search.watched_badge')}
              </span>
            )}
            <img
              src={movie.cover_url ?? ''}
              alt={movie.title}
              style={{
                width: '100%',
                aspectRatio: '2/3',
                objectFit: 'cover',
                display: 'block',
                backgroundColor: '#2a2a3e',
              }}
              onError={e => {
                ;(e.target as HTMLImageElement).style.display = 'none'
              }}
            />
            <div style={{ padding: '8px 10px' }}>
              <p
                style={{
                  margin: 0,
                  fontWeight: 600,
                  fontSize: 13,
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {movie.title}
              </p>
              <p style={{ margin: '4px 0 0', fontSize: 12, color: '#aaa' }}>
                {movie.year ?? '—'}
                {movie.imdb_rating && ` · ★ ${movie.imdb_rating}`}
              </p>
            </div>
          </div>
        ))}
      </div>

      {/* Infinite scroll sentinel */}
      <div ref={sentinelRef} style={{ height: 1 }} />

      {loading && (
        <p style={{ textAlign: 'center', color: '#aaa', padding: 16 }}>
          {t('search.loading')}
        </p>
      )}

      {!loading && displayMovies.length === 0 && (
        <p style={{ textAlign: 'center', color: '#aaa', padding: 40 }}>
          {t('search.no_results')}
        </p>
      )}
    </div>
  )
}
