import api from './index'

export interface Conversation {
  conversation_id: string
  title: string
  created_at: string
  updated_at: string
}

export interface SourceInfo {
  chunk_id: string
  document_id: string
  filename: string
  title: string
  section_path: string
  snippet: string
  score: number
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  created_at?: string
}

export const chatApi = {
  async listConversations() {
    const res = await api.get<Conversation[]>('/chat/conversations')
    return res.data
  },
  async deleteConversation(id: string) {
    await api.delete(`/chat/conversations/${id}`)
  },
  async getMessages(conversationId: string): Promise<ChatMessage[]> {
    const res = await api.get(`/chat/conversations/${conversationId}/messages`)
    return res.data
  },
  streamChat(
    query: string,
    conversationId: string | null,
    knowledgeBaseIds: string[] | null,
    onToken: (token: string) => void,
    onMetadata: (data: { conversation_id: string }) => void,
    onSources: (sources: SourceInfo[]) => void,
    onDone: () => void,
    onError: (err: string) => void,
    onThinking?: (text: string) => void,
    onStatus?: (phase: string, message: string) => void,
  ): AbortController {
    const controller = new AbortController()
    const token = localStorage.getItem('token') || ''
    const body = JSON.stringify({ query, conversation_id: conversationId, knowledge_base_ids: knowledgeBaseIds })

    fetch('/api/v1/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body,
      signal: controller.signal,
    }).then(async (res) => {
      const reader = res.body?.getReader()
      if (!reader) return
      const decoder = new TextDecoder()
      let buffer = ''
      let lastEventType = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''
        for (const line of lines) {
          if (line.startsWith('event: ')) {
            lastEventType = line.slice(7).trim()
            if (lastEventType === 'done') {
              onDone()
            }
          } else if (line.startsWith('data: ')) {
            const data = line.slice(6).trim()
            if (lastEventType === 'error') {
              lastEventType = ''
              try { const parsed = JSON.parse(data); onError(parsed.error || data) }
              catch { onError(data) }
            } else if (lastEventType === 'sources') {
              lastEventType = ''
              try { onSources(JSON.parse(data)) }
              catch { /* ignore */ }
            } else if (lastEventType === 'metadata') {
              lastEventType = ''
              try { const parsed = JSON.parse(data); onMetadata(parsed) }
              catch { /* ignore */ }
            } else if (lastEventType === 'status') {
              lastEventType = ''
              try {
                const parsed = JSON.parse(data)
                if (onStatus) onStatus(parsed.phase, parsed.message)
              } catch { /* ignore */ }
            } else if (lastEventType === 'thinking') {
              lastEventType = ''
              if (onThinking) onThinking(data)
            } else if (data.startsWith('{')) {
              try {
                const parsed = JSON.parse(data)
                if (parsed.conversation_id) {
                  onMetadata(parsed)
                }
              } catch { /* ignore */ }
            } else {
              onToken(data)
            }
          }
        }
      }
    }).catch((err) => {
      if (err.name !== 'AbortError') {
        onError(err.message || 'Connection error')
      }
    })

    return controller
  },
}
