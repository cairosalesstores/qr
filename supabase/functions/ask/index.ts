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

import { createClient } from "jsr:@supabase/supabase-js@2";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

const MAX_QUESTION = 400;
const MAX_HISTORY = 6;

// What the assistant may and may not do. Everything it says must come from the data below.
const RULES = `
أنت "كايرو"، مساعد ذكي في محل أسواق القاهرة للمبيعات للأجهزة المنزلية والإلكترونيات.
بتتكلم مصري بسيط وودود، زي بائع شاطر ومحترم، وبتختصر: جملتين أو تلاتة بحد أقصى.

القواعد اللي ملهاش استثناء:
- اتكلم فقط من بيانات المنتج والمنتجات المشابهة اللي هتتبعتلك تحت. ممنوع تخمّن أو تخترع أي رقم أو مواصفة.
- لو السؤال محتاج معلومة مش موجودة في البيانات (مواعيد التوصيل، التركيب، فرع معين، مقارنة بمحل تاني)، قول إنك متأكدش واعرض إنك تنادي البائع.
- ممنوع تتفاوض على السعر أو توعد بخصم أو عرض. لو العميل طلب خصم: قوله ده قرار البائع واعرض تنادي حد.
- السعر اللي بتتعامل بيه هو سعر الكاش بس. لو سأل عن التقسيط أو القسط الشهري أو مدة التقسيط: متقولش أي رقم، قوله إن التقسيط بيتظبط مع البائع واعرض تنادي حد.
- الأسعار اللي بتقولها هي اللي في البيانات بالظبط، وقول إن سعر الفرع هو الأساس.
- لو المنتج مش متوفر، قول كده بوضوح واقترح البدائل المتاحة اللي في البيانات.
- متقولش إنك إنسان. لو سألوك، قول إنك مساعد ذكي بيساعدهم يختاروا.

في آخر ردك اكتب سطر لوحده بالشكل ده بالظبط (مش هيتعرض للعميل):
META: intent=<price|fit|compare|stock|warranty|discount|other>; answered=<yes|no>; handover=<yes|no>; refusal=<none|outside_data|discount|personal>
`.trim();

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

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  const started = Date.now();

  try {
    const { code, question, session_id, history = [], voice = false } = await req.json();
    if (!code || !question) {
      return Response.json({ error: "code and question are required" }, { status: 400, headers: CORS });
    }
    if (String(question).length > MAX_QUESTION) {
      return Response.json({ error: "question too long" }, { status: 400, headers: CORS });
    }

    const db = createClient(
      Deno.env.get("SUPABASE_URL")!,
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? Deno.env.get("SERVICE_KEY")!,
    );

    const { data: product } = await db.from("products").select("*").eq("code", code).single();
    if (!product) {
      return Response.json({ error: "unknown product" }, { status: 404, headers: CORS });
    }

    // Similar products in stock, so it can answer "is there a cheaper one?" honestly
    const { data: similar } = await db
      .from("products")
      .select("code, title, model, price_cash, specs")
      .eq("family", product.family)
      .not("price_cash", "is", null)
      .neq("code", code)
      .order("price_cash", { ascending: true })
      .limit(6);

    const context = [
      "بيانات المنتج اللي العميل بيسأل عنه:",
      productFacts(product),
      "",
      "منتجات مشابهة متوفرة دلوقتي (استخدمها لو سأل عن أرخص أو مقارنة):",
      ...(similar ?? []).map((s) =>
        `- ${s.title} (${s.model}): كاش ${Math.round(s.price_cash).toLocaleString("en")} جنيه`
      ),
    ].join("\n");

    const model = Deno.env.get("GEMINI_MODEL") ?? "gemini-3.1-flash-lite";
    const turns = (history as { role: string; text: string }[]).slice(-MAX_HISTORY).map((t) => ({
      role: t.role === "assistant" ? "model" : "user",
      parts: [{ text: t.text }],
    }));

    const res = await fetch(
      `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-goog-api-key": Deno.env.get("GEMINI_API_KEY")!,
        },
        body: JSON.stringify({
          systemInstruction: { parts: [{ text: `${RULES}\n\n${context}` }] },
          contents: [...turns, { role: "user", parts: [{ text: question }] }],
          generationConfig: { temperature: 0.3, maxOutputTokens: 400 },
        }),
      },
    );

    if (!res.ok) {
      const detail = await res.text();
      console.error("gemini", res.status, detail.slice(0, 300));
      return Response.json({ error: "assistant unavailable" }, { status: 502, headers: CORS });
    }

    const payload = await res.json();
    const raw = payload?.candidates?.[0]?.content?.parts?.map((p: { text?: string }) => p.text ?? "").join("") ?? "";
    const parsed = parseMeta(raw);
    if (!parsed.answer) {
      return Response.json({ error: "assistant unavailable" }, { status: 502, headers: CORS });
    }

    // Log it for the supervisors' reports. A failure here must not cost the customer an answer.
    try {
      await db.from("conversations").insert({
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
        answer_ms: Date.now() - started,
        model,
      });
    } catch (e) {
      console.error("log failed", e);
    }

    return Response.json(
      { answer: parsed.answer, intent: parsed.intent, handover: parsed.handed_over },
      { headers: CORS },
    );
  } catch (e) {
    console.error(e);
    return Response.json({ error: "something went wrong" }, { status: 500, headers: CORS });
  }
});
