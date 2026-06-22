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
            <th>分块数</th>
            <th>日期</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="d in docs" :key="d.document_id">
            <td>{{ d.filename }}</td>
            <td>
              <span class="badge" :class="statusBadge(d.status)">{{ statusLabel(d.status) }}</span>
            </td>
            <td style="color:var(--text-tertiary);font-size:12px">{{ d.kb_id.slice(0, 8) }}...</td>
            <td>{{ d.chunk_count }}</td>
            <td style="color:var(--text-tertiary);font-size:12px">{{ formatDate(d.created_at) }}</td>
          </tr>
          <tr v-if="!docs.length">
            <td colspan="5" style="text-align:center;color:var(--text-tertiary);padding:36px;font-size:13px">暂无文档</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="uploading" class="modal-overlay">
      <div class="modal-card" style="text-align:center">
        <p style="font-size:14px">上传中... {{ uploadProgress }}/{{ uploadTotal }}</p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { documentApi, type DocumentItem } from '../api/documents'
import { kbApi, type KB } from '../api/kb'

const docs = ref<DocumentItem[]>([])
const fileInput = ref<HTMLInputElement | null>(null)
const kbs = ref<KB[]>([])
const uploading = ref(false)
const uploadProgress = ref(0)
const uploadTotal = ref(0)
const loading = ref(false)
const dragOver = ref(false)

async function load() {
  loading.value = true
  try {
    docs.value = await documentApi.list()
    kbs.value = await kbApi.list()
  } catch { /* ignore */ }
  loading.value = false
}

function triggerUpload() {
  fileInput.value?.click()
}

async function handleFiles(e: Event) {
  const files = (e.target as HTMLInputElement).files
  if (!files?.length) return
  await uploadFiles(Array.from(files))
}

async function handleDrop(e: DragEvent) {
  dragOver.value = false
  const files = e.dataTransfer?.files
  if (!files?.length) return
  await uploadFiles(Array.from(files))
}

async function uploadFiles(files: File[]) {
  const targetKb = kbs.value[0]
  if (!targetKb) {
    alert('请先创建知识库')
    return
  }
  uploading.value = true
  uploadTotal.value = files.length
  uploadProgress.value = 0
  for (const f of files) {
    try {
      await documentApi.upload(f, targetKb.id)
    } catch { /* ignore */ }
    uploadProgress.value++
  }
  uploading.value = false
  await load()
}

function statusLabel(s: string) {
  const map: Record<string, string> = { indexed: '已完成', indexing: '处理中', processing: '处理中', error: '失败', failed: '失败' }
  return map[s] || s
}

function statusBadge(s: string) {
  if (s === 'indexed' || s === 'completed') return 'badge-success'
  if (s === 'indexing' || s === 'processing') return 'badge-warning'
  if (s === 'error' || s === 'failed') return 'badge-error'
  return 'badge-info'
}

function formatDate(iso: string) {
  if (!iso) return '-'
  return new Date(iso).toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

onMounted(load)
</script>
