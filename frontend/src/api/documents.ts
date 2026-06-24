import api from './index'

export interface DocumentItem {
  document_id: string
  filename: string
  status: string
  kb_id: string
  chunk_count: number
  embedded_chunk_count: number
  error_message: string
  created_at: string
}

export interface UploadRes {
  document_id: string
  filename: string
  status: string
  chunk_count: number
  embedded_chunk_count?: number
  message?: string
}

export const documentApi = {
  async list(kbId?: string) {
    const params = kbId ? { kb_id: kbId } : {}
    const res = await api.get<DocumentItem[]>('/documents', { params })
    return res.data
  },
  async upload(file: File, kbId: string) {
    const form = new FormData()
    form.append('file', file)
    form.append('kb_id', kbId)
    const res = await api.post<UploadRes>('/documents/upload', form, { timeout: 300000 })
    return res.data
  },
  async remove(documentId: string) {
    const res = await api.delete(`/documents/${documentId}`)
    return res.data
  },
}
