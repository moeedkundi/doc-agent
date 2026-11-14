// Server-sent events over fetch, for POST endpoints (EventSource only does GET).

export interface SseMessage {
  event: string
  data: string
  id?: string
}

export async function* readSse(res: Response): AsyncGenerator<SseMessage> {
  if (!res.body) return
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value.replace(/\r\n/g, '\n')
    let cut: number
    while ((cut = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, cut)
      buffer = buffer.slice(cut + 2)
      const msg: SseMessage = { event: 'message', data: '' }
      const data: string[] = []
      for (const line of block.split('\n')) {
        if (!line || line.startsWith(':')) continue
        const i = line.indexOf(':')
        const field = i < 0 ? line : line.slice(0, i)
        const val = i < 0 ? '' : line.slice(i + 1).replace(/^ /, '')
        if (field === 'event') msg.event = val
        else if (field === 'data') data.push(val)
        else if (field === 'id') msg.id = val
      }
      if (data.length) {
        msg.data = data.join('\n')
        yield msg
      }
    }
  }
}
