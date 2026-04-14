import React, { useEffect, useState } from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
import keycloak from './keycloak'

function AuthGuard() {
  const [authenticated, setAuthenticated] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    keycloak
      .init({
        onLoad: 'login-required',
        checkLoginIframe: false,
      })
      .then((auth) => {
        if (auth) {
          setAuthenticated(true)
          // Refresh token silently before expiry
          setInterval(() => {
            keycloak.updateToken(60).catch(() => keycloak.login())
          }, 30000)
        } else {
          keycloak.login()
        }
      })
      .catch((err) => {
        console.error('Keycloak init failed', err)
        setError('Authentication service unavailable. Please try again.')
      })
  }, [])

  if (error) {
    return (
      <div style={{ display:'flex', alignItems:'center', justifyContent:'center', height:'100vh', background:'#faf8f5', color:'#c0392b', fontFamily:'Inter,sans-serif', flexDirection:'column', gap:'1rem' }}>
        <div style={{ fontSize:'2rem' }}>⚠️</div>
        <div>{error}</div>
        <button onClick={() => keycloak.login()} style={{ padding:'0.5rem 1.5rem', background:'#2d3748', color:'white', border:'none', borderRadius:'8px', cursor:'pointer' }}>
          Retry Login
        </button>
      </div>
    )
  }

  if (!authenticated) {
    return (
      <div style={{ display:'flex', alignItems:'center', justifyContent:'center', height:'100vh', background:'#faf8f5', color:'#888', fontFamily:'Inter,sans-serif', flexDirection:'column', gap:'1rem' }}>
        <div style={{ width:'40px', height:'40px', border:'3px solid #e2e8f0', borderTop:'3px solid #2d3748', borderRadius:'50%', animation:'spin 1s linear infinite' }} />
        <div>Authenticating with Homelab SSO…</div>
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </div>
    )
  }

  return <App keycloak={keycloak} />
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode><AuthGuard /></React.StrictMode>
)

