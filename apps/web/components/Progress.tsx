import styles from "./Progress.module.css";

/** No spinners. A 2px bar that fills, plus the step name. */
export function Progress({ step, fraction }: { step: string; fraction: number }) {
  return (
    <div className={styles.wrap} role="status" aria-live="polite">
      <div className={styles.track}>
        <div className={styles.fill} style={{ width: `${fraction * 100}%` }} />
      </div>
      <p className="data">{step}</p>
    </div>
  );
}

export function Loading({ what = "LOADING" }: { what?: string }) {
  return (
    <div className={styles.loading}>
      <Progress step={what} fraction={0.5} />
    </div>
  );
}
