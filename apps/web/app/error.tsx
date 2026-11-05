"use client";

import { Button } from "@/components/Button";
import styles from "./status.module.css";

export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <main className={styles.page}>
      <p className="data">500</p>
      <h1 className={styles.title}>Something broke on our side.</h1>
      <Button onClick={reset}>Try again</Button>
    </main>
  );
}
