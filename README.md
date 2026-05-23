# Artisan · Evidence-First Outbound

> An auditable outbound strategy system. Every commercial claim it produces is
> grounded in a public evidence snippet, verified by NLI, and tagged with its
> verification status — or explicitly marked as unknown.

This is a take-home for the Applied AI role at
[Artisan](https://www.ycombinator.com/companies/artisan/jobs/Y0EbjIC-applied-ai).

The goal is **not** a generic RAG email generator. The goal is provenance,
validation, planning, and refusal as first-class concerns of an AI SDR
architecture — the discipline the product would need at scale.

---

## What it does

Two flows that share the same evidence substrate:

1. **Sender research** — given a sender homepage, infer a value proposition and
   a structured ICP (industries, size bands, likely buyers, common triggers,
   negative ICP). Every field carries its confidence and its supporting
   `observation_id` list.

2. **Target evaluation & outbound drafting** — given the inferred sender
   artifacts, a target homepage, and a recipient persona (role + seniority),
   the system researches the target from public sources at runtime, evaluates
   fit, picks two angles, drafts two emails (pain-led + trigger-led), verifies
   every factual claim with NLI, and returns a full **claim map**.

The same evidence pipeline is used for both flows. Only the synthesis layer differs.

---

## Architecture

A LangGraph state machine that calls strong open-source components for the
infrastructure work, with **one explicit agentic decision point** (the
Planner). No open-ended autonomous loops.

```text
SENDER GRAPH
START → sender_crawl → sender_extract → sender_validate → planner
                                                       ├─ fetch_more  → sender_crawl (explicit URLs, max once)
                                                       ├─ continue    → sender_synthesize → END
                                                       └─ stop        → END

TARGET GRAPH
START → target_crawl → target_extract → target_validate → planner
                                                       ├─ fetch_more   → target_crawl (max once)
                                                       ├─ web_search   → external_enrichment → strategy
                                                       ├─ continue     → strategy
                                                       └─ stop         → END

       strategy → writer → claim_extract → claim_verify
                                                       ├─ needs repair → repair (max once) → analytics → END
                                                       └─ ok           → analytics → END
```

### Key design choices

- **Lean ingestion stack: `httpx` + `trafilatura` + lazy `playwright`.**
  Static fetch with httpx covers ~95% of B2B SaaS marketing sites. trafilatura
  turns HTML into clean markdown without LLM dependencies. When the static
  body is too thin (JS-rendered SPA), we re-fetch the page with a single
  shared Playwright browser. Crawl frontier is a best-first BFS scored by
  keyword overlap (about / pricing / customers / careers / ...). Raw HTML is
  cached on disk by sha256(url) so re-runs are free.
- **Deterministic provenance is owned by us.** We section the markdown
  structurally (H1..H4 + paragraph blocks) and emit
  `section_id = sha1(url, heading, char_start)`. The LLM extractor is only
  allowed to reference existing section_ids — invented ids are rejected.
- **LangGraph for orchestration.** Pipeline stages are graph nodes with a
  typed shared state. Routing is conditional on the Planner's typed output.
  All business logic stays in `pipeline/` and `synthesis/`; the graph is the
  glue.
- **Single agentic decision point.** The Planner runs at most twice per flow
  (post-extract + post-fetch_more), and can choose: `continue`, `fetch_more`,
  `web_search`, `proceed_low_confidence`, or `stop`.
- **Instructor + Pydantic for structured outputs.** Every LLM call returns a
  validated Pydantic model; Instructor retries on schema failure internally.
  No hand-rolled JSON repair prompts.
- **Real NLI, selectively applied.** `cross-encoder/nli-deberta-v3-xsmall`
  is the entailment judge. We skip NLI on observation kinds that are usually
  *directly stated* on the page (pricing, integrations, tech stack) when the
  extractor was confident, and reserve it for *inferred / high-risk* claims
  (pain points, triggers, hiring, funding, leadership). Claims in emails are
  always NLI-verified.
- **External search is enrichment only, behind a typed abstraction.**
  `ExternalSignalProvider` has a `Disabled` default and an
  `OpenAIWebSearchProvider` impl (Responses API `web_search` tool). No Tavily,
  no DuckDuckGo scraping. Only the target flow can call it, and only when the
  Planner says so. Protected platforms (LinkedIn, Maps, X, ...) are always
  filtered.
- **One bounded repair pass.** After claim verification, if any claim is
  unsupported or contradicted, we run the repair node once (rewrite or drop).
  No loops.
- **Defensive refusal.** If the Planner returns `stop`, the target flow ends
  early with `fit_level = none` and `contact_decision = skip`. The UI shows
  this — we never silently invent a strategy.

---

## Tech stack

| Layer          | Choice |
| ---            | --- |
| Backend        | FastAPI + LangGraph state machines |
| Crawling       | `httpx` + `trafilatura` + lazy `playwright` (Chromium fallback for SPAs) |
| LLM            | OpenAI via **Instructor** (`gpt-4o-mini` by default) |
| NLI            | `sentence-transformers` CrossEncoder (DeBERTa-v3-xsmall, CPU) |
| Embeddings     | `sentence-transformers/all-MiniLM-L6-v2` (CPU) |
| Storage        | SQLite (provenance) + on-disk HTML cache keyed by sha256(url) |
| Observability  | MLflow (file backend, self-contained) |
| External search| OpenAI Responses API `web_search` tool (optional, planner-gated) |
| Frontend       | Next.js 14, Tailwind, shadcn/ui-style primitives, Framer Motion |
| Streaming      | Server-Sent Events for live stage updates |

---

## Run it

### 1. With Docker (recommended)

```bash
cp .env.example .env
# put your OPENAI_API_KEY in .env

docker compose up --build
```

Then open:

- App:    http://localhost:3000
- API:    http://localhost:8000/docs
- MLflow: http://localhost:5000

The backend image is built on top of `mcr.microsoft.com/playwright/python` so
Chromium and its system deps are ready for the Playwright JS-render fallback.
The first build also pre-downloads the NLI + embedding models so the first
request is fast.

### 2. Local dev (no Docker)

```bash
# backend
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium         # only needed if outside the Playwright Docker base image
export OPENAI_API_KEY=sk-...
uvicorn app.main:app --reload --port 8000

# frontend (new shell)
cd frontend
npm install
npm run dev
```

---

## Walkthrough

1. **Sender Research** — enter your homepage URL. The crawler picks the most
   relevant pages by keyword scoring. Watch the pipeline stages stream live.

2. **ICP & Value Proposition** — two side-by-side cards. Each field can be
   expanded to inspect the observations that support it (with section
   snippets and source URL). Confidence is a deterministic function of
   evidence count and observation confidence — not invented by the model.

3. **Outreach Generation** — fit assessment, contact decision, persona
   alignment, and two emails. Each claim has a verification badge:
   `supported` / `repaired` / `neutral` / `unsupported` / `contradicted`,
   with the NLI score and expandable evidence per claim.

4. **Analytics** — every metric in the spec: latency, tokens, cost, pages,
   sections, observations, validation rate, evidence compression, claims
   supported / unsupported / repaired, angle overlap, pipeline timeline,
   planner decisions, and the full claim chain (observation → strategy angle
   → email claim → verification status).

---

## Metric definitions

```
compression_ratio            = raw_cleaned_chars / evidence_chars_used
claim_support_rate           = (entailed + repaired) claims / total claims
unsupported_claim_rate       = unsupported claims / total claims
observation_validation_rate  = entailed observations / total observations
angle_overlap                = cosine(embedding(email_a.body), embedding(email_b.body))
```

The repair loop is triggered when any claim is `unsupported` or
`contradicted`, and angle-divergence repair triggers when overlap exceeds
`0.78`.

---

## Repository layout

```
.
├── backend/
│   └── app/
│       ├── config.py                  # pydantic-settings
│       ├── schemas.py                 # enums + Pydantic models (typed value spaces)
│       ├── db.py                      # SQLite schema + helpers
│       ├── progress.py                # async progress channel for SSE
│       ├── orchestrator.py            # thin wrapper around the LangGraph runners
│       ├── graph/
│       │   ├── state.py               # typed FlowState (TypedDict)
│       │   ├── nodes.py               # all graph nodes
│       │   ├── sender_graph.py        # sender state machine
│       │   └── target_graph.py        # target state machine
│       ├── pipeline/
│       │   ├── crawl.py               # httpx + trafilatura BFS crawl + deterministic markdown sectioning
│       │   ├── extract.py             # LLM observation extractor (Instructor)
│       │   ├── validate.py            # selective NLI validation
│       │   └── planner.py             # the single agentic decision point
│       ├── synthesis/
│       │   ├── sender.py              # ICP + value proposition (sender)
│       │   ├── strategy.py            # fit + strategy + persona alignment (target)
│       │   ├── writer.py              # pain-led + trigger-led emails
│       │   ├── claim_extract.py       # deterministic claim consolidation
│       │   ├── verify.py              # NLI claim verification + repair
│       │   └── overlap.py             # angle overlap + divergence repair
│       ├── services/
│       │   ├── llm.py                 # Instructor wrapper, usage + cost accounting
│       │   ├── nli.py                 # CrossEncoder NLI
│       │   ├── embed.py               # sentence-transformers embeddings
│       │   └── external.py            # ExternalSignalProvider (disabled / openai_web_search)
│       ├── observability/tracker.py   # MLflow run + stage timeline + metrics
│       └── routes/
│           ├── flows.py               # POST + SSE endpoints
│           └── dashboard.py           # read-only audit endpoints
├── frontend/                          # unchanged: Next.js multi-step workflow
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## Trade-offs and explicit non-goals

- **No autonomous loop.** Open-ended ReAct loops produce non-auditable
  evidence. The Planner runs at most twice and the repair pass runs at most
  once per email.
- **No vector DB.** With ~10 pages per company and ~50 sections per run, an
  in-memory dict keyed on `section_id` is faster, fully auditable, and avoids
  introducing an opaque retrieval layer between evidence and synthesis.
- **No RAGAS.** Claim quality is measured deterministically: NLI entailment
  scores plus exact ref-to-evidence mapping.
- **Selective NLI.** Running NLI on every observation is wasteful when the
  extractor was already confident and the kind is directly stated. We focus
  validation on inferred / high-risk kinds.

---

## Health check

```bash
curl http://localhost:8000/api/v1/health
# { "ok": true, "llm_model": "gpt-4o-mini", "embedding_model": "...", "nli_model": "..." }
```
