import { useEffect, useState } from 'react'
import { Cpu, HardDrive, ShieldCheck } from 'lucide-react'
import { api } from './api'
import type { SystemStatus } from './types'

export function SystemStatusChip({ detail = false }: { detail?: boolean }) {
  const [status, setStatus] = useState<SystemStatus>()
  const [offline, setOffline] = useState(false)
  useEffect(() => {
    let live = true
    api.systemStatus().then(value => { if (live) { setStatus(value); setOffline(false) } }).catch(() => live && setOffline(true))
    return () => { live = false }
  }, [])
  if (offline) return <div className="system-chip offline"><span/><b>API offline</b></div>
  return <div className={`system-chip ${status?.model.loaded ? 'ready' : ''}`} title={status?.model.error || status?.model.id || 'Checking local runtime'}>
    <span className="system-dot"/>
    <div><b>{status?.model.loaded ? 'Gemma ready' : 'Local runtime'}</b><small>{status ? `${status.model.device.toUpperCase()} · ${status.model.generation}` : 'Checking…'}</small></div>
    {detail && status && <div className="system-detail"><span><ShieldCheck/> {status.trust.privacy}</span><span><HardDrive/> On-device data</span><span><Cpu/> {status.model.dtype}</span></div>}
  </div>
}
