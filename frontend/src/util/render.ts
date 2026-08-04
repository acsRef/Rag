/**
 * 安全的 Markdown 渲染：v-html 前置消毒。
 *
 * 背景：marked 默认透传原始 HTML。文档内容可经提示词注入让 LLM 原样引用
 * `<img src=x onerror=...>` 之类标签，直接 v-html 会在查看者浏览器执行
 * （token 在 localStorage → 会话劫持）。
 *
 * 策略（零新依赖）：
 *  1. 先 HTML 转义（& < >）——杀死一切原始 HTML 注入；Markdown 语法
 *     （标题/列表/代码围栏/链接）不含裸 HTML，不受影响。
 *  2. marked 渲染后再按协议白名单外的危险 scheme 过滤 href/src——
 *     防 `[x](javascript:...)` 这类点击型 XSS（转义管不到 markdown 链接目标）。
 */
import { marked } from 'marked'

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

// marked 输出的属性一律双引号；危险 scheme 整段替换为 #（含 data:——
// 聊天消息没有合法的 data: 内嵌资源场景）
const UNSAFE_URL_RE = /(\s(?:href|src)\s*=\s*")\s*(?:javascript|vbscript|file|data)\s*:[^"]*(")/gi

export function renderMarkdown(text: string): string {
  if (!text) return ''
  const escaped = escapeHtml(text)
  try {
    const html = marked.parse(escaped, { async: false }) as string
    return html.replace(UNSAFE_URL_RE, '$1#$2')
  } catch {
    return escaped
  }
}
