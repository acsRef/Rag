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
      <div class="chat-messages-inner">
        <div v-for="(m, i) in messages" :key="i" class="message-row" :class="m.role">
          <div class="message-avatar">{{ m.role === 'user' ? (auth.user?.display_name?.[0] || 'U') : 'R' }}</div>
          <div class="message-body">
            <div class="message-bubble">{{ m.content }}</div>
            <div class="message-time">{{ m.time || '' }}</div>
          </div>
        </div>
        <div v-if="streaming" class="message-row assistant">
          <div class="message-avatar">R</div>
          <div class="message-body">
            <div class="message-bubble">
              {{ streamingContent }}<span v-if="streamingContent && !streamDone" class="cursor-blink">▍</span>
              <div v-if="!streamingContent" class="thinking-dots">
                <span /><span /><span />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="loadingMsgs" class="chat-messages" style="padding:24px;max-width:720px;margin:0 auto;width:100%">
      <div class="skeleton skeleton-line" style="width:40%" />
      <div class="skeleton skeleton-line" />
      <div class="skeleton skeleton-line" style="width:55%" />
      <div style="height:16px" />
      <div class="skeleton skeleton-line" style="width:35%;margin-left:auto" />
      <div class="skeleton skeleton-line" style="width:50%;margin-left:auto" />
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
import { ref, nextTick, watch } from 'vue'
import { useAuthStore } from '../stores/auth'
import { chatApi } from '../api/chat'

const auth = useAuthStore()

const props = defineProps<{
  conversations: { conversation_id: string; title: string }[]
  currentConvId: string | null
}>()

const emit = defineEmits<{
  'update-conv-list': []
  'switch-conv': [id: string]
}>()

const input = ref('')
const messages = ref<{ role: 'user' | 'assistant'; content: string; time?: string }[]>([])
const streaming = ref(false)
const streamingContent = ref('')
const streamDone = ref(false)
const loadingMsgs = ref(false)
const msgContainer = ref<HTMLElement | null>(null)
const currentConvId = ref<string | null>(null)
let abortController: AbortController | null = null

function now() {
  return new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

watch(() => props.currentConvId, (id) => {
  if (id !== currentConvId.value) {
    currentConvId.value = id
    if (id) {
      loadingMsgs.value = true
      setTimeout(() => { loadingMsgs.value = false }, 600)
    }
    messages.value = []
    streamingContent.value = ''
    streamDone.value = false
  }
})

async function send() {
  const q = input.value.trim()
  if (!q || streaming.value) return
  input.value = ''

  messages.value.push({ role: 'user', content: q, time: now() })
  await nextTick()
  scrollToBottom()

  streaming.value = true
  streamingContent.value = ''
  streamDone.value = false

  abortController = chatApi.streamChat(
    q,
    currentConvId.value,
    null,
    (token) => {
      streamingContent.value += token
      scrollToBottom()
    },
    (meta) => {
      currentConvId.value = meta.conversation_id
      emit('switch-conv', meta.conversation_id)
      emit('update-conv-list')
    },
    () => {
      messages.value.push({ role: 'assistant', content: streamingContent.value, time: now() })
      streaming.value = false
      streamingContent.value = ''
      streamDone.value = true
      emit('update-conv-list')
      scrollToBottom()
    },
    (err) => {
      messages.value.push({ role: 'assistant', content: `错误：${err}`, time: now() })
      streaming.value = false
      streamingContent.value = ''
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
</style>
