import type { ReactNode } from "react";

import styles from "./Heading.module.css";

/** The page title band, in English and Odia. */
export function PageTitle({ en, or, aside }: { en: string; or: string; aside?: ReactNode }) {
  return (
    <header className={styles.title}>
      <div className={styles.inner}>
        <div>
          <h1 className={styles.en}>{en}</h1>
          <p className={styles.or} lang="or">
            {or}
          </p>
        </div>
        {aside ? <div className={styles.aside}>{aside}</div> : null}
      </div>
    </header>
  );
}

export function SectionTitle({ en, or }: { en: string; or?: string }) {
  return (
    <h2 className={styles.section}>
      <span className={styles.sectionEn}>{en}</span>
      {or ? (
        <span className={styles.sectionOr} lang="or">
          {or}
        </span>
      ) : null}
    </h2>
  );
}
