"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, errorText, getToken } from "@/lib/api";

type Info = { current_versions: Record<string, string>; required: string[]; active: Record<string, { version: string }> };

// Тексты согласий готовит юрист; здесь — краткое описание и ссылка на полный текст.
const TEXTS: Record<string, { title: string; text: string }> = {
  pd_processing: {
    title: "Обработка персональных данных (обязательно)",
    text: "Сбор и обработка ИИН, ФИО, банковских выписок и операций для ведения налогового учёта. Данные хранятся и обрабатываются только в Казахстане (ст. 8, 12 Закона № 94-V).",
  },
  automated_processing: {
    title: "Автоматизированная разметка операций",
    text: "Сервис предлагает категории операций автоматически (правила и локальная модель в РК). Вы можете подтвердить или изменить категорию и возразить против решения — эксперт рассмотрит возражение за 3 рабочих дня (ст. 19-1).",
  },
  cross_border: {
    title: "Трансграничная передача (необязательно)",
    text: "Сложные вопросы в чате могут обрабатываться внешней моделью за пределами РК. Перед отправкой удаляются ИИН, ФИО, счета, телефоны, адреса, а суммы заменяются диапазонами (ст. 16). Без согласия чат работает на локальной модели.",
  },
};

export default function ConsentsPage() {
  const router = useRouter();
  const [info, setInfo] = useState<Info | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => api<Info>("/consents").then(setInfo).catch((e) => setError(errorText(e)));
  useEffect(() => {
    if (!getToken()) router.replace("/login/");
    else load();
  }, [router]);

  async function toggle(type: string, on: boolean) {
    setError(null);
    try {
      if (on) await api("/consents", { body: { type, version: info!.current_versions[type] } });
      else await api(`/consents/${type}`, { method: "DELETE" });
      await load();
    } catch (e) {
      setError(errorText(e));
    }
  }

  if (!info) return <div className="main muted">{error ?? "Загрузка…"}</div>;
  const missing = info.required.filter((t) => info.active[t]?.version !== info.current_versions[t]);
  return (
    <div className="main" style={{ maxWidth: 720, margin: "0 auto" }}>
      <h1>Согласия</h1>
      {error && <div className="notice error">{error}</div>}
      {Object.entries(TEXTS).map(([type, t]) => {
        const on = info.active[type]?.version === info.current_versions[type];
        return (
          <div className="card" key={type}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong>{t.title}</strong>
              <span className={`badge ${on ? "ok" : ""}`}>{on ? "дано" : "не дано"}</span>
            </div>
            <p className="small">{t.text}</p>
            <p className="small muted">Редакция {info.current_versions[type]}</p>
            {on ? <button onClick={() => toggle(type, false)}>Отозвать</button>
                : <button className="primary" onClick={() => toggle(type, true)}>Согласен</button>}
          </div>
        );
      })}
      <button className="primary" disabled={missing.length > 0} onClick={() => router.replace("/")}>Продолжить</button>
    </div>
  );
}
