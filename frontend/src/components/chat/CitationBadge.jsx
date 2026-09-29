/** Turns bare [N] citation markers inside markdown text nodes into small styled
 * badges, so citations stand out from the prose without a custom markdown syntax. */
function splitCitations(text, sourceCount, keyPrefix) {
  const parts = text.split(/(\[\d+\])/g)
  return parts.map((part, i) => {
    const match = part.match(/^\[(\d+)\]$/)
    if (match) {
      const n = Number(match[1])
      if (n >= 1 && n <= sourceCount) {
        return (
          <sup
            key={`${keyPrefix}-${i}`}
            className="mx-0.5 rounded bg-emerald-500/20 px-1 text-[10px] font-semibold text-emerald-300"
          >
            {n}
          </sup>
        )
      }
    }
    return part
  })
}

/** Maps a react-markdown `children` array through citation splitting, leaving
 * already-rendered elements (bold, links, etc.) untouched. */
export function withCitations(children, sourceCount) {
  if (!sourceCount) return children
  const items = Array.isArray(children) ? children : [children]
  return items.flatMap((child, i) =>
    typeof child === 'string' ? splitCitations(child, sourceCount, i) : [child],
  )
}
