<template>
  <div style="display:flex;flex-direction:column;flex:1;overflow:hidden">
    <div class="page-header">
      <span>Knowledge Bases</span>
      <button v-if="auth.hasPermission('kb.create')" class="btn btn-primary" @click="showCreate = true">+ New KB</button>
    </div>

    <div class="kb-content">
      <div v-if="!kbs.length" style="text-align:center;color:var(--text-tertiary);padding:60px;font-size:15px">
        No knowledge bases yet
      </div>
      <div v-else class="kb-grid">
        <div v-for="kb in kbs" :key="kb.id" class="kb-card">
          <h3>{{ kb.name }}</h3>
          <div class="kb-card-meta">
            <span class="badge" :class="visibilityBadge(kb.visibility)">{{ kb.visibility }}</span>
          </div>
          <div class="kb-card-actions">
            <button
              v-if="auth.hasPermission('kb.delete')"
              class="btn btn-danger"
              style="font-size:12px;padding:4px 12px"
              @click="delKb(kb.id)"
            >
              Delete
            </button>
          </div>
        </div>
      </div>
    </div>

    <div v-if="showCreate" class="modal-overlay" @click.self="showCreate = false">
      <div class="modal-card">
        <h2>New Knowledge Base</h2>
        <div class="form-group">
          <label>Name</label>
          <input v-model="newName" class="form-input" placeholder="KB name" @keyup.enter="createKb" />
        </div>
        <div class="form-group">
          <label>Visibility</label>
          <select v-model="newVisibility" class="form-select">
            <option value="public">Public</option>
            <option value="internal">Internal</option>
            <option value="restricted">Restricted</option>
          </select>
        </div>
        <div class="modal-actions">
          <button class="btn btn-ghost" @click="showCreate = false">Cancel</button>
          <button class="btn btn-primary" :disabled="!newName.trim()" @click="createKb">Create</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useAuthStore } from '../stores/auth'
import { kbApi, type KB } from '../api/kb'

const auth = useAuthStore()
const kbs = ref<KB[]>([])
const showCreate = ref(false)
const newName = ref('')
const newVisibility = ref('public')

async function load() {
  try {
    kbs.value = await kbApi.list()
  } catch { /* ignore */ }
}

async function createKb() {
  if (!newName.value.trim()) return
  try {
    await kbApi.create(newName.value.trim(), newVisibility.value)
    showCreate.value = false
    newName.value = ''
    await load()
  } catch { /* ignore */ }
}

async function delKb(id: string) {
  if (!confirm('Delete this knowledge base?')) return
  try {
    await kbApi.delete(id)
    await load()
  } catch { /* ignore */ }
}

function visibilityBadge(v: string) {
  if (v === 'public') return 'badge-success'
  if (v === 'internal') return 'badge-info'
  return 'badge-warning'
}

onMounted(load)
</script>
