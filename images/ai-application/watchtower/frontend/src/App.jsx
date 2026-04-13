import { useState, useEffect, useCallback, useRef } from 'react'
import { Search, RefreshCw, Activity, Cpu, Zap, Eye } from 'lucide-react'

const TABS = [
  { id: 'reasoning', label: 'Reasoning',  endpoint: '/api/feed/reasoning' },
  { id: 'coder',     label: 'Coder',       endpoint: '/api/feed/coder'     },
  { id: 'embedding', label: 'Embedding',   endpoint: '/api/feed/embedding' },
  { id: 'reranker',  label: 'Reranker',    endpoint: '/api/feed/reranker'  },
]

function fmtDuration(ms) {
  if (!ms && ms !== 0) return '—'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
}

function fmtTs(iso) {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch { return iso }
}

function SourceBadge({ source }) {
  if (!source || source === 'none') return null
  const labels = { qdrant: '🗄️ KB', web: '🌐 Web', none: null }
  const label = labels[source]
  if (!label) return null
  return <span className="tag" style={{ background: 'var(--badge-bg, rgba(255,255,255,0.08))', color: 'var(--text-dim)' }}>{label}</span>
}

function FeedCard({ item }) {
  const [expanded, setExpanded] = useState(false)
  const prompt = item.prompt || ''
  const truncated = prompt.length > 200 && !expanded

  return (
    <div className="feed-card">
      <div className="card-header">
        <div>
          <div className="card-model">{item.model || 'unknown'}</div>
        </div>
        <div className="card-ts">{fmtTs(item.timestamp)}</div>
      </div>

      {prompt ? (
        <div
          className="card-prompt"
          style={{ cursor: prompt.length > 200 ? 'pointer' : 'default' }}
          onClick={() => prompt.length > 200 && setExpanded(e => !e)}
          title={prompt.length > 200 ? (expanded ? 'Click to collapse' : 'Click to expand') : ''}
        >
          {truncated ? prompt.slice(0, 200) + '…' : prompt}
        </div>
      ) : (
        <div className="card-prompt-empty">No prompt captured</div>
      )}

      <div className="card-tags">
        {item.source && item.source !== 'none' && <SourceBadge source={item.source} />}
        {(item.input_tokens > 0) && (
          <span className="tag tag-in">
            <Zap size={11} /> {item.input_tokens} in
          </span>
        )}
        {(item.output_tokens > 0) && (
          <span className="tag tag-out">
            <Activity size={11} /> {item.output_tokens} out
          </span>
        )}
        {(item.duration_ms > 0) && (
          <span className="tag tag-ms">
            <Cpu size={11} /> {fmtDuration(item.duration_ms)}
          </span>
        )}
      </div>
    </div>
  )
}

function Feed({ tab, search }) {
  const [items, setItems]   = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState(null)
  const [count, setCount]   = useState(0)

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const url = `${tab.endpoint}?limit=50&hours=6${search ? `&search=${encodeURIComponent(search)}` : ''}`
      const r = await fetch(url)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      const data = await r.json()
      setItems(data.items || [])
      setCount(data.items?.length || 0)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [tab.endpoint, search])

  useEffect(() => {
    fetchData()
    const id = setInterval(fetchData, 30_000)
    return () => clearInterval(id)
  }, [fetchData])

  if (loading) return (
    <div className="state-box">
      <RefreshCw size={28} style={{ animation: 'spin 1s linear infinite' }} />
      <span>Loading {tab.label} feed…</span>
    </div>
  )

  if (error) return (
    <div className="state-box">
      <Eye size={28} />
      <span className="state-error">Failed to load: {error}</span>
      <span style={{ fontSize: 12 }}>Is the backend running and Loki/Tempo reachable?</span>
    </div>
  )

  if (!items.length) return (
    <div className="state-box">
      <Activity size={28} />
      <span>No requests recorded in the last 6 hours</span>
      {search && <span style={{ fontSize: 12 }}>Try clearing the search filter</span>}
    </div>
  )

  return (
    <>
      <div className="feed-meta">
        <span>{count} request{count !== 1 ? 's' : ''} · last 6h</span>
        <span>Auto-refreshes every 30s</span>
      </div>
      <div className="feed-list">
        {items.map((item, i) => <FeedCard key={item.trace_id || `${item.timestamp}-${i}`} item={item} />)}
      </div>
    </>
  )
}

export default function App() {
  const [activeTab, setActiveTab] = useState(TABS[0])
  const [search, setSearch] = useState('')
  const [deferredSearch, setDeferredSearch] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const debounce = useRef(null)
  const [feedKey, setFeedKey] = useState(0)

  const handleSearch = (val) => {
    setSearch(val)
    clearTimeout(debounce.current)
    debounce.current = setTimeout(() => setDeferredSearch(val), 400)
  }

  const handleRefresh = () => {
    setRefreshing(true)
    setFeedKey(k => k + 1)
    setTimeout(() => setRefreshing(false), 800)
  }

  return (
    <div className="watchtower-layout">
      <header className="watchtower-header">
        <div className="watchtower-wordmark">
          <Eye size={22} />
          <div>
            <div className="watchtower-title">watchtower</div>
            <div className="watchtower-subtitle">LLM Request Feed · AI Platform</div>
          </div>
        </div>
        <button className={`refresh-btn${refreshing ? ' spinning' : ''}`} onClick={handleRefresh}>
          <RefreshCw size={14} /> Refresh
        </button>
      </header>

      <div className="search-wrap">
        <Search size={16} />
        <input
          className="search-input"
          type="text"
          placeholder="Search requests…"
          value={search}
          onChange={e => handleSearch(e.target.value)}
        />
      </div>

      <div className="tabs-bar">
        {TABS.map(t => (
          <button
            key={t.id}
            className={`tab-btn${activeTab.id === t.id ? ' active' : ''}`}
            onClick={() => setActiveTab(t)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <Feed key={`${activeTab.id}-${feedKey}`} tab={activeTab} search={deferredSearch} />
    </div>
  )
}
