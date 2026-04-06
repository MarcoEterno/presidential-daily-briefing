const ALLOWED_ORIGINS = [
  "https://marcoeterno.github.io",
  "http://localhost",
  "http://127.0.0.1",
];

const SYSTEM_PROMPT = `You are a concise geopolitical analyst answering follow-up questions about a news story from the Presidential Daily Briefing.

You have access to the story's compiled intelligence brief and source material below. Ground your answers in this material first. If the user's question requires information beyond what's provided, use your knowledge and indicate when you're going beyond the compiled sources.

Be direct and specific. Cite source names when referencing their analysis. Keep answers to 2-4 paragraphs unless the question demands more depth. Use the same authoritative tone as the briefing itself.`;

function buildUserPrompt(question, ctx) {
  let sourceList = "";
  if (ctx.source_articles && ctx.source_articles.length > 0) {
    sourceList = "\n\nSOURCE ARTICLES:\n" +
      ctx.source_articles.map(a => `- [${a.source_name}] ${a.title} (${a.url})`).join("\n");
  }

  let ttList = "";
  if (ctx.think_tank_refs && ctx.think_tank_refs.length > 0) {
    ttList = "\n\nTHINK TANK REFERENCES:\n" +
      ctx.think_tank_refs.map(r => `- [${r.source_name}] ${r.title} (${r.url})`).join("\n");
  }

  return `STORY: ${ctx.headline || ""}

BRIEFING CONTENT:
Situation: ${ctx.situation || ""}
Context & Analysis: ${ctx.context_and_analysis || ""}
Implications: ${ctx.implications || ""}

DEEP ANALYSIS:
${ctx.deep_context || "No additional analysis available."}
${sourceList}${ttList}

USER QUESTION: ${question}`;
}

function corsHeaders(origin) {
  const allowed = ALLOWED_ORIGINS.some(o => origin && origin.startsWith(o));
  return {
    "Access-Control-Allow-Origin": allowed ? origin : ALLOWED_ORIGINS[0],
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
  };
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const cors = corsHeaders(origin);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors });
    }

    if (request.method !== "POST") {
      return Response.json({ error: "Method not allowed" }, { status: 405, headers: cors });
    }

    const url = new URL(request.url);
    if (url.pathname !== "/api/ask") {
      return Response.json({ error: "Not found" }, { status: 404, headers: cors });
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return Response.json({ error: "Invalid JSON" }, { status: 400, headers: cors });
    }

    const { question, story_context } = body;
    if (!question || !story_context) {
      return Response.json({ error: "Missing question or story_context" }, { status: 400, headers: cors });
    }

    try {
      const anthropicRes = await fetch("https://api.anthropic.com/v1/messages", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-api-key": env.ANTHROPIC_API_KEY,
          "anthropic-version": "2023-06-01",
        },
        body: JSON.stringify({
          model: "claude-haiku-4-5-20251001",
          max_tokens: 1024,
          system: SYSTEM_PROMPT,
          messages: [
            { role: "user", content: buildUserPrompt(question, story_context) },
          ],
        }),
      });

      if (!anthropicRes.ok) {
        const errText = await anthropicRes.text();
        console.error("Anthropic API error:", anthropicRes.status, errText);
        return Response.json(
          { error: `API error (${anthropicRes.status})` },
          { status: 502, headers: cors }
        );
      }

      const data = await anthropicRes.json();
      const answer = data.content?.[0]?.text || "No response generated.";

      return Response.json({ answer }, { headers: cors });
    } catch (err) {
      console.error("Worker error:", err);
      return Response.json({ error: "Internal error" }, { status: 500, headers: cors });
    }
  },
};
