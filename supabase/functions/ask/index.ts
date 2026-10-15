// كايرو — the shop-floor assistant.
//
// A Supabase Edge Function: the customer's phone posts a question, this looks the
// product up in the database, asks Gemini to answer *only* from that data, logs the
// exchange, and returns the answer.
//
// The Gemini key and the database service key live here as function secrets; the
// browser never sees either.
//
// Deploy: Supabase dashboard → Edge Functions → Deploy a new function → name it "ask"
// → paste this file → Deploy. Secrets needed: GEMINI_API_KEY, GEMINI_MODEL.

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const MAX_QUESTION = 400;
// Gemini answers in 1-2s, but now and then a call stalls for 20s and returns nothing.
// Rather than wait it out, a second request goes off after this long and whichever
// answers first wins. The customer never pays for the stalled one.
const HEDGE_MS = 4000;
const MAX_HISTORY = 6;

// What the assistant may and may not do. Everything it says must come from the data below.
const RULES = `
أنت "كايرو"، مساعد ذكي في محل أسواق القاهرة للمبيعات للأجهزة المنزلية والإلكترونيات.
بتتكلم مصري بسيط وودود، زي بائع شاطر ومحترم.

إزاي تكتب:
- جملتين بحد أقصى. ادخل في الإجابة على طول.
- متبدأش الرد بتحية (مفيش "أهلاً بيك" ولا "يا هلا") ومتستخدمش ألقاب زي "يا باشا" أو "يا غالي".
- اكتب عربي سليم إملائيًا. كل كلمة لازم تكون كلمة حقيقية ومكتوبة صح. متخترعش كلمات ومتدمجش كلمتين في بعض.
- متكررش اسم المنتج كامل في كل جملة، قول "الشاشة دي" أو "الغسالة دي".
- الأرقام بالأرقام الإنجليزية زي ما هي في البيانات.
- لو العميل سأل بالإنجليزي، رد بالإنجليزي كله وبنفس القواعد دي بالظبط. لو سأل بالعربي، رد بالعربي.

القواعد اللي ملهاش استثناء:
- اتكلم فقط من بيانات المنتج والمنتجات المشابهة اللي هتتبعتلك تحت. ممنوع تخمّن أو تخترع أي رقم أو مواصفة.
- لو السؤال محتاج معلومة مش موجودة في البيانات (مواعيد التوصيل، التركيب، فرع معين، مقارنة بمحل تاني)، قول إنك متأكدش واعرض إنك تنادي البائع.
- ممنوع تتفاوض على السعر أو توعد بخصم أو عرض. لو العميل طلب خصم: قوله ده قرار البائع واعرض تنادي حد.
- السعر اللي بتتعامل بيه هو سعر الكاش بس. لو سأل عن التقسيط أو القسط الشهري أو مدة التقسيط: متقولش أي رقم، قوله إن التقسيط بيتظبط مع البائع واعرض تنادي حد.
- الأسعار اللي بتقولها هي اللي في البيانات بالظبط، وقول إن سعر الفرع هو الأساس.
- لو المنتج مش متوفر، قول كده بوضوح واقترح البدائل المتاحة اللي في البيانات.
- متقولش إنك إنسان. لو سألوك، قول إنك مساعد ذكي بيساعدهم يختاروا.

في آخر ردك اكتب سطر لوحده بالشكل ده بالظبط (مش هيتعرض للعميل):
META: intent=<price|fit|compare|stock|warranty|discount|instalment|other>; answered=<yes|no>; handover=<yes|no>; refusal=<none|outside_data|discount|personal>
`.trim();

// PostgREST straight over fetch — no client library, nothing to compile on boot.
const DB = Deno.env.get("SUPABASE_URL")!;
const KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? Deno.env.get("SERVICE_KEY")!;

async function rest(path: string, method = "GET", body?: unknown) {
  const r = await fetch(`${DB}/rest/v1/${path}`, {
    method,
    headers: {
      apikey: KEY,
      Authorization: `Bearer ${KEY}`,
      "Content-Type": "application/json",
      ...(method === "POST" ? { Prefer: "return=minimal" } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`db ${r.status} ${(await r.text()).slice(0, 200)}`);
  const text = method === "GET" ? await r.text() : "";
  return { data: text ? JSON.parse(text) : null };
}

function productFacts(p: Record<string, unknown>): string {
  const specs = p.specs && typeof p.specs === "object"
    ? Object.entries(p.specs as Record<string, string>).map(([k, v]) => `${k}: ${v}`).join(" · ")
    : "";
  const cash = p.price_cash as number | null;
  return [
    `المنتج: ${p.title ?? p.name}`,
    `الموديل: ${p.model}`,
    `الماركة: ${p.brand}`,
    `القسم: ${p.category}`,
    cash ? `سعر الكاش: ${Math.round(cash).toLocaleString("en")} جنيه` : "السعر: غير متاح حاليًا (المنتج غير متوفر)",

    p.warranty ? `الضمان: ${p.warranty}` : "",
    p.country ? `بلد الصنع: ${p.country}` : "",
    specs ? `المواصفات: ${specs}` : "",
    p.description ? `وصف من الشركة: ${p.description}` : "",
  ].filter(Boolean).join("\n");
}

function parseMeta(text: string) {
  const line = text.split("\n").find((l) => l.trim().startsWith("META:")) ?? "";
  const get = (key: string) => line.match(new RegExp(`${key}=([a-z_]+)`))?.[1] ?? "";
  return {
    answer: text.split("\n").filter((l) => !l.trim().startsWith("META:")).join("\n").trim(),
    intent: get("intent") || "other",
    answered: get("answered") !== "no",
    handed_over: get("handover") === "yes",
    refusal_reason: get("refusal") && get("refusal") !== "none" ? get("refusal") : null,
  };
}

/** Reads Gemini's SSE stream and calls back with each new piece of text. */
async function streamGemini(
  url: string, body: string, signal: AbortSignal, onText: (piece: string) => void,
): Promise<string> {
  const res = await fetch(url, {
    method: "POST",
    signal,
    headers: { "content-type": "application/json", "x-goog-api-key": Deno.env.get("GEMINI_API_KEY")! },
    body,
  });
  if (!res.ok || !res.body) {
    console.error("gemini stream", res.status, (await res.text()).slice(0, 300));
    return "";
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "", full = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (!payload || payload === "[DONE]") continue;
      try {
        const j = JSON.parse(payload);
        const piece = (j?.candidates?.[0]?.content?.parts ?? [])
          .map((p: { text?: string }) => p.text ?? "").join("");
        if (piece) { full += piece; onText(piece); }
      } catch { /* a partial frame; the next read completes it */ }
    }
  }
  return full;
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  const started = Date.now();

  try {
    const { code, question, session_id, history = [], voice = false, stream = false,
            language = "ar" } = await req.json();
    if (!code || !question) {
      return Response.json({ error: "code and question are required" }, { status: 400, headers: CORS });
    }
    if (String(question).length > MAX_QUESTION) {
      return Response.json({ error: "question too long" }, { status: 400, headers: CORS });
    }

    const { data: rows } = await rest(`products?code=eq.${encodeURIComponent(code)}&select=*`);
    const product = rows?.[0];
    if (!product) {
      return Response.json({ error: "unknown product" }, { status: 404, headers: CORS });
    }

    // Products a customer would really weigh against this one. Ordering by price
    // alone offered a 10,000 EGP set to someone standing at a 71,000 EGP one, so
    // peers are taken from a band around this product's own price.
    const peers = async (lo: number, hi: number) => {
      const band = lo && hi ? `&price_cash=gte.${Math.round(lo)}&price_cash=lte.${Math.round(hi)}` : "";
      const { data } = await rest(
        `products?family=eq.${encodeURIComponent(product.family ?? "")}` +
        `&price_cash=not.is.null&code=neq.${encodeURIComponent(code)}${band}` +
        `&select=code,title,model,price_cash,specs&order=price_cash.asc&limit=6`);
      return data ?? [];
    };
    const cash = product.price_cash as number | null;
    let similar: Record<string, unknown>[] = cash ? await peers(cash * 0.55, cash * 1.6) : await peers(0, 0);
    if (cash && similar.length < 3) similar = await peers(cash * 0.3, cash * 2.5);

    const context = [
      "بيانات المنتج اللي العميل بيسأل عنه:",
      productFacts(product),
      "",
      "منتجات مشابهة متوفرة دلوقتي (استخدمها لو سأل عن أرخص أو مقارنة):",
      ...(similar ?? []).map((s) =>
        `- ${s.title} (${s.model}): كاش ${Math.round(Number(s.price_cash)).toLocaleString("en")} جنيه`
      ),
    ].join("\n");

    const model = Deno.env.get("GEMINI_MODEL") ?? "gemini-3.5-flash-lite";
    const turns = (history as { role: string; text: string }[]).slice(-MAX_HISTORY).map((t) => ({
      role: t.role === "assistant" ? "model" : "user",
      parts: [{ text: t.text }],
    }));

    const geminiBody = JSON.stringify({
      systemInstruction: { parts: [{ text: `${RULES}\n\n${context}` }] },
      contents: [...turns, { role: "user", parts: [{ text: question }] }],
      generationConfig: { temperature: 0.2, maxOutputTokens: 220 },
    });

    // One place that writes the turn away for the supervisors' reports.
    const logTurn = async (parsed: ReturnType<typeof parseMeta>) => {
      try {
        await rest("conversations", "POST", {
          session_id: session_id ?? null,
          turn: Math.floor((history as unknown[]).length / 2) + 1,
          code,
          question,
          answer: parsed.answer,
          intent: parsed.intent,
          answered: parsed.answered,
          refusal_reason: parsed.refusal_reason,
          handed_over: parsed.handed_over,
          voice_used: Boolean(voice),
          language: String(language).slice(0, 5),
          answer_ms: Date.now() - started,
          model,
        });
      } catch (e) {
        console.error("log failed", e);
      }
    };

    // ---- streaming: the page shows each word as it arrives instead of a blank wait
    if (stream) {
      const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:streamGenerateContent?alt=sse`;
      const enc = new TextEncoder();
      const body = new ReadableStream({
        async start(controller) {
          const send = (o: unknown) => controller.enqueue(enc.encode(JSON.stringify(o) + "\n"));
          const ac = new AbortController();
          let full = "", sent = 0;
          // Everything from the META line on is for us, not the customer. Holding
          // back a short tail stops a half-written "META:" reaching the screen.
          const flush = (final = false) => {
            const cut = full.indexOf("META:");
            const show = cut >= 0 ? full.slice(0, cut) : (final ? full : full.slice(0, Math.max(0, full.length - 6)));
            if (show.length > sent) { send({ t: show.slice(sent) }); sent = show.length; }
          };
          try {
            full = await streamGemini(url, geminiBody, ac.signal, () => flush());
            flush(true);
            const parsed = parseMeta(full);
            if (!parsed.answer) send({ error: "assistant unavailable" });
            else {
              send({ done: { intent: parsed.intent, handover: parsed.handed_over } });
              await logTurn(parsed);
            }
          } catch (e) {
            console.error("stream", (e as Error).message);
            send({ error: "assistant unavailable" });
          }
          controller.close();
        },
      });
      return new Response(body, {
        headers: { ...CORS, "Content-Type": "application/x-ndjson; charset=utf-8", "Cache-Control": "no-cache" },
      });
    }

    const callGemini = async (signal: AbortSignal) => {
      try {
        const res = await fetch(
          `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`,
          {
            method: "POST",
            signal,
            headers: {
              "content-type": "application/json",
              "x-goog-api-key": Deno.env.get("GEMINI_API_KEY")!,
            },
            body: geminiBody,
          },
        );
        if (!res.ok) {
          console.error("gemini", res.status, (await res.text()).slice(0, 300));
          return "";
        }
        const payload = await res.json();
        return payload?.candidates?.[0]?.content?.parts?.map((p: { text?: string }) => p.text ?? "").join("") ?? "";
      } catch (e) {
        if ((e as Error).name !== "AbortError") console.error("gemini call", (e as Error).message);
        return "";
      }
    };

    const answered = (p: Promise<string>) =>
      p.then((t) => (t.trim() ? t : Promise.reject(new Error("empty"))));

    // The free tier drops a request now and then. One retry, so a hiccup does not
    // reach a customer standing in the shop as "I cannot answer".
    const dbDone = Date.now();
    const a = new AbortController();
    const first = callGemini(a.signal);
    let hedged = false;
    let raw = await Promise.race([
      answered(first).catch(() => ""),
      new Promise<string>((r) => setTimeout(() => r(""), HEDGE_MS)),
    ]);
    if (!raw.trim()) {
      hedged = true;
      const b = new AbortController();
      const second = callGemini(b.signal);
      raw = await Promise.any([answered(first), answered(second)]).catch(() => "");
      a.abort();
      b.abort();
    }
    const firstDone = Date.now();
    const parsed = parseMeta(raw);
    if (!parsed.answer) {
      return Response.json({ error: "assistant unavailable" }, { status: 502, headers: CORS });
    }

    await logTurn(parsed);

    return Response.json(
      {
        answer: parsed.answer, intent: parsed.intent, handover: parsed.handed_over,
        timing: { db_ms: dbDone - started, gemini_ms: firstDone - dbDone,
                  hedged, total_ms: Date.now() - started },
      },
      { headers: CORS },
    );
  } catch (e) {
    console.error(e);
    return Response.json({ error: "something went wrong" }, { status: 500, headers: CORS });
  }
});
