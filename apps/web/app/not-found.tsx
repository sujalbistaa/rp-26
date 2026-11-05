import Link from "next/link";

import { Button } from "@/components/Button";
import styles from "./status.module.css";

export default function NotFound() {
  return (
    <main className={styles.page}>
      <p className="data">404</p>
      <h1 className={styles.title}>No such page.</h1>
      <Link href="/search" className={styles.link}>
        <Button as="span">Back to New Search</Button>
      </Link>
    </main>
  );
}
