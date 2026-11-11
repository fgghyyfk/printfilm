import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // 浏览器抠图：onnxruntime-web 自己按 import.meta.url 找 wasm，预构建会打乱路径
  optimizeDeps: { exclude: ['onnxruntime-web'] },
  worker: { format: 'es' },
})
