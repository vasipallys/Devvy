import { useEffect, useState } from 'react'
import { Check, Copy, Share2, UserPlus, X } from 'lucide-react'
import { api } from './api'
import type { ResourceShare, ShareResource } from './types'

export function ShareButton({ resourceType, resourceId, label = 'Share' }: {
  resourceType: ShareResource
  resourceId: string
  label?: string
}) {
  const [open, setOpen] = useState(false)
  return <>
    <button className="share-action" onClick={() => setOpen(true)}><Share2/> {label}</button>
    {open && <ShareDialog resourceType={resourceType} resourceId={resourceId} onClose={() => setOpen(false)}/>} 
  </>
}

function ShareDialog({ resourceType, resourceId, onClose }: {
  resourceType: ShareResource
  resourceId: string
  onClose: () => void
}) {
  const [email, setEmail] = useState('')
  const [permission, setPermission] = useState<'viewer' | 'editor'>('viewer')
  const [shares, setShares] = useState<ResourceShare[]>([])
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  const load = async () => {
    try {
      const all = await api.shares(false)
      setShares(all.filter(item => item.resource_type === resourceType && item.resource_id === resourceId))
    } catch (cause) { setError((cause as Error).message) }
  }
  useEffect(() => { load() }, [resourceType, resourceId])

  const grant = async (event: React.FormEvent) => {
    event.preventDefault(); setError(''); setSaved(false)
    try { await api.share(resourceType, resourceId, email, permission); setEmail(''); setSaved(true); await load() }
    catch (cause) { setError((cause as Error).message) }
  }

  return <div className="modal-scrim">
    <section className="share-dialog" role="dialog" aria-modal="true" aria-labelledby="share-title">
      <button className="modal-close" onClick={onClose} aria-label="Close"><X/></button>
      <div className="dialog-icon"><Share2/></div><span className="eyebrow">EXPLICIT ACCESS</span>
      <h2 id="share-title">Share this {resourceType}</h2>
      <p>Only existing workspace members can receive access. You remain the owner and can revoke access at any time.</p>
      <form onSubmit={grant} className="share-form">
        <label>Member email<input type="email" value={email} onChange={event => setEmail(event.target.value)} placeholder="teammate@company.com" required/></label>
        <label>Permission<select value={permission} onChange={event => setPermission(event.target.value as 'viewer' | 'editor')}><option value="viewer">Viewer — can read</option><option value="editor">Editor — can contribute</option></select></label>
        <button className="auth-primary"><UserPlus/> Grant access</button>
      </form>
      {saved && <div className="share-success"><Check/> Access updated.</div>}
      {error && <div className="auth-form-error" role="alert">{error}</div>}
      <div className="share-list"><span className="eyebrow">PEOPLE WITH ACCESS</span>
        {shares.length === 0 ? <p>No one else has access.</p> : shares.map(item => <div key={item.id}>
          <span className="member-avatar">{item.recipient?.display_name?.[0] || '?'}</span>
          <span><b>{item.recipient?.display_name}</b><small>{item.recipient?.email} · {item.permission}</small></span>
          <button onClick={async () => { await api.revokeShare(item.id); await load() }}>Revoke</button>
        </div>)}
      </div>
      <button className="copy-id" onClick={() => navigator.clipboard.writeText(resourceId)}><Copy/> Copy resource ID</button>
    </section>
  </div>
}

