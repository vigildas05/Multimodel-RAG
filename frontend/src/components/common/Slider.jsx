export default function Slider({ label, value, min, max, step = 1, onChange, description, formatValue }) {
  return (
    <div className="py-1.5">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-sm text-slate-200">{label}</span>
        <span className="text-xs font-mono text-slate-400">{formatValue ? formatValue(value) : value}</span>
      </div>
      {description && <div className="mb-1 text-xs text-slate-500">{description}</div>}
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-emerald-500"
      />
    </div>
  )
}
