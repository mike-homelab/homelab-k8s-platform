import { useState, useEffect, useCallback, useRef } from 'react'
import { Search, RefreshCw, Brain, Target, Layers, Code, Activity, Cpu, Zap, Eye, Database, Sparkle } from 'lucide-react'

function fmtDuration(ms) {
  if (!ms && ms !== 0) return '—'
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function fmtTs(iso) {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  } catch { return iso }
}

function StepRow({ step, type }) {
  const configs = {
    embedding: { label: 'Embedding', icon: <Layers size={14} />, color: '#4a5568' },
    reranker:  { label: 'Reranker',  icon: <Target size={14} />, color: '#c05621' },
    reasoning: { label: 'Reasoning', icon: <Sparkle size={14} />, color: '#2d3748' },
    coder:     { label: 'Coder',     icon: <Code size={14}   />, color: '#1a202c' },
  }
  const config = configs[type] || { label: type, icon: <Activity size={14} /> }

  return (
    <div className="step-item">
      <div className="step-label">
        {config.icon}
        <span style={{ color: config.color }}>{config.label}</span>
      </div>
      <div className="step-model">{step.model}</div>
      <div className="step-metrics">
        {type === 'embedding' && step.input_tokens > 0 && (
           <span className="tag tag-chunks"><Database size={11} /> {step.input_tokens} chunks</span>
        )}
        {step.input_tokens > 0 && type !== 'embedding' && (
          <span className="tag tag-in"><Zap size={11} /> {step.input_tokens} in</span>
        )}
        {step.output_tokens > 0 && (
          <span className="tag tag-out"><Activity size={11} /> {step.output_tokens} out</span>
        )}
        {step.duration_ms > 0 && (
          <span className="tag tag-ms"><Cpu size={11} /> {fmtDuration(step.duration_ms)}</span>
        )}
      </div>
    </div>
  )
}

function UnifiedRequestCard({ item }) {
  // Sort steps into logical categories
  const embedding = item.steps?.find(s => s.source === 'embed')
  const reranker  = item.steps?.find(s => s.source === 'rerank')
  const reasoning = item.steps?.find(s => ['web', 'qdrant', 'none', 'savant'].includes(s.source))
  const coder     = item.steps?.find(s => s.source === 'coder' || s.model?.includes('coder'))

  return (
    <div className="request-card">
      <div className="card-header">
        <div className="card-title">User Interaction</div>
        <div className="card-ts">{fmtTs(item.timestamp)}</div>
      </div>
      
      <div className="card-prompt">{item.prompt || 'No message captured'}</div>

      <div className="steps-list">
        {embedding && <StepRow step={embedding} type="embedding" />}
        {reranker  && <StepRow step={reranker}  type="reranker"  />}
        {reasoning && <StepRow step={reasoning} type="reasoning" />}
        {coder     && <StepRow step={coder}     type="coder"     />}
      </div>
    </div>
  )
}

function Feed({ search }) {
  const [items, setItems]   = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const url = `/api/feed/unified?limit=50&hours=24${search ? `&search=${encodeURIComponent(search)}` : ''}`
      const r = await fetch(url)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()

      // Invert steps within items because the grouping loop might have them reversed
      const itemsProcessed = (data.items || []).map(it => ({
        ...it,
        steps: [...it.steps].reverse() // The query was ORDER DESC, so grouping might need re-sorting
      }))

      setItems(itemsProcessed)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [search])

  useEffect(() => {
    fetchData()
    const id = setInterval(fetchData, 30_000)
    return () => clearInterval(id)
  }, [fetchData])

  if (loading && !items.length) return (
    <div className="state-box">
      <RefreshCw size={24} style={{ animation: 'spin 2s linear infinite' }} />
      <span>Fetching platform activity…</span>
    </div>
  )

  if (error) return (
    <div className="state-box">
      <Eye size={24} />
      <span style={{ color: '#c05621' }}>{error}</span>
      <button className="refresh-btn" style={{marginTop: 10}} onClick={fetchData}>Try again</button>
    </div>
  )

  if (!items.length) return (
    <div className="state-box" style={{ background: '#ffffff', borderRadius: 12, border: '1px solid #e5e2da' }}>
      <Brain size={32} style={{ opacity: 0.15 }} />
      <span>No requests found. Generating telemetry will populate this feed.</span>
    </div>
  )

  return (
    <div className="feed-list">
      {items.map((item) => (
        <UnifiedRequestCard key={item.request_id} item={item} />
      ))}
    </div>
  )
}

export default function App() {
  const [search, setSearch] = useState('')
  const [deferredSearch, setDeferredSearch] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const debounce = useRef(null)

  const handleSearch = (val) => {
    setSearch(val)
    clearTimeout(debounce.current)
    debounce.current = setTimeout(() => setDeferredSearch(val), 400)
  }

  const handleRefresh = () => {
    setRefreshing(true)
    setTimeout(() => {
      window.location.reload()
    }, 500)
  }

  return (
    <div className="watchtower-layout">
      <header className="watchtower-header">
        <div className="watchtower-wordmark">
          <Brain size={28} />
          <div>
            <div className="watchtower-title">watchtower</div>
            <div className="watchtower-subtitle">Platform Observability · Est. 2026</div>
          </div>
        </div>
        <button className={`refresh-btn${refreshing ? ' spinning' : ''}`} onClick={handleRefresh}>
          <RefreshCw size={14} /> Update
        </button>
      </header>

      <div className="search-wrap">
        <Search size={18} />
        <input
          className="search-input"
          type="text"
          placeholder="Filter conversation by message..."
          value={search}
          onChange={e => handleSearch(e.target.value)}
        />
      </div>

      <div className="feed-meta">
        <span>Daily Activity Summary</span>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
           <div style={{ width: 6, height: 6, background: '#22c55e', borderRadius: '50%' }} />
           <span>Live Monitoring</span>
        </div>
      </div>

      <Feed search={deferredSearch} />
    </div>
  )
}
