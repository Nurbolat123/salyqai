"use client";

import { useEffect, useRef, useState } from "react";
import Shell from "@/components/Shell";
import { api, errorText } from "@/lib/api";
import { tenge } from "@/lib/format";

type Msg = { role: "user" | "assistant"; content: string; cards?: { tool: string; data: Record<string, unknown> }[] };

function Card({ tool, data }: { tool: string; data: Record<string, unknown> }) {
  if (tool === "calculate_tax") {
    const d = data as { period: string; income_tiyn: number; tax: { total_payable_tiyn: number } };
    return <div className="card small">Расчёт за {d.period}: доход {tenge(d.income_tiyn)}, налог {tenge(d.tax.total_payable_tiyn)}</div>;
  }
  if (tool === "show_transactions") {
    const d = data as { counts: Record<string, number> };
    return <div className="card small">На проверке: {d.counts.review}, предложено: {d.counts.suggested}, подтверждено: {d.counts.confirmed}</div>;
  }
  return null;
}

export default function ChatPage() {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => { api<Msg[]>("/chat/messages").then(setMsgs); }, []);
  useEffect(() => end.current?.scrollIntoView({ behavior: "smooth" }), [msgs]);

  async function send() {
    const q = text.trim();
    if (!q) return;
    setText("");
    setBusy(true);
    setMsgs((m) => [...m, { role: "user", content: q }]);
    try {
      const r = await api<{ answer: string; cards: Msg["cards"] }>("/chat/messages", { body: { text: q } });
      setMsgs((m) => [...m, { role: "assistant", content: r.answer, cards: r.cards }]);
    } catch (e) {
      setMsgs((m) => [...m, { role: "assistant", content: `Ошибка: ${errorText(e)}` }]);
    } finally {
      setBusy(false);
    }
  }

  async function escalate() {
    await api("/chat/escalate", { body: { text: text.trim() || null } });
    setNotice("Вопрос передан эксперту. Ответ появится в разделе «Обращения».");
  }

  return (
    <Shell>
      <h1>Чат</h1>
      <p className="small muted">Отвечает Salyq. Цифры берутся из вашего налогового расчёта. Сложный вопрос можно передать эксперту.</p>
      {notice && <div className="notice warn">{notice}</div>}
      <div className="chat card" style={{ minHeight: 300 }}>
        {msgs.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            {m.content}
            {m.cards?.map((c, j) => <Card key={j} {...c} />)}
          </div>
        ))}
        {busy && <div className="msg assistant muted">Думаю…</div>}
        <div ref={end} />
      </div>
      <div className="row">
        <input style={{ flex: 1 }} value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()}
               placeholder="Например: сколько мне платить налога за полугодие?" />
        <button className="primary" disabled={busy} onClick={send}>Отправить</button>
        <button onClick={escalate}>Передать эксперту</button>
      </div>
    </Shell>
  );
}
