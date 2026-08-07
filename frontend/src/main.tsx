import React from 'react'
import ReactDOM from 'react-dom/client'
import { DesktopApp } from './DesktopApp'
import { AuthProvider } from './AuthContext'
import './styles.css'
import './home-talk.css'
import './talk-composer.css'
import './avatar.css'
import './product.css'
import './estimate.css'
import './jira.css'
import './design-system.css'
import './auth.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><AuthProvider><DesktopApp /></AuthProvider></React.StrictMode>,
)
