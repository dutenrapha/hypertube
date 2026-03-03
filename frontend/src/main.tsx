import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { I18nextProvider } from 'react-i18next'
import i18n from './i18n'
import { AuthProvider } from './contexts/AuthContext'
import { Header } from './components/Header'
import Register from './pages/Register'
import Login from './pages/Login'
import AuthCallback from './pages/AuthCallback'
import ForgotPassword from './pages/ForgotPassword'
import ResetPassword from './pages/ResetPassword'
import Profile from './pages/Profile'
import UserProfile from './pages/UserProfile'
import Search from './pages/Search'
import MovieDetails from './pages/MovieDetails'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <I18nextProvider i18n={i18n}>
      <BrowserRouter>
        <AuthProvider>
          <Header />
          <Routes>
            <Route path="/" element={<Search />} />
            <Route path="/register" element={<Register />} />
            <Route path="/login" element={<Login />} />
            <Route path="/auth/callback" element={<AuthCallback />} />
            <Route path="/forgot-password" element={<ForgotPassword />} />
            <Route path="/reset-password" element={<ResetPassword />} />
            <Route path="/profile" element={<Profile />} />
            <Route path="/users/:id" element={<UserProfile />} />
            <Route path="/movies/:id" element={<MovieDetails />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </I18nextProvider>
  </StrictMode>,
)
