"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { type ReactNode, createContext, useContext, useEffect, useState } from "react";
import { api, getToken, setToken } from "@/lib/api";

export type Me = {
  id: number; full_name: string; iin_masked: string; region_code: string | null; activity_code: string | null;
  ip_registered_on: string | null; employees_count: number; declared_income_tiyn: number | null; role: string;
};

const MeContext = createContext<Me | null>(null);
export const useMe = () => useContext(MeContext);

const LINKS = [
  ["/", "Налог"],
  ["/transactions/", "Операции"],
  ["/statements/", "Выписки"],
  ["/declarations/", "Форма 910.00"],
  ["/chat/", "Чат"],
  ["/objections/", "Обращения"],
  ["/profile/", "Профиль"],
] as const;

export default function Shell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const path = usePathname();
  const [me, setMe] = useState<Me | null>(null);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login/");
      return;
    }
    api<Me>("/me").then(setMe).catch(() => router.replace("/login/"));
  }, [router]);

  async function logout() {
    await api("/auth/session", { method: "DELETE" }).catch(() => undefined);
    setToken(null);
    router.replace("/login/");
  }

  if (!me) return <div className="main muted">Загрузка…</div>;
  return (
    <MeContext.Provider value={me}>
      <div className="layout">
        <nav className="nav">
          <div className="brand">Salyq</div>
          {LINKS.map(([href, label]) => (
            <Link key={href} href={href} className={path === href ? "active" : ""}>{label}</Link>
          ))}
          {me.role === "expert" && <Link href="/admin/" className={path === "/admin/" ? "active" : ""}>Эксперт</Link>}
          <div className="spacer" />
          <div className="small muted" style={{ padding: "8px 10px" }}>{me.full_name}</div>
          <button onClick={logout}>Выйти</button>
        </nav>
        <main className="main">{children}</main>
      </div>
    </MeContext.Provider>
  );
}
