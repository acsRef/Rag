<template>
  <div class="app-layout">
    <AppSidebar
      :conversations="conversations"
      :current-conv-id="currentConvId"
      @switch-conv="switchConv"
      @new-conv="newConv"
      @delete-conv="deleteConv"
    />
    <main class="main-content">
      <router-view
        :conversations="conversations"
        :current-conv-id="currentConvId"
        @update-conv-list="loadConversations"
        @switch-conv="switchConv"
      />
    </main>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import AppSidebar from './AppSidebar.vue'
import { chatApi, type Conversation } from '../api/chat'

const conversations = ref<Conversation[]>([])
const currentConvId = ref<string | null>(null)

async function loadConversations() {
  try {
    conversations.value = await chatApi.listConversations()
  } catch { /* ignore */ }
}

function switchConv(id: string) {
  currentConvId.value = id
}

function newConv() {
  currentConvId.value = null
}

async function deleteConv(id: string) {
  try {
    await chatApi.deleteConversation(id)
    conversations.value = conversations.value.filter(c => c.conversation_id !== id)
    if (currentConvId.value === id) {
      currentConvId.value = null
    }
  } catch { /* ignore */ }
}

onMounted(loadConversations)
</script>
