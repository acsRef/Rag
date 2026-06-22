<template>
  <aside class="sidebar">
    <div class="sidebar-logo">RAGent</div>

    <nav class="sidebar-nav">
      <button class="nav-item" :class="{ active: route.path.startsWith('/chat') }" @click="go('/chat')">
        💬 对话
      </button>
      <button
        v-if="auth.hasPermission('doc.upload') || auth.hasPermission('doc.read_all')"
        class="nav-item"
        :class="{ active: route.path === '/documents' }"
        @click="go('/documents')"
      >
        📄 文档
      </button>
      <button
        v-if="auth.hasPermission('kb.create') || auth.hasPermission('kb.delete')"
        class="nav-item"
        :class="{ active: route.path === '/kb' }"
        @click="go('/kb')"
      >
        🗂 知识库
      </button>
    </nav>

    <template v-if="route.path.startsWith('/chat')">
      <div class="sidebar-divider" />
      <button class="new-conv-btn" @click="$emit('new-conv')">
        <span>+</span> 新对话
      </button>
      <div class="sidebar-conversations">
        <div
          v-for="c in conversations"
          :key="c.conversation_id"
          class="conv-item"
          :class="{ active: currentConvId === c.conversation_id }"
          @click="$emit('switch-conv', c.conversation_id)"
        >
          <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" :title="c.title">{{ c.title }}</span>
          <button class="delete-btn-icon" title="删除" @click.stop="$emit('delete-conv', c.conversation_id)">✕</button>
        </div>
        <div v-if="!conversations.length" style="padding:10px;font-size:11px;color:var(--text-tertiary);text-align:center">
          暂无对话
        </div>
      </div>
    </template>

    <div class="sidebar-footer">
      <div class="sidebar-user">
        <span>{{ auth.user?.display_name || auth.user?.username }}</span>
        <button class="logout-btn" @click="doLogout">退出</button>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import type { Conversation } from '../api/chat'

defineProps<{
  conversations: Conversation[]
  currentConvId: string | null
}>()

defineEmits<{
  'switch-conv': [id: string]
  'new-conv': []
  'delete-conv': [id: string]
}>()

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

function go(path: string) {
  router.push(path)
}

function doLogout() {
  auth.logout()
  router.push('/login')
}
</script>
