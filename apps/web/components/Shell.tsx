"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { createContext, useContext, useState, type ReactNode } from "react";

import { post } from "@/lib/api";
import { useKeys } from "@/lib/keys";
import type { Role, User } from "@/lib/types";
import { useResource } from "@/lib/useResource";
import { Button } from "./Button";
import { Loading } from "./Progress";
import styles from "./Shell.module.css";

const RANK: Record<Role, number> = { OFFICER: 1, ANALYST: 2, ADMIN: 3 };

const NAV: { href: string; label: string; role: Role }[] = [
  { href: "/search", label: "New Search", role: "OFFICER" },
  { href: "/records", label: "Records", role: "OFFICER" },
  { href: "/enroll", label: "Enroll", role: "ANALYST" },
  { href: "/audit", label: "Audit Log", role: "ADMIN" },
  { href: "/admin", label: "Admin", role: "ADMIN" },
];

const UserContext = createContext<User | null>(null);

export function useUser() {
  const user = useContext(UserContext);
  if (!user) throw new Error("useUser needs Shell");
  return user;
}

export const canDo = (user: User, role: Role) => RANK[user.role] >= RANK[role];

function useNightShift() {
  const [night, setNight] = useState(
    () => typeof document !== "undefined" && document.documentElement.dataset.theme === "night",
  );
  function toggle() {
    const next = !night;
    setNight(next);
    const theme = next ? "night" : "day";
    document.documentElement.dataset.theme = theme;
    document.cookie = `rkl_theme=${theme}; path=/; max-age=31536000; samesite=strict`;
  }
  return { night, toggle };
}

export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const { data: user } = useResource<User>("/auth/me");
  const { night, toggle } = useNightShift();

  useKeys({
    n: toggle,
    "/": () => router.push("/records?focus=1"),
    u: () => {
      // The search page binds U itself, to open the file picker.
      if (pathname !== "/search") router.push("/search");
    },
  });

  async function signOut() {
    await post("/auth/logout");
    window.location.assign("/sign-in");
  }

  if (!user) return <Loading what="Checking session" />;

  return (
    <UserContext.Provider value={user}>
      <a className={styles.skip} href="#main">
        Skip to content
      </a>
      <div className={styles.frame}>
        <header className={styles.header}>
          <div className={styles.headerInner}>
            <Link href="/search" className={styles.brand}>
              <span className={styles.wordmark}>Rourkela Police</span>
              <span className={styles.brandSub}>
                <span className={styles.odia} lang="or">
                  ରାଉରକେଲା ପୋଲିସ
                </span>
                <span>Face Match Portal</span>
              </span>
            </Link>
            <div className={styles.tools}>
              <div className={styles.who}>
                <span className="data">{user.officer_id}</span>
                <span className="label">{user.role}</span>
              </div>
              <Button size="small" onClick={toggle} aria-pressed={night}>
                {night ? "Day mode" : "Night shift"}
              </Button>
              <Link href="/account" className={styles.toolLink}>
                <Button as="span" size="small">
                  Account
                </Button>
              </Link>
              <Button size="small" onClick={signOut}>
                Sign out
              </Button>
            </div>
          </div>
        </header>
        <nav className={styles.nav} aria-label="Main">
          <div className={styles.tabs}>
            {NAV.filter((item) => canDo(user, item.role)).map((item) => {
              const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={active ? `${styles.tab} ${styles.active}` : styles.tab}
                  aria-current={active ? "page" : undefined}
                >
                  {item.label}
                </Link>
              );
            })}
          </div>
        </nav>
        <main id="main" className={styles.main}>
          {children}
        </main>
        <footer className={styles.foot}>
          <div className={styles.footInner}>
            <span>Internal system. Authorised officers only. All activity is logged.</span>
            <span className={styles.keys}>U upload / J K move / Enter open / Esc back / slash records / N night shift</span>
          </div>
        </footer>
      </div>
    </UserContext.Provider>
  );
}
