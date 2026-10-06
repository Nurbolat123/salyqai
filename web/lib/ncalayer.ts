/**
 * Подпись ЭЦП в браузере через NCALayer (НУЦ РК), который пользователь запускает на компьютере.
 * NCALayer слушает wss://127.0.0.1:13579. Подписываем base64 данных, CMS с вложенными данными.
 */

const NCALAYER_URL = "wss://127.0.0.1:13579/";

function toBase64(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  bytes.forEach((b) => (bin += String.fromCharCode(b)));
  return btoa(bin);
}

export function signWithNcaLayer(data: string, keyType: "AUTHENTICATION" | "SIGNATURE"): Promise<string> {
  return new Promise((resolve, reject) => {
    let ws: WebSocket;
    try {
      ws = new WebSocket(NCALAYER_URL);
    } catch {
      reject(new Error("Не удалось подключиться к NCALayer. Установите и запустите NCALayer."));
      return;
    }
    const timer = setTimeout(() => {
      ws.close();
      reject(new Error("NCALayer не ответил. Проверьте, что программа запущена."));
    }, 120_000);
    ws.onerror = () => {
      clearTimeout(timer);
      reject(new Error("NCALayer не запущен. Запустите NCALayer и повторите."));
    };
    ws.onopen = () =>
      ws.send(
        JSON.stringify({
          module: "kz.gov.pki.knca.commonUtils",
          method: "createCMSSignatureFromBase64",
          args: ["PKCS12", keyType, toBase64(data), true],
        }),
      );
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data as string);
      if (msg.result?.version) return; // приветствие NCALayer
      clearTimeout(timer);
      ws.close();
      if (String(msg.code) === "200" && msg.responseObject) resolve(msg.responseObject as string);
      else reject(new Error(msg.message || "Подпись отменена"));
    };
  });
}

/** Заглушка бэкенда SALYQ_ECP_VERIFIER=dev: base64(JSON {nonce, iin, full_name}). Только разработка. */
export function devSign(nonce: string, iin: string, fullName: string): string {
  return toBase64(JSON.stringify({ nonce, iin, full_name: fullName }));
}

export const DEV_LOGIN = process.env.NEXT_PUBLIC_DEV_LOGIN === "true";

const DEV_IIN_KEY = "salyq.dev_iin";
export function rememberDevIin(iin: string): void {
  try { sessionStorage.setItem(DEV_IIN_KEY, iin); } catch { /* нет хранилища */ }
}
export function devIin(): string {
  try { return sessionStorage.getItem(DEV_IIN_KEY) ?? ""; } catch { return ""; }
}
