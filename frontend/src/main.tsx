import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

function App() {
  return (
    <div>
      <h1>Hypertube</h1>
    </div>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
