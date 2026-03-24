import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const OPENROUTER_API_KEY = Deno.env.get("OPENROUTER_API_KEY") ?? "";
const EMBED_MODEL = "openai/text-embedding-3-small";

function getExpectedKey(): string {
  const explicit = Deno.env.get("BRAIN_API_KEY") ?? "";
  if (explicit) return explicit;
  const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? "";
  const m = supabaseUrl.match(/https:\/\/([a-zA-Z0-9]+)\.supabase\.co/);
  if (m) return `brain-${m[1]}`;
  return "";
}

serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", {
      headers: {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "authorization, content-type",
      },
    });
  }

  const expectedKey = getExpectedKey();
  if (expectedKey) {
    const authHeader = req.headers.get("authorization") ?? "";
    if (!authHeader.includes(expectedKey)) {
      return new Response(JSON.stringify({ error: "unauthorized" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      });
    }
  }

  const { input } = await req.json();
  const texts = Array.isArray(input) ? input : [input];

  const response = await fetch("https://openrouter.ai/api/v1/embeddings", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${OPENROUTER_API_KEY}`,
    },
    body: JSON.stringify({ model: EMBED_MODEL, input: texts }),
  });

  if (!response.ok) {
    const err = await response.text();
    return new Response(JSON.stringify({ error: err }), {
      status: response.status,
      headers: { "Content-Type": "application/json" },
    });
  }

  const data = await response.json();
  const embeddings = data.data.map(
    (d: { embedding: number[] }) => d.embedding
  );

  return new Response(JSON.stringify({ embeddings }), {
    headers: {
      "Content-Type": "application/json",
      "Access-Control-Allow-Origin": "*",
    },
  });
});
