import type { MascotState } from '../types'

interface MascotProps {
  state?: MascotState
  size?: 'sm' | 'md' | 'lg' | 'hero'
  label?: string
}

export function Mascot({ state = 'neutral', size = 'md', label }: MascotProps) {
  return (
    <div className={`mascot mascot--${state} mascot--${size}`} role="img" aria-label={label ?? `Tavi is ${state}`}>
      <svg viewBox="0 0 180 220" aria-hidden="true">
        <defs>
          <linearGradient id="tavi-shell" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#f5f0e6" />
            <stop offset="1" stopColor="#d4c6b4" />
          </linearGradient>
          <filter id="tavi-shadow" x="-40%" y="-40%" width="180%" height="200%">
            <feDropShadow dx="0" dy="12" stdDeviation="10" floodColor="#0f1d1a" floodOpacity=".2" />
          </filter>
        </defs>
        <g className="mascot__float" filter="url(#tavi-shadow)">
          <path className="mascot__scarf" d="M53 120c-15 24-9 56 17 70l11-30-4-35z" fill="#d56d4a" />
          <path className="mascot__tail" d="M113 166c18 13 29 9 37-2-1 24-15 38-37 29z" fill="#9cc6c8" />
          <path className="mascot__shell" d="M90 24c14 0 27 15 34 35 24 8 37 28 34 53-3 23-18 38-39 44-7 20-17 34-29 34-13 0-23-14-30-34-21-6-36-21-39-44-3-25 10-45 34-53 8-20 21-35 35-35z" fill="url(#tavi-shell)" stroke="#122521" strokeWidth="6" />
          <circle cx="90" cy="105" r="53" fill="#122521" />
          <circle cx="90" cy="105" r="42" fill="#355044" stroke="#d9a441" strokeWidth="3" />
          <path className="mascot__needle" d="M90 64l9 36-9 8-9-8z" fill="#d56d4a" />
          <path className="mascot__needle mascot__needle--south" d="M90 146l-9-36 9-8 9 8z" fill="#f5f0e6" opacity=".9" />
          <g className="mascot__face">
            <ellipse className="mascot__eye mascot__eye--left" cx="73" cy="105" rx="4.5" ry="6" fill="#fbf8f1" />
            <ellipse className="mascot__eye mascot__eye--right" cx="107" cy="105" rx="4.5" ry="6" fill="#fbf8f1" />
            <path className="mascot__mouth" d="M78 123c8 8 16 8 24 0" fill="none" stroke="#fbf8f1" strokeWidth="3" strokeLinecap="round" />
          </g>
          <path className="mascot__pack" d="M129 80c17 3 26 17 23 33l-5 24-18-5 3-24-11-17z" fill="#a84c31" stroke="#122521" strokeWidth="4" />
          <circle className="mascot__spark mascot__spark--a" cx="34" cy="52" r="5" fill="#d9a441" />
          <circle className="mascot__spark mascot__spark--b" cx="154" cy="42" r="3" fill="#9cc6c8" />
        </g>
      </svg>
    </div>
  )
}
