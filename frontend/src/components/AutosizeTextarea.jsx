import { useEffect, useRef } from 'react'

function resizeTextarea(textarea) {
  if (!textarea) return
  textarea.style.height = 'auto'
  const minimum = Number.parseFloat(window.getComputedStyle(textarea).minHeight) || 0
  textarea.style.height = `${Math.max(textarea.scrollHeight, minimum)}px`
}

export default function AutosizeTextarea({ onInput, value, ...props }) {
  const textareaRef = useRef(null)

  useEffect(() => {
    const textarea = textareaRef.current
    const onResize = () => resizeTextarea(textarea)
    resizeTextarea(textarea)
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [value])

  return <textarea
    {...props}
    onInput={event => {
      resizeTextarea(event.currentTarget)
      onInput?.(event)
    }}
    ref={textareaRef}
    style={{ ...props.style, overflowY: 'hidden', resize: 'none' }}
    value={value}
  />
}