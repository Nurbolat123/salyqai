"use client";

import { useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { api } from "@/lib/api";
import { date } from "@/lib/format";

type Objection = { id: number; subject_ref: string; text: string; status: string; due_at: string; resolution: string | null; created_at: string };
type Question = { id: number; text: string; answer: string | null; created_at: string };

export default function ObjectionsPage() {
  const [objs, setObjs] = useState<Objection[]>([]);
  const [qs, setQs] = useState<Question[]>([]);
  useEffect(() => {
    api<Objection[]>("/objections").then(setObjs);
    api<Question[]>("/chat/questions").then(setQs);
  }, []);
  return (
    <Shell>
      <h1>Обращения</h1>
      <h2>Возражения</h2>
      {objs.length === 0 && <p className="muted">Возражений нет. Возразить можно на экране «Как посчитано».</p>}
      {objs.map((o) => (
        <div className="card" key={o.id}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="small muted">от {date(o.created_at)}</span>
            <span className="badge">{o.status === "open" ? `ответ до ${date(o.due_at)}` : "рассмотрено"}</span>
          </div>
          <p>{o.text}</p>
          {o.resolution && <div className="notice warn"><b>Ответ эксперта:</b> {o.resolution}</div>}
        </div>
      ))}
      <h2>Вопросы эксперту</h2>
      {qs.length === 0 && <p className="muted">Вопросов нет. Задать вопрос можно из чата.</p>}
      {qs.map((q) => (
        <div className="card" key={q.id}>
          <p style={{ whiteSpace: "pre-wrap" }}>{q.text}</p>
          {q.answer ? <div className="notice warn"><b>Ответ:</b> {q.answer}</div> : <span className="badge">ждёт ответа</span>}
        </div>
      ))}
    </Shell>
  );
}
