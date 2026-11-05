import styles from "./Stamp.module.css";

export function Stamp({ word }: { word: "PURGED" }) {
  return <span className={`${styles.chip} ${styles.purged}`}>{word}</span>;
}

/** Colour never carries the band alone: the word is always there. */
export function BandLabel({ band }: { band: "STRONG" | "POSSIBLE" | "WEAK" }) {
  return <span className={`${styles.chip} ${styles[band.toLowerCase()]}`}>{band}</span>;
}
