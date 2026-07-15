import { useEffect } from 'react'
import { pushEvent, sendMessage } from '../agent'
import { useStore } from '../store'

/**
 * 免登录航线编辑：iframe 嵌入 public/route-editor.html。
 * 编辑器保存后 postMessage({type:'editor:saved', route_id}) →
 * 前端转 AG-UI 事件回传 Agent（[EDITOR_SAVED]），Agent 复述变更影响。
 */
export function EditorView() {
  const url = useStore((s) => s.iframeUrl)

  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (e.origin !== window.location.origin) return
      if (e.data?.type === 'editor:saved') {
        pushEvent('编辑结果回传 · postMessage → 前端 → Agent')
        void sendMessage(`[EDITOR_SAVED] route_id=${e.data.route_id}`, { hidden: true })
      }
    }
    window.addEventListener('message', onMsg)
    return () => window.removeEventListener('message', onMsg)
  }, [])

  if (!url) return null
  return (
    <div className="browser">
      <div className="browser-bar">
        <div className="b-dots">
          <i />
          <i />
          <i />
        </div>
        <div className="b-url">
          {window.location.origin}
          {url.slice(0, 46)}…<b>（免登录临时凭证 · 10min）</b>
        </div>
      </div>
      <iframe className="editor-frame" src={url} title="航线编辑器" />
      <div className="editor-note">嵌入方式：iframe · 编辑结果经 postMessage → AG-UI 回传</div>
    </div>
  )
}
