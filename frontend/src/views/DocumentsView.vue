<template>
  <div style="display:flex;flex-direction:column;flex:1;overflow:hidden">
    <div class="page-header">
      <span>Documents</span>
      <button class="btn btn-primary" @click="triggerUpload">+ Upload</button>
      <input ref="fileInput" type="file" style="display:none" multiple @change="handleFiles" />
    </div>

    <div class="doc-content">
      <div
        class="upload-area"
        @drop.prevent="handleDrop"
        @dragover.prevent
        @click="triggerUpload"
      >
        <div class="upload-area-icon">📄</div>
        <div class="upload-area-text">Drop files here or click to upload</div>
        <div class="upload-area-hint">Supports PDF, DOCX, TXT, MD, images</div>
      </div>

      <table class="data-table">
        <thead>
          <tr>
            <th>File</th>
            <th>Status</th>
            <th>Knowledge Base</th>
            <th>Chunks</th>
            <th>Date</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="d in docs" :key="d.document_id">
            <td>{{ d.filename }}</td>
            <td>
              <span class="badge" :class="statusBadge(d.status)">{{ d.status }}</span>
            </td>
            <td style="color:var(--text-tertiary);font-size:13px">{{ d.kb_id.slice(0, 8) }}...</td>
            <td>{{ d.chunk_count }}</td>
            <td style="color:var(--text-tertiary);font-size:13px">{{ formatDate(d.created_at) }}</td>
          </tr>
          <tr v-if="!docs.length">
            <td colspan="5" style="text-align:center;color:var(--text-tertiary);padding:40px">No documents yet</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="uploading" class="modal-overlay">
      <div class="modal-card" style="text-align:center">
        <p>Uploading... {{ uploadProgress }}/{{ uploadTotal }}</p>
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

async function load() {
  try {
    docs.value = await documentApi.list()
    kbs.value = await kbApi.list()
  } catch { /* ignore */ }
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
  const files = e.dataTransfer?.files
  if (!files?.length) return
  await uploadFiles(Array.from(files))
}

async function uploadFiles(files: File[]) {
  const targetKb = kbs.value[0]
  if (!targetKb) {
    alert('Create a knowledge base first')
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

function statusBadge(s: string) {
  if (s === 'indexed' || s === 'completed') return 'badge-success'
  if (s === 'indexing' || s === 'processing') return 'badge-warning'
  if (s === 'error' || s === 'failed') return 'badge-error'
  return 'badge-info'
}

function formatDate(iso: string) {
  if (!iso) return '-'
  return new Date(iso).toLocaleDateString('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

onMounted(load)
</script>
