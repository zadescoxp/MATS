import Image from "next/image";

interface PersonaAvatarProps {
  name: string;
  src?: string;
  size?: number;
}

export function PersonaAvatar({ name, src, size = 40 }: PersonaAvatarProps) {
  return (
    <div
      className="relative grid shrink-0 place-items-center overflow-hidden rounded-full bg-surface text-xs font-semibold text-muted ring-1 ring-border"
      style={{ width: size, height: size }}
      aria-label={`${name} persona`}
    >
      {src ? (
        <Image src={src} alt="" fill sizes={`${size}px`} className="object-cover" />
      ) : (
        name.slice(0, 2).toUpperCase()
      )}
    </div>
  );
}
