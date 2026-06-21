<template>
  <div class="chat-container">
    <div v-if="!messages.length" class="chat-empty">
      Start a conversation
    </div>
    <div v-else ref="msgContainer" class="chat-messages">
      <div
        v-for="(m, i) in messages"
        :key="i"
        class="message-row"
        :class="m.role"
      >
        <div class="message-bubble">{{ m.content }}</div>
      </div>
      <div v-if="streaming" class="message-row assistant">
        <div class="message-bubble">
          {{ streamingContent }}<span v-if="streamingContent && !streamDone" class="cursor-blink">▍</span>
          <div v-if="!streamingContent" class="thinking-dots">
            <span /><span /><span />
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
          placeholder="Ask anything..."
          @keydown="handleKeydown"
          @input="autoResize"
        />
        <button class="send-btn" :disabled="!input.trim() || streaming" @click="send">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
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
import { chatApi } from '../api/chat'

const props = defineProps<{
  conversations: { conversation_id: string; title: string }[]
  currentConvId: string | null
}>()

const emit = defineEmits<{
  'update-conv-list': []
  'switch-conv': [id: string]
}>()

const input = ref('')
const messages = ref<{ role: 'user' | 'assistant'; content: string }[]>([])
const streaming = ref(false)
const streamingContent = ref('')
const streamDone = ref(false)
const msgContainer = ref<HTMLElement | null>(null)
const currentConvId = ref<string | null>(null)
let abortController: AbortController | null = null

watch(() => props.currentConvId, (id) => {
  if (id !== currentConvId.value) {
    currentConvId.value = id
    messages.value = []
    streamingContent.value = ''
    streamDone.value = false
  }
})

async function send() {
  const q = input.value.trim()
  if (!q || streaming.value) return
  input.value = ''

  messages.value.push({ role: 'user', content: q })
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
      messages.value.push({ role: 'assistant', content: streamingContent.value })
      streaming.value = false
      streamingContent.value = ''
      streamDone.value = true
      emit('update-conv-list')
      scrollToBottom()
    },
    (err) => {
      messages.value.push({ role: 'assistant', content: `Error: ${err}` })
      streaming.value = false
      streamingContent.value = ''
    },
  )
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
  el.style.height = Math.min(el.scrollHeight, 120) + 'px'
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
