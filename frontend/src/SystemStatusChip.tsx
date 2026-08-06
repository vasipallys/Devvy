import { useEffect, useState } from 'react'
import { Cpu, HardDrive, ShieldCheck } from 'lucide-react'
import { api } from './api'
import { Tooltip } from './Tooltip'
import type { SystemStatus } from './types'

export function SystemStatusChip({ detail = false }: { detail?: boolean }) {
  const [status, setStatus] = useState<SystemStatus>()
  const [offline, setOffline] = useState(false)
  useEffect(() => {
    let live = true
    api.systemStatus().then(value => { if (live) { setStatus(value); setOffline(false) } }).catch(() => live && setOffline(true))
    return () => { live = false }
  }, [])
  if (offline) return <Tooltip label="API offline"
    detail="The browser cannot reach the local backend on 127.0.0.1:8765. Nothing is lost — start the backend and any request that was running will still be in Activity.">
    <div className="system-chip offline"><span/><b>API offline</b></div></Tooltip>
  return <Tooltip
    label={status?.model.loaded ? 'Model loaded and local' : 'Local runtime'}
    detail={status?.model.error
      ? `The runtime reported: ${status.model.error}`
      : status
        ? `${status.model.id} running on ${status.model.device.toUpperCase()} at ${status.model.dtype}. It loads on first use, so the first request of a session is slower. Nothing you type is sent anywhere.`
        : 'Checking whether the local model is loaded. It loads lazily on the first request.'}>
    <div className={`system-chip ${status?.model.loaded ? 'ready' : ''}`}>
    <span className="system-dot"/>
    <div><b>{status?.model.loaded ? 'Gemma ready' : 'Local runtime'}</b><small>{status ? `${status.model.device.toUpperCase()} · ${status.model.generation}` : 'Checking…'}</small></div>
    {detail && status && <div className="system-detail"><span><ShieldCheck/> {status.trust.privacy}</span><span><HardDrive/> On-device data</span><span><Cpu/> {status.model.dtype}</span></div>}
  </div></Tooltip>
}
