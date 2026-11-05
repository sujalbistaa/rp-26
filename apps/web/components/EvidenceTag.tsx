import type { ReactNode } from "react";

import styles from "./EvidenceTag.module.css";

interface Props {
  src: string;
  alt: string;
  caption: ReactNode;
  overlay?: ReactNode;
  width?: number;
  height?: number;
}

/** A photo with a 1px border and a mono caption strip underneath. */
export function EvidenceTag({ src, alt, caption, overlay, width, height }: Props) {
  return (
    <figure className={styles.tag}>
      <div className={styles.frame}>
        <img src={src} alt={alt} width={width} height={height} className={styles.image} />
        {overlay}
      </div>
      <figcaption className={styles.caption}>{caption}</figcaption>
    </figure>
  );
}
