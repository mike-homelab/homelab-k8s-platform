import { useState, useRef, useEffect, useCallback } from 'react'
import { Send, BookOpen, Globe, Brain, Database } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const SUGGESTIONS = [
  'What is in the internal knowledge base?',
  'Explain the AI platform architecture',
  'How are embeddings used for search?',
  'What models are running in the homelab?',
]

function SourceBadge({ source }) {
  if (source === 'qdrant') return (
    <span className="source-badge source-qdrant">
      <Database size={10} /> Knowledge Base
    </span>
  )
  if (source === 'web') return (
    <span className="source-badge source-web">
      <Globe size={10} /> Web Search
    </span>
  )
  return (
    <span className="source-badge source-none">
      <Brain size={10} /> General Knowledge
    </span>
  )
}

export default function App() {
  const [messages, setMessages] = useState([])
  const [input, setInput]       = useState('')
  const [loading, setLoading]   = useState(false)
  const listRef = useRef(null)
  const taRef   = useRef(null)

  // Auto-scroll to bottom
  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight
    }
  }, [messages])

  // Auto-resize textarea
  const handleInput = (e) => {
    setInput(e.target.value)
    const ta = taRef.current
    if (ta) {
      ta.style.height = 'auto'
      ta.style.height = Math.min(ta.scrollHeight, 160) + 'px'
    }
  }

  const sendMessage = useCallback(async (text) => {
    const msg = (text ?? input).trim()
    if (!msg || loading) return

    setInput('')
    if (taRef.current) taRef.current.style.height = 'auto'

    const userMsg = { role: 'user', content: msg, id: Date.now() }
    const asstId  = Date.now() + 1
    const asstMsg = { role: 'assistant', content: '', source: null, streaming: true, id: asstId }

    setMessages(prev => [...prev, userMsg, asstMsg])
    setLoading(true)

    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: msg, stream: true }),
      })

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)

      const reader  = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue
          try {
            const data = JSON.parse(line.slice(6))
            if (data.meta) {
              setMessages(prev => prev.map(m =>
                m.id === asstId ? { ...m, source: data.meta.source } : m
              ))
            } else if (data.token) {
              setMessages(prev => prev.map(m =>
                m.id === asstId ? { ...m, content: m.content + data.token } : m
              ))
            } else if (data.done) {
              setMessages(prev => prev.map(m =>
                m.id === asstId ? {
                  ...m,
                  streaming: false,
                  stats: {
                    prompt_tokens:      data.prompt_tokens,
                    completion_tokens:  data.completion_tokens,
                    duration_ms:        data.duration_ms,
                  }
                } : m
              ))
            }
          } catch { /* skip malformed lines */ }
        }
      }
    } catch (err) {
      setMessages(prev => prev.map(m =>
        m.id === asstId
          ? { ...m, content: `Error: ${err.message}`, streaming: false, error: true }
          : m
      ))
    } finally {
      setLoading(false)
    }
  }, [input, loading])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const isEmpty = messages.length === 0

  return (
    <div className="chat-layout">
      {/* Header */}
      <header className="chat-header">
        <BookOpen size={20} className="chat-logo" />
        <div>
          <div className="chat-brand">Savant</div>
          <div className="chat-tagline">Powered by Local Knowledge & Web Search</div>
        </div>
      </header>

      {/* Message list or empty state */}
      {isEmpty ? (
        <div className="empty-state">
          <BookOpen size={40} style={{ color: 'var(--accent)', opacity: .7 }} />
          <div>
            <h2>What would you like to know?</h2>
            <p>Ask anything — I'll search the internal knowledge base first, then the web if needed.</p>
          </div>
          <div className="suggestion-grid">
            {SUGGESTIONS.map(s => (
              <button key={s} className="suggestion-btn" onClick={() => sendMessage(s)}>
                {s}
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="message-list" ref={listRef}>
          {messages.map(msg => (
            <div key={msg.id} className={`msg-row ${msg.role}`}>
              <div className={`msg-bubble ${msg.role}${msg.streaming ? ' streaming' : ''}`}>
                {msg.role === 'assistant' ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {msg.content || (msg.streaming ? '' : '…')}
                  </ReactMarkdown>
                ) : (
                  msg.content
                )}
              </div>
              {msg.role === 'assistant' && !msg.streaming && (
                <div className="msg-meta">
                  {msg.source && <SourceBadge source={msg.source} />}
                  {msg.stats && (
                    <span>
                      {msg.stats.prompt_tokens}↑ {msg.stats.completion_tokens}↓ · {msg.stats.duration_ms}ms
                    </span>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Input */}
      <div className="input-area">
        <form
          className="input-form"
          onSubmit={e => { e.preventDefault(); sendMessage() }}
        >
          <textarea
            ref={taRef}
            className="input-box"
            placeholder="Ask a question… (Shift+Enter for new line)"
            value={input}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            disabled={loading}
            rows={1}
          />
          <button type="submit" className="send-btn" disabled={!input.trim() || loading}>
            <Send size={18} />
          </button>
        </form>
        <p className="input-hint">
          Searches internal Knowledge Base → falls back to web search → local reasoning.
        </p>
      </div>
    </div>
  )
}
