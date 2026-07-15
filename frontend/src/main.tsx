import React from 'react'
import ReactDOM from 'react-dom/client'
import { App } from './App'
import { sendMessage } from './agent'
import './styles.css'
import { useStore } from './store'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)

// 开场白（不经 LLM，纯前端）
useStore.setState({
  items: [
    {
      kind: 'agent',
      id: 'welcome',
      streaming: false,
      text:
        '你好，我是飞行作业助手。已接入 3 个业务能力服务（调度 · 航线 · 飞前检查），' +
        '可以用一句话安排整个飞行核查流程。\n\n试试下面这句，或直接输入你的指令。',
    },
  ],
})

// 供演示彩排用：window.__demo('我要起飞')
declare global {
  interface Window {
    __demo: (text: string) => void
  }
}
window.__demo = (text: string) => void sendMessage(text)
