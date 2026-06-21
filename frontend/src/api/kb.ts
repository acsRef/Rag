import api from './index'

export interface KB {
  id: string
  name: string
  visibility: string
  owner_id: string
  allowed_role_ids: number[]
}

export const kbApi = {
  async list() {
    const res = await api.get<KB[]>('/kb')
    return res.data
  },
  async create(name: string, visibility: string = 'public') {
    const res = await api.post<KB>('/kb', { name, visibility })
    return res.data
  },
  async delete(id: string) {
    await api.delete(`/kb/${id}`)
  },
}
