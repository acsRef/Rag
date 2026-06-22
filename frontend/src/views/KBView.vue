<template>
  <div style="display:flex;flex-direction:column;flex:1;overflow:hidden">
    <div class="page-header">
      <span>知识库管理</span>
      <button v-if="auth.hasPermission('kb.create')" class="btn btn-primary" @click="showCreate = true">
        + 新建
      </button>
    </div>

    <div class="kb-content">
      <div v-if="loading" class="kb-grid">
        <div v-for="n in 4" :key="n" class="skeleton skeleton-card" />
      </div>

      <div v-else-if="!kbs.length" style="text-align:center;color:var(--text-tertiary);padding:48px;font-size:14px">
        暂无知识库
      </div>

      <div v-else class="kb-grid">
        <div v-for="kb in kbs" :key="kb.id" class="kb-card">
          <div class="kb-card-accent" :style="{ background: accentColor(kb.visibility) }" />
          <div class="kb-card-body">
            <h3>{{ kb.name }}</h3>
            <div class="kb-card-meta">
              <span class="badge" :class="visibilityBadge(kb.visibility)">{{ visibilityLabel(kb.visibility) }}</span>
            </div>
            <div class="kb-card-actions">
              <button
                v-if="auth.hasPermission('kb.delete')"
                class="btn btn-danger"
                style="font-size:11px;padding:4px 10px"
                @click="delKb(kb.id)"
              >
                删除
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="showCreate" class="modal-overlay" @click.self="showCreate = false">
      <div class="modal-card">
        <h2>新建知识库</h2>
        <div class="form-group">
          <label>名称</label>
          <input v-model="newName" class="form-input" placeholder="知识库名称" @keyup.enter="createKb" />
        </div>
        <div class="form-group">
          <label>可见性</label>
          <select v-model="newVisibility" class="form-select">
            <option value="public">公开</option>
            <option value="internal">内部</option>
            <option value="restricted">受限</option>
          </select>
        </div>
        <div class="modal-actions">
          <button class="btn btn-ghost" @click="showCreate = false">取消</button>
          <button class="btn btn-primary" :disabled="!newName.trim()" @click="createKb">创建</button>
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
const loading = ref(false)

async function load() {
  loading.value = true
  try {
    kbs.value = await kbApi.list()
  } catch { /* ignore */ }
  loading.value = false
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
  if (!confirm('确认删除此知识库？')) return
  try {
    await kbApi.delete(id)
    await load()
  } catch { /* ignore */ }
}

function visibilityLabel(v: string) {
  const map: Record<string, string> = { public: '公开', internal: '内部', restricted: '受限' }
  return map[v] || v
}

function visibilityBadge(v: string) {
  if (v === 'public') return 'badge-success'
  if (v === 'internal') return 'badge-info'
  return 'badge-warning'
}

function accentColor(v: string) {
  if (v === 'public') return 'var(--green)'
  if (v === 'internal') return 'var(--accent)'
  return 'var(--orange)'
}

onMounted(load)
</script>
