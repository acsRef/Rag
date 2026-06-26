<template>
  <div class="chat-container">
    <div v-if="!messages.length && !loadingMsgs" class="chat-welcome">
      <div style="text-align:center">
        <h2>RAGent</h2>
        <p>基于知识库的智能问答，上传文档即刻开始</p>
      </div>
      <div class="chat-welcome-suggestions">
        <button class="suggestion-btn" @click="pickSuggestion('什么是 RAG？')">
          <span class="s-label">什么是 RAG？</span>
          <span class="s-desc">了解检索增强生成的基本原理</span>
        </button>
        <button class="suggestion-btn" @click="pickSuggestion('如何优化文档分块策略？')">
          <span class="s-label">如何优化文档分块策略？</span>
          <span class="s-desc">了解语义分块和固定大小分块的区别</span>
        </button>
        <button class="suggestion-btn" @click="pickSuggestion('上传的文档怎么查询？')">
          <span class="s-label">上传的文档怎么查询？</span>
          <span class="s-desc">查看如何检索知识库中的内容</span>
        </button>
      </div>
    </div>

    <div v-else ref="msgContainer" class="chat-messages">
      <div v-if="loadingMsgs" class="chat-loading-hint">正在加载对话...</div>
      <div v-else class="chat-messages-inner">
        <div v-for="(m, i) in messages" :key="i" class="message-row" :class="m.role">
          <div class="message-avatar">{{ m.role === 'user' ? (auth.user?.display_name?.[0] || 'U') : 'R' }}</div>
          <div class="message-body">
            <div v-if="m._think" class="think-block">
              <details>
                <summary>💭 思考过程</summary>
                <div class="think-content" v-text="m._think"></div>
              </details>
            </div>
            <div class="message-bubble" v-html="renderMd(m.content)"></div>
            <div v-if="m.sources?.length" class="sources-bar">
              <button class="sources-toggle" @click="m._sourcesOpen = !m._sourcesOpen">
                ▸ 参考来源 ({{ m.sources.length }})
              </button>
              <div v-if="m._sourcesOpen" class="sources-list">
                <div v-for="(s, si) in m.sources" :key="s.chunk_id" class="source-item">
                  <div class="source-header">{{ si + 1 }}. {{ s.filename || s.title || '未命名文档' }}</div>
                  <div v-if="s.section_path" class="source-section">{{ s.section_path }}</div>
                  <div class="source-snippet">{{ s.snippet }}</div>
                </div>
              </div>
            </div>
            <div class="message-time">{{ m.time || '' }}</div>
          </div>
        </div>
        <div v-if="streaming" class="message-row assistant">
          <div class="message-avatar">R</div>
          <div class="message-body">
            <div v-if="thinkText" class="think-block think-streaming">
              <details open>
                <summary>💭 思考过程</summary>
                <div class="think-content" v-text="thinkText"></div>
              </details>
            </div>
            <div class="message-bubble">
              <div v-if="streamingContent" v-html="renderMd(streamingContent)"></div>
              <span v-if="streamingContent && !streamDone" class="cursor-blink">▍</span>
              <div v-if="!streamingContent && !thinkText && statusMsg" class="thinking-hint">{{ statusMsg }}</div>
              <div v-if="!streamingContent && !thinkText && !statusMsg" class="thinking-dots">
                <span /><span /><span />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div class="chat-input-area">
      <div class="chat-input-wrapper">
        <textarea
          v-model="input"
          class="chat-input"
          rows="1"
          placeholder="请输入问题..."
          @keydown="handleKeydown"
          @input="autoResize"
        />
        <button class="send-btn" :disabled="!input.trim() || streaming" @click="send">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <line x1="22" y1="2" x2="11" y2="13" />
            <polygon points="22 2 15 22 11 13 2 9 22 2" />
          </svg>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, nextTick, watch, onUnmounted, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import { chatApi, type SourceInfo } from '../api/chat'
import { marked } from 'marked'

const auth = useAuthStore()

const props = defineProps<{
  conversations: { conversation_id: string; title: string }[]
  currentConvId: string | null
}>()

const emit = defineEmits<{
  'update-conv-list': []
  'switch-conv': [id: string]
}>()

function renderMd(text: string): string {
  if (!text) return ''
  try {
    return marked.parse(text, { async: false }) as string
  } catch {
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  }
}

const input = ref('')
interface ChatMsg { role: 'user' | 'assistant'; content: string; time?: string; sources?: SourceInfo[]; _sourcesOpen?: boolean; _think?: string }
const messages = ref<ChatMsg[]>([])
const streaming = ref(false)
const streamingContent = ref('')
const currentSources = ref<SourceInfo[]>([])
const streamDone = ref(false)
const loadingMsgs = ref(false)
const msgContainer = ref<HTMLElement | null>(null)
const currentConvId = ref<string | null>(null)
const statusPhase = ref('')
const statusMsg = ref('')
const thinkText = ref('')
let abortController: AbortController | null = null

function now() {
  return new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function abortStream() {
  if (abortController) {
    abortController.abort()
    abortController = null
  }
}

async function loadMessages(cid: string) {
  loadingMsgs.value = true
  messages.value = []
  try {
    const msgs = await chatApi.getMessages(cid)
    messages.value = msgs.map(m => {
      // Backend now stores thinking_content separately; also support legacy <think> tags
      const thinkMatch = m.content.match(/<think>(.*?)<\/think>/s)
      const think = thinkMatch ? thinkMatch[1].trim() : undefined
      const clean = m.content.replace(/<think>.*?<\/think>/s, '').trim()
      return {
        role: m.role,
        content: clean,
        time: m.created_at ? new Date(m.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '',
        _think: think,
      }
    })
  } catch { messages.value = [] }
  loadingMsgs.value = false
  await nextTick()
  scrollToBottom()
}

watch(() => props.currentConvId, async (id) => {
  if (id !== currentConvId.value) {
    abortStream()
    currentConvId.value = id
    streamingContent.value = ''
    streamDone.value = false
    streaming.value = false
    statusPhase.value = ''
    statusMsg.value = ''
    thinkText.value = ''
    if (id) {
      await loadMessages(id)
    } else {
      messages.value = []
    }
  }
})

onMounted(async () => {
  if (props.currentConvId) {
    currentConvId.value = props.currentConvId
    await loadMessages(props.currentConvId)
  }
})

onUnmounted(abortStream)

async function send() {
  const q = input.value.trim()
  if (!q || streaming.value) return
  input.value = ''

  messages.value.push({ role: 'user', content: q, time: now() })
  await nextTick()
  scrollToBottom()

  streaming.value = true
  streamingContent.value = ''
  currentSources.value = []
  streamDone.value = false
  statusPhase.value = ''
  statusMsg.value = ''
  thinkText.value = ''

  abortController = chatApi.streamChat(
    q,
    currentConvId.value,
    null,
    (token) => {
      // Token event — answer text only (thinking comes via onThinking)
      const t = token.replace(/\\n/g, '\n')
      streamingContent.value += t
      scrollToBottom()
    },
    (meta) => {
      currentConvId.value = meta.conversation_id
      emit('switch-conv', meta.conversation_id)
      emit('update-conv-list')
    },
    (sources) => {
      currentSources.value = sources
    },
    () => {
      messages.value.push({
        role: 'assistant',
        content: streamingContent.value,
        sources: [...currentSources.value],
        time: now(),
        _think: thinkText.value || undefined,
      })
      streaming.value = false
      streamingContent.value = ''
      streamDone.value = true
      statusPhase.value = ''
      statusMsg.value = ''
      emit('update-conv-list')
      scrollToBottom()
    },
    (err) => {
      messages.value.push({ role: 'assistant', content: `错误：${err}`, time: now() })
      streaming.value = false
      streamingContent.value = ''
    },
    (thinking) => {
      // SSE thinking event — separate from answer text
      thinkText.value += thinking.replace(/\\n/g, '\n')
      scrollToBottom()
    },
    (phase, message) => {
      statusPhase.value = phase
      statusMsg.value = message
    },
  )
}

function pickSuggestion(text: string) {
  input.value = text
  send()
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

function autoResize(e: Event) {
  const el = e.target as HTMLTextAreaElement
  el.style.height = 'auto'
  el.style.height = Math.min(el.scrollHeight, 100) + 'px'
}

function scrollToBottom() {
  nextTick(() => {
    if (msgContainer.value) {
      msgContainer.value.scrollTop = msgContainer.value.scrollHeight
    }
  })
}
</script>

<style scoped>
.cursor-blink {
  animation: blink 1s step-end infinite;
}
@keyframes blink {
  50% { opacity: 0; }
}

/* --- Markdown in message-bubble --- */
.message-bubble :deep(p) { margin: 0.4em 0; }
.message-bubble :deep(p:first-child) { margin-top: 0; }
.message-bubble :deep(p:last-child) { margin-bottom: 0; }
.message-bubble :deep(code) {
  background: rgba(0,0,0,0.06);
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 0.92em;
  font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace;
}
.message-bubble :deep(pre) {
  background: rgba(0,0,0,0.04);
  padding: 10px 14px;
  border-radius: 8px;
  overflow-x: auto;
  font-size: 0.9em;
  line-height: 1.4;
  margin: 0.5em 0;
}
.message-bubble :deep(pre code) {
  background: none;
  padding: 0;
  font-size: inherit;
}
.message-bubble :deep(ul), .message-bubble :deep(ol) {
  padding-left: 1.4em;
  margin: 0.3em 0;
}
.message-bubble :deep(li) { margin: 0.15em 0; }
.message-bubble :deep(blockquote) {
  border-left: 3px solid var(--accent);
  margin: 0.4em 0;
  padding: 2px 10px;
  color: var(--text-secondary);
}
.message-bubble :deep(table) {
  border-collapse: collapse;
  margin: 0.4em 0;
  font-size: 0.9em;
}
.message-bubble :deep(th), .message-bubble :deep(td) {
  border: 1px solid var(--border);
  padding: 4px 8px;
  text-align: left;
}
.message-bubble :deep(th) { background: rgba(0,0,0,0.03); }
.message-bubble :deep(a) { color: var(--accent); }

.think-block details {
  margin-bottom: 6px;
}
.think-block summary {
  font-size: 11px;
  color: var(--text-tertiary);
  cursor: pointer;
  user-select: none;
  padding: 4px 8px;
  border-radius: 6px;
  background: rgba(0,0,0,0.04);
  display: inline-block;
}
.think-block.think-streaming summary {
  background: rgba(0, 122, 255, 0.08);
  color: var(--accent);
}
.think-content {
  font-size: 12px;
  color: var(--text-tertiary);
  padding: 6px 10px;
  margin-top: 4px;
  border-left: 2px solid var(--border);
  white-space: pre-wrap;
  line-height: 1.5;
}

.chat-loading-hint {
  text-align: center;
  padding: 24px;
  font-size: 13px;
  color: var(--text-tertiary);
}

.thinking-hint {
  font-size: 13px;
  color: var(--text-tertiary);
  padding: 4px 0;
}
</style>
