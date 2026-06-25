<template>
  <div style="display:flex;flex-direction:column;flex:1;overflow:hidden">
    <div class="page-header">
      <span>文档管理</span>
      <button class="btn btn-primary" @click="triggerUpload">+ 上传</button>
      <input ref="fileInput" type="file" style="display:none" multiple @change="handleFiles" />
    </div>

    <div class="doc-content">
      <div
        class="upload-area"
        :class="{ 'drag-over': dragOver }"
        @drop.prevent="handleDrop"
        @dragover.prevent="dragOver = true"
        @dragleave="dragOver = false"
        @click="triggerUpload"
      >
        <div class="upload-area-icon">📄</div>
        <div class="upload-area-text">拖拽文件到此处，或点击上传</div>
        <div class="upload-area-hint">支持 PDF、DOCX、TXT、MD、图片等格式</div>
      </div>

      <div v-if="loading" style="padding:20px">
        <div class="skeleton skeleton-line" />
        <div class="skeleton skeleton-line" />
        <div class="skeleton skeleton-line" style="width:45%" />
      </div>

      <table v-else class="data-table">
        <thead>
          <tr>
            <th>文件名</th>
            <th>状态</th>
            <th>知识库</th>
            <th>分块</th>
            <th>日期</th>
            <th style="width:60px">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="d in docs" :key="d.document_id">
            <td>
              {{ d.filename }}
              <span v-if="d.error_message" class="error-hint" :title="d.error_message">⚠</span>
            </td>
            <td>
              <span class="badge" :class="statusBadge(d.status)">{{ statusLabel(d.status) }}</span>
            </td>
            <td style="color:var(--text-tertiary);font-size:12px">{{ d.kb_id.slice(0, 8) }}...</td>
            <td>
              <template v-if="d.status === 'indexed' || d.status === 'partial' || d.status === 'indexing'">
                {{ d.embedded_chunk_count }}<span style="color:var(--text-tertiary)">/{{ d.chunk_count }}</span>
              </template>
              <template v-else>{{ d.chunk_count }}</template>
            </td>
            <td style="color:var(--text-tertiary);font-size:12px">{{ formatDate(d.created_at) }}</td>
            <td>
              <button class="btn btn-ghost" style="color:var(--red);padding:3px 10px;font-size:11px" @click="confirmDelete(d)">删除</button>
            </td>
          </tr>
          <tr v-if="!docs.length">
            <td colspan="6" style="text-align:center;color:var(--text-tertiary);padding:36px;font-size:13px">暂无文档</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="showKbPicker" class="modal-overlay" @click.self="showKbPicker = false">
      <div class="modal-card">
        <h3 style="margin:0 0 12px;font-size:15px">选择目标知识库</h3>
        <select v-model="selectedKbId" class="form-select" style="margin-bottom:12px">
          <option v-for="kb in kbs" :key="kb.id" :value="kb.id">{{ kb.name }}</option>
        </select>
        <div style="display:flex;gap:8px;justify-content:flex-end">
          <button class="btn btn-ghost" @click="showKbPicker = false">取消</button>
          <button class="btn btn-primary" @click="confirmUpload">上传</button>
        </div>
      </div>
    </div>

    <div v-if="uploading" class="modal-overlay">
      <div class="modal-card" style="text-align:center">
        <p style="font-size:14px">上传中... {{ uploadProgress }}/{{ uploadTotal }}</p>
      </div>
    </div>

    <div v-if="showDeleteConfirm" class="modal-overlay" @click.self="showDeleteConfirm = false">
      <div class="modal-card">
        <h3 style="margin:0 0 12px;font-size:15px">确认删除</h3>
        <p style="font-size:13px;color:var(--text-secondary);margin-bottom:16px">
          确定要删除 <strong>{{ deletingDoc?.filename }}</strong> 吗？关联的分块数据将一并删除，不可恢复。
        </p>
        <div style="display:flex;gap:8px;justify-content:flex-end">
          <button class="btn btn-ghost" @click="showDeleteConfirm = false">取消</button>
          <button class="btn btn-danger" @click="doDelete">删除</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { documentApi, type DocumentItem } from '../api/documents'
import { kbApi, type KB } from '../api/kb'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const docs = ref<DocumentItem[]>([])
const fileInput = ref<HTMLInputElement | null>(null)
const kbs = ref<KB[]>([])
const uploading = ref(false)
const uploadProgress = ref(0)
const uploadTotal = ref(0)
const loading = ref(false)
const dragOver = ref(false)
const selectedKbId = ref('')
const showKbPicker = ref(false)
const pendingFiles = ref<File[]>([])
const showDeleteConfirm = ref(false)
const deletingDoc = ref<DocumentItem | null>(null)

// SSE 文档进度订阅 — 替代 3 秒轮询
// 收到事件时用 immutable update 只更新对应行的某几个字段
// Vue 的 key-diffing 会让只有"那一个 cell"重渲染,整行不闪
let eventSource: EventSource | null = null

function applyDocEvent(ev: {
  document_id: string
  embedded_chunk_count: number
  chunk_count: number
  status?: string
  error_message?: string
}) {
  const idx = docs.value.findIndex(d => d.document_id === ev.document_id)
  if (idx < 0) return  // 文档不在当前列表(可能用户切走了)
  // 不可变更新:用 spread 替换匹配行,其他行原样引用
  // Vue 3 reactivity 会让只有该行的"被改字段"重渲染
  docs.value[idx] = {
    ...docs.value[idx],
    embedded_chunk_count: ev.embedded_chunk_count ?? docs.value[idx].embedded_chunk_count,
    chunk_count: ev.chunk_count ?? docs.value[idx].chunk_count,
    status: ev.status ?? docs.value[idx].status,
    error_message: ev.error_message ?? docs.value[idx].error_message,
  }
}

function startEventSource() {
  if (eventSource) return
  const token = localStorage.getItem('token')
  const url = token ? `/api/v1/documents/events?token=${token}` : '/api/v1/documents/events'
  eventSource = new EventSource(url)
  eventSource.addEventListener('doc_progress', (e) => {
    try {
      const data = JSON.parse((e as MessageEvent).data)
      applyDocEvent(data)
    } catch { /* ignore */ }
  })
  eventSource.onerror = () => {
    // EventSource 浏览器原生会自动重连,这里不做事
  }
}

function stopEventSource() {
  if (eventSource) {
    eventSource.close()
    eventSource = null
  }
}

async function load() {
  // 初次加载显示 skeleton;SSE 接管后只在数据变化时局部更新
  if (docs.value.length === 0) loading.value = true
  try {
    docs.value = await documentApi.list()
    kbs.value = await kbApi.list()
  } catch { /* ignore */ }
  loading.value = false
}

function triggerUpload() {
  fileInput.value?.click()
}

function promptKbPicker(files: File[]) {
  pendingFiles.value = files
  selectedKbId.value = auth.user?.workspace_kb_id || kbs.value[0]?.id || ''
  showKbPicker.value = true
}

async function handleFiles(e: Event) {
  const files = (e.target as HTMLInputElement).files
  if (!files?.length) return
  promptKbPicker(Array.from(files))
}

async function handleDrop(e: DragEvent) {
  dragOver.value = false
  const files = e.dataTransfer?.files
  if (!files?.length) return
  promptKbPicker(Array.from(files))
}

async function confirmUpload() {
  if (!selectedKbId.value) {
    alert('请选择知识库')
    return
  }
  showKbPicker.value = false
  await uploadFiles(pendingFiles.value, selectedKbId.value)
}

async function uploadFiles(files: File[], kbId: string) {
  uploading.value = true
  uploadTotal.value = files.length
  uploadProgress.value = 0
  for (const f of files) {
    try {
      await documentApi.upload(f, kbId)
    } catch { /* ignore */ }
    uploadProgress.value++
  }
  uploading.value = false
  await load()
}

function confirmDelete(doc: DocumentItem) {
  deletingDoc.value = doc
  showDeleteConfirm.value = true
}

async function doDelete() {
  if (!deletingDoc.value) return
  const id = deletingDoc.value.document_id
  showDeleteConfirm.value = false
  deletingDoc.value = null
  try {
    await documentApi.remove(id)
  } catch { /* ignore */ }
  await load()
}

function statusLabel(s: string) {
  const map: Record<string, string> = { indexed: '已完成', indexing: '嵌入中', processing: '处理中', partial: '部分可用', error: '失败', failed: '失败' }
  return map[s] || s
}

function statusBadge(s: string) {
  if (s === 'indexed' || s === 'completed') return 'badge-success'
  if (s === 'indexing' || s === 'processing') return 'badge-warning'
  if (s === 'partial') return 'badge-warning'
  if (s === 'error' || s === 'failed') return 'badge-error'
  return 'badge-info'
}

function formatDate(iso: string) {
  if (!iso) return '-'
  return new Date(iso).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

onMounted(async () => {
  await load()
  startEventSource()
})
onUnmounted(stopEventSource)
</script>

<style scoped>
.error-hint {
  cursor: help;
  margin-left: 4px;
  font-size: 12px;
  color: var(--orange);
}
</style>
