/** 当前页串行轮询；停止后丢弃在途响应，所有终态到齐即停止。 */
export function pollToolRunPage<T extends { items: { status: string }[] }>(options: {
  load: (signal: AbortSignal) => Promise<T>
  onData: (value: T) => void
  onError: (error: unknown) => void
  schedule?: (callback: () => void, delay: number) => ReturnType<typeof setTimeout>
  cancel?: (timer: ReturnType<typeof setTimeout>) => void
}) {
  const controller = new AbortController()
  const schedule = options.schedule || setTimeout
  const cancel = options.cancel || clearTimeout
  let timer: ReturnType<typeof setTimeout> | undefined
  let pending = true
  // 每次请求结束后才安排下一次，不会并发覆盖当前页。
  async function refresh() {
    try {
      const value = await options.load(controller.signal)
      if (controller.signal.aborted) return
      pending = value.items.some((row) => row.status === 'queued' || row.status === 'running')
      options.onData(value)
    } catch (error) {
      if (controller.signal.aborted) return
      options.onError(error)
    }
    if (pending && !controller.signal.aborted) timer = schedule(() => void refresh(), 5000)
  }
  void refresh()
  return () => {
    controller.abort()
    if (timer !== undefined) cancel(timer)
  }
}

/** 迟到的查询不得把已完成的同一记录降回进行中。 */
export function keepToolRunTerminal<T extends { id: number; status: string }>(current: T, incoming: T): T {
  return current.id === incoming.id && ['succeeded', 'failed', 'cancelled'].includes(current.status)
    && ['queued', 'running'].includes(incoming.status) ? current : incoming
}
