"use client"

import { useState, useEffect, useRef } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"

interface Message {
  role: "user" | "agent"
  content: string
}

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState("")
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [socket, setSocket] = useState<WebSocket | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    // Create a new session on load
    const initSession = async () => {
      try {
        const resp = await fetch('/api/session', { method: 'POST' })
        const data = await resp.json()
        setSessionId(data.id)
      } catch (err) {
        console.error("Failed to init session:", err)
      }
    }
    initSession()
  }, [])

  useEffect(() => {
    if (!sessionId) return

    // Connect WebSocket
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const wsUrl = `${protocol}//${window.location.host}/api/ws/${sessionId}`
    const ws = new WebSocket(wsUrl)

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      if (data.type === 'agent_event') {
        setMessages(prev => [...prev, { role: "agent", content: data.content }])
      }
    }

    setSocket(ws)
    return () => ws.close()
  }, [sessionId])

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollIntoView({ behavior: "smooth" })
    }
  }, [messages])

  const handleSend = () => {
    if (!input.trim() || !socket) return
    
    const userMsg = input.trim()
    setMessages(prev => [...prev, { role: "user", content: userMsg }])
    setInput("")
    
    socket.send(JSON.stringify({ content: userMsg }))
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-between p-4 bg-slate-950 text-slate-50">
      <div className="w-full max-w-4xl flex flex-col gap-4 h-[90vh]">
        <Card className="bg-slate-900 border-slate-800 flex-1 flex flex-col overflow-hidden">
          <CardHeader>
            <CardTitle className="text-xl font-bold text-indigo-400">Kodewriter Agent</CardTitle>
          </CardHeader>
          <CardContent className="flex-1 overflow-hidden p-0">
            <ScrollArea className="h-full p-4">
              <div className="flex flex-col gap-4">
                {messages.map((m, i) => (
                  <div
                    key={i}
                    className={`flex ${
                      m.role === "user" ? "justify-end" : "justify-start"
                    }`}
                  >
                    <div
                      className={`max-w-[80%] rounded-lg px-4 py-2 ${
                        m.role === "user"
                          ? "bg-indigo-600 text-white"
                          : "bg-slate-800 text-slate-200 border border-slate-700"
                      }`}
                    >
                      {m.content}
                    </div>
                  </div>
                ))}
                <div ref={scrollRef} />
              </div>
            </ScrollArea>
          </CardContent>
        </Card>

        <div className="flex gap-2">
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleSend()}
            placeholder="Type your coding request..."
            className="bg-slate-900 border-slate-800 text-slate-50 focus-visible:ring-indigo-500"
          />
          <Button onClick={handleSend} className="bg-indigo-600 hover:bg-indigo-700">
            Send
          </Button>
        </div>
      </div>
    </main>
  )
}
