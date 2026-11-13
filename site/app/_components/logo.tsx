type MarkProps = { id: string; className?: string };

function Petals({ id }: { id: string }) {
  return (
    <>
      <defs>
        <linearGradient id={`${id}-a`} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#2EE59D" />
          <stop offset="1" stopColor="#0E8A5F" />
        </linearGradient>
        <linearGradient id={`${id}-b`} x1="1" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#22C3E6" />
          <stop offset="1" stopColor="#1667C9" />
        </linearGradient>
        <linearGradient id={`${id}-c`} x1="0" y1="1" x2="1" y2="0">
          <stop offset="0" stopColor="#F5B83D" />
          <stop offset="1" stopColor="#F0703A" />
        </linearGradient>
      </defs>
      <g style={{ isolation: "isolate" }} opacity="0.92">
        {(["a", "b", "c"] as const).map((key, i) => (
          <ellipse
            key={key}
            cx="48"
            cy="48"
            rx="40"
            ry="16"
            transform={`rotate(${-30 + i * 60} 48 48)`}
            fill={`url(#${id}-${key})`}
            opacity="0.85"
          />
        ))}
      </g>
    </>
  );
}

export function Mark({ id, className }: MarkProps) {
  return (
    <svg viewBox="0 0 96 96" className={className} aria-hidden="true">
      <Petals id={id} />
    </svg>
  );
}

export function Logo({ id, className }: MarkProps) {
  return (
    <svg viewBox="0 0 340 104" className={className} role="img" aria-label="ehor">
      <g transform="translate(4 4)">
        <Petals id={id} />
      </g>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="9"
        strokeLinecap="round"
        strokeLinejoin="round"
        transform="translate(124 0)"
      >
        <path d="M2 68h36a19 19 0 1 0-5.6 13.4" />
        <path d="M62 28v60M62 66a18 18 0 0 1 36 0v22" />
        <circle cx="136" cy="68" r="19" />
        <path d="M176 48v40M176 70a20 20 0 0 1 24-19" />
      </g>
    </svg>
  );
}
