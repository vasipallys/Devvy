import { Component, type ErrorInfo, type ReactNode } from 'react'
import { CircleAlert, RotateCw } from 'lucide-react'

interface Props { children: ReactNode; onHome?: () => void }
interface State { error: Error | null; stack: string }

/** Catches render errors so a crash shows what broke instead of an empty black window.
 *
 *  React unmounts the whole tree when a render throws. Without a boundary the user is
 *  left with a blank page and no way to tell a crash apart from a slow load — which is
 *  the opposite of what this app promises everywhere else. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, stack: '' }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Keep the full detail in the console for a bug report; show a readable summary below.
    console.error('Devvy render error:', error, info.componentStack)
    this.setState({ stack: info.componentStack || '' })
  }

  render() {
    const { error, stack } = this.state
    if (!error) return this.props.children
    return <div className="crash-screen" role="alert">
      <div className="crash-card">
        <div className="crash-head"><CircleAlert /><b>This screen stopped rendering</b></div>
        <p>Devvy hit an unexpected error while drawing this view. Your conversations and files
          on disk are unaffected.</p>
        <pre className="crash-message">{error.name}: {error.message}</pre>
        {stack && <details className="crash-detail">
          <summary>Component stack</summary>
          <pre>{stack.trim()}</pre>
        </details>}
        <div className="crash-actions">
          <button onClick={() => this.setState({ error: null, stack: '' })}><RotateCw size={15} /> Try again</button>
          {this.props.onHome && <button onClick={() => { this.setState({ error: null, stack: '' }); this.props.onHome?.() }}>Back to Home</button>}
          <button onClick={() => window.location.reload()}>Reload Devvy</button>
        </div>
        <small>The full error and component stack are in the browser console.</small>
      </div>
    </div>
  }
}
