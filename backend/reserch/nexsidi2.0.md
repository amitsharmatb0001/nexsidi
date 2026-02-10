# NexSidi v2.0 architecture deep-dive

**NexSidi v2.0 should ship as a Python-native stack — FastAPI, LangGraph, PostgreSQL with pgvector, Redis/Celery — deployed on GCP Cloud Run, buildable in 30 days with ₹1.14L credits covering 4–9 months of infrastructure.** This report dissects how Cursor and Windsurf actually work under the hood, which Netflix patterns apply at your scale (most don't), whether 2M-token context windows kill RAG (they don't), and delivers concrete buy-vs-build decisions for every layer. The goal: a production architecture that respects the "working software > perfect code" philosophy while leaving clear upgrade paths for scale.

---

## How Cursor and Windsurf actually index your codebase

The "instant index" experience in these AI coding editors hides fundamentally different architectural bets. **Cursor indexes to the cloud; Windsurf indexes locally.**

Cursor's pipeline begins on the client, where code is chunked into syntactic segments and encrypted. These chunks are uploaded to Cursor's servers, where **GPU-accelerated embedding models** compute vectors — not locally. The vectors land in **Turbopuffer**, a multi-tenant vector database (migrated from Qdrant during an emergency outage, confirmed by cofounder Sualeh Asif). Documentation embeddings go to Pinecone separately. No raw source code is stored server-side — only embeddings plus obfuscated metadata like segment-encrypted file paths and line ranges. The system holds **hundreds of terabytes** of index data.

Speed comes from a **Merkle tree synchronization engine**. Every file gets a cryptographic hash; folder hashes derive from children. A sync cycle runs every ~3 minutes, comparing client and server trees to identify changed files. Only deltas are re-uploaded and re-embedded. Embeddings are cached by content hash, so identical code across users hits cache. For teams, Cursor computes a **simhash** (locality-sensitive hash) of the new user's Merkle tree, finds matching indexes in the same organization — since clones of the same repo share **92% similarity** — and copies the existing index instantly. This drops median time-to-first-query from **7.87 seconds to 525 milliseconds**.

Windsurf takes the opposite approach. Local indexing is the default, with remote indexing available only for enterprise tiers. More radically, Windsurf's **Riptide engine** (formerly Cortex) isn't embedding-based at all — it's a **specialized LLM that evaluates snippet relevance by reading raw code**. Codeium claims **3× better retrieval accuracy** than state-of-the-art embedding systems and can process up to **100 million tokens** without quality degradation. This is architecturally novel: instead of computing vector similarity, they run a trained model to judge whether a code snippet answers the retrieval query.

**NexSidi takeaway:** For project-scale codebases (thousands of files, not millions), pgvector with embedding-based retrieval is the pragmatic starting point. Riptide's LLM-based retrieval is fascinating but requires dedicated GPU infrastructure you don't need yet.

---

## Multi-file edits use fine-tuned models, not deterministic diffs

When Cursor applies a change across 50 files, it does not use a diff algorithm. Cofounder Aman Sanger confirmed: **"Contrary to popular perception, the diff matching in Apply is not a deterministic algorithm."** It's a custom fine-tuned LLM.

The architecture is two-phase. First, a **frontier model** (Claude, GPT-4) plans the change and outputs a high-level sketch. Second, a **custom "fast-apply" model** — fine-tuned Llama-3-70B — takes the current file plus the sketch and rewrites the entire file. Cursor found that LLMs struggle with diff-formatted output because diffs force compressed reasoning, are out-of-distribution for most training data, and require accurate line counting. For files under 400 lines, full-file rewrite outperforms diff-based approaches.

The speed magic is **speculative edits**, a variant of speculative decoding. Since most of the output will be identical to the existing file, the current code serves as a strong draft. Fireworks AI's inference engine validates how much of the existing file matches what the model would generate at temperature=0, then only generates the novel portions. This achieves **~13× speedup** over vanilla inference, hitting roughly **1,000 tokens/second** on the 70B model.

Cursor Composer (now Cursor 2.0) uses a proprietary **Mixture-of-Experts model trained with reinforcement learning**, generating at **250 tokens/second**. For multi-file edits, it runs an agentic loop — planning, executing file edits sequentially, running terminal commands to verify, iterating until complete. Up to **8 parallel agents** can work simultaneously, isolated via Git worktrees. The **Anyrun orchestrator**, written in Rust, launches cloud agents in Firecracker VMs on AWS EC2.

Windsurf Cascade takes a different approach with a **dedicated background planning agent** that continuously refines a long-term plan while the main model handles short-term actions. Cascade chains **up to 20 tool calls per prompt** and maintains "Flow Awareness" — tracking all user actions (edits, terminal commands, clipboard) to adapt in real time.

**NexSidi takeaway:** The two-phase pattern (planning model → execution model) is directly applicable. Your code generation agent should use a frontier model for architecture decisions and a faster model for file generation. LangGraph's graph structure maps perfectly to this planning-then-execution flow.

---

## Cursor and Windsurf's tech stacks under the hood

Both editors are **VS Code forks** (Electron + TypeScript) on the client. The server-side diverges significantly.

Cursor's backend is **TypeScript for business logic, Rust for performance-critical components**. The indexing engine, the Anyrun agent orchestrator, and other hot paths are Rust, invoked from TypeScript via a Node.js bridge. The codebase spans ~25,000 files and ~7 million lines across ~50 engineers. Infrastructure runs on **AWS for CPU, Azure for GPU inference**, using tens of thousands of NVIDIA H100 GPUs. Key services include Turbopuffer (primary vector DB), WarpStream (Kafka-compatible streaming), Terraform, Datadog, Sentry, and Stripe. The system handles **1M+ transactions per second** at peak, with 100× growth in 12 months.

Windsurf/Codeium evolved from **Exafunction**, a GPU virtualization company. Their deep infrastructure DNA means they built custom GPU kernels and compilers for transformer inference. They train and deploy proprietary models alongside fine-tuned Llama variants. CEO Varun Mohan stated they've "served Llama models to thousands of engineers using a single GPU." They process **100 billion tokens of code per day**.

---

## Emergent behavior runs on agentic loops with semantic search

"Emergent" file discovery — where the AI edits files it wasn't explicitly asked about — isn't magic. It's a systematic agentic loop with rich tooling.

Cursor's Agent mode (the default since 2025) has codebase-wide semantic search via Turbopuffer embeddings, plus grep/ripgrep for exact string matching. When executing a task, the agent encounters import statements, error messages, test failures, or type mismatches that point to other files, and it **follows those leads autonomously**. The model decides which tools to use — some prefer grep over semantic search, so the agent harness is tuned per frontier model.

Windsurf's approach is more proactive. Flow Awareness means Cascade is always watching what you do. If you modify a function parameter, it **automatically identifies and updates all call sites** across the codebase using Riptide's LLM-based code search. The dedicated planning agent maintains a running todo list, adapting the plan as the user's actions change context.

**NexSidi takeaway:** Your agents should have access to project-wide search tools (file listing, grep, semantic search via pgvector) and operate in loops where they can discover dependencies. LangGraph's conditional edges enable this naturally — an agent node can route to a "search and discover" node before proceeding to code generation.

---

## Netflix patterns that matter at your scale (and the ones that don't)

Netflix handles **10 million concurrent connections** routing to **1 million+ requests per second** across 100+ backend clusters. Their patterns form a defense-in-depth stack, but most are overkill below 10,000 concurrent users.

**Zuul 2** is Netflix's API gateway, rebuilt on Netty's non-blocking I/O to handle thousands of persistent connections per instance without thread exhaustion. The old Zuul 1 used thread-per-connection and collapsed under load. Netflix runs **80+ Zuul clusters** handling 5.5 million persistent WebSocket/SSE connections at peak. **For NexSidi: use Cloud Run's built-in load balancer or Nginx. You won't hit 1,000 concurrent connections for months.**

**Hystrix circuit breakers** prevent cascading failures by fast-failing requests to unhealthy services. It entered maintenance mode in 2018; **Resilience4j** is the modern replacement — lightweight, modular, composable via function decorators. **For NexSidi: circuit breakers are worth adding even at small scale.** They're cheap to implement and prevent one failing LLM provider from taking down your entire pipeline. Wrap your LLM API calls in Resilience4j-style retry/circuit-breaker logic (Python's `tenacity` library provides similar patterns).

**The sidecar pattern** (Netflix Prana) deploys a helper process alongside each microservice to provide service discovery, circuit breaking, and configuration without language-specific libraries. It evolved into modern service meshes like Istio and Envoy. **For NexSidi: skip entirely. You're running a monolith on Cloud Run.**

---

## GraphQL Federation is an organizational solution, not a technical one

Netflix migrated from Falcor (their homegrown JSON graph library) to **GraphQL Federation** using their open-source DGS framework. Their Studio Edge graph spans **150+ subgraphs, 3,000+ types, and 200+ participating teams**. Query planning overhead stays **under 10 milliseconds**.

But here's the critical insight: **Federation solves team coordination problems, not data fetching problems.** Meta (where GraphQL was invented) still runs a monolithic GraphQL API at billions-of-users scale. Federation becomes necessary when the API team is an organizational bottleneck — when 5+ independent teams need to ship API changes without coordinating deploys.

For NexSidi with a single founder and <1M users, the recommended progression is clear:

- **Now**: Simple FastAPI REST endpoints, possibly a monolithic GraphQL server if you want typed APIs
- **At 3–5 teams**: Backend-for-Frontend (BFF) pattern — one aggregation layer per client type
- **At 10+ teams, 20+ services**: Schema stitching or lightweight federation
- **At Netflix scale**: Full GraphQL Federation with schema registry and dedicated platform team

**For NexSidi: a single FastAPI backend with well-organized route modules is the right answer. GraphQL Federation would consume your entire 30-day sprint just in infrastructure setup.**

---

## 2M-token context windows don't kill RAG — they make it more strategic

Gemini 2.0 Pro offers a **2 million token** context window — roughly 3,400 pages of text. The temptation is to dump entire codebases into context and skip retrieval entirely. The data says otherwise.

A comprehensive study by Li et al. found that while long-context models outperform RAG in average accuracy, **RAG costs ~1,250× less per query**. An Elasticsearch benchmark measured RAG at **$0.00008 per request** versus **$0.10 for full-context queries**. At 10,000 daily queries, that's $0.80/day versus $1,000/day. Filling a 2M-token Gemini 2.0 Pro context costs **$2.50–$5.00 in input tokens alone** per request.

Beyond cost, accuracy degrades with context length. The **"lost in the middle" problem** (Stanford/UW, published in TACL) shows a U-shaped performance curve: LLMs handle information at the beginning and end of context well, but suffer **>30% accuracy degradation** for information buried in the middle. This holds across models, even those explicitly designed for long context. Additionally, most models start degrading after **32K–64K tokens** in practice, regardless of their stated maximum.

Google's **context caching** helps for repeated queries against the same documents — cache reads cost **10% of base input price** — but the fundamental economics favor a hybrid approach: RAG for retrieval and filtering, then feed relevant chunks into long-context models for deep reasoning.

**NexSidi takeaway:** Use pgvector-based RAG to retrieve relevant project context (requirements docs, existing code, user history), then feed curated context to your LLM calls. Reserve long-context for specific scenarios like analyzing an entire generated codebase for QA review. Gemini 2.0 Flash at **$0.10/M input tokens** is excellent for bulk processing; Claude Sonnet 4.5 at **$3/M** for complex agent reasoning.

---

## Claude's Computer Use is literally a VLM taking screenshots in a loop

Anthropic's Computer Use is exactly what it sounds like: **a Vision Language Model running in an agentic loop**, taking screenshots, analyzing them, executing mouse/keyboard actions, and taking another screenshot.

The loop cycle is: user instruction → screenshot → VLM analysis → determine action (click coordinates, type text, scroll) → execute action → new screenshot → analyze result → repeat. The model runs inside a **Docker container** with a virtual X11 display, a lightweight Linux desktop (Mutter window manager), and pre-installed apps. Three core tools provide the interface: a `computer` tool for screen interaction, a `text_editor` for file operations, and `bash` for command execution.

The remarkable training insight: Anthropic trained Claude to **count pixels** for cursor positioning, and trained it on only a few simple applications (calculator, text editor) with no internet access. Yet it generalized to complex, unseen software. On the OSWorld benchmark, Claude achieved **14.9%** task completion (human-level is 70–75%), far ahead of the next-best at 7.7%. Still experimental, but the architecture pattern — VLM + action loop + sandboxed environment — is the foundation for autonomous computer-using agents.

**For NexSidi's QA agent:** This same pattern could power automated testing. Deploy generated apps in a sandboxed environment, use a VLM to navigate and verify the UI, report issues. This is a Phase 2 feature — powerful but complex to implement.

---

## Agent memory needs three tiers, not one

The question "Redis or SQL for memory?" presents a false dichotomy. Production agent systems use **three distinct tiers** working together.

**Working memory** (Redis, sub-millisecond) holds the current conversation state, active task context, and agent scratchpad. This is the "RAM" of your agent system. **Long-term memory** (PostgreSQL + pgvector, low-millisecond) stores user preferences, learned facts, project history as vector embeddings for semantic retrieval. **Episodic memory** (PostgreSQL with temporal metadata) enables "remember when we discussed this last week?" queries with time-aware search.

Agents should **not** read the entire cache every time. The MemGPT/Letta architecture (Packer et al., 2023) treats the LLM context window like physical RAM in an operating system — the agent itself manages its memory via function calls, paging relevant information in and out of context as needed. The newer A-MEM approach achieves **85–93% reduction in token usage** through selective top-k retrieval.

**Microsoft's GraphRAG** builds knowledge graphs from documents using LLM-extracted entity-relationship triples, then applies community detection (Leiden algorithm) to create hierarchical summaries. It excels at global thematic questions ("what are the main themes across all projects?") and multi-hop relationship queries. For code projects, graph structure is natural: files import other files, functions call functions, classes inherit from classes. **Vector similarity alone can find textually similar code but cannot answer "what files depend on this module?"**

**NexSidi recommendation:** Start with Redis (working memory) + PostgreSQL/pgvector (long-term + episodic). GraphRAG and Neo4j are Phase 2 — powerful for understanding project structure semantically, but the indexing cost (many LLM calls for entity extraction) and operational complexity aren't justified on Day 1.

---

## What "Antigravity" and "Emergent Architectures" actually mean

**Antigravity** has two meanings in the AI/Python world. The classic one is Python's `import antigravity` easter egg — it opens XKCD comic #353 in your browser, a joke about Python making you fly. The new, significant meaning is **Google's Antigravity IDE**, announced November 2025 alongside Gemini 3. It's an agentic development platform (likely forked from Windsurf, which Google acquired for $2.4 billion) with a **Manager View** for orchestrating multiple parallel AI agents across editor, terminal, and browser surfaces. It achieved **76.2% on SWE-bench Verified**. The name deliberately references the Python easter egg.

**Emergent Architectures** in AI agents refers to systems where complex, intelligent behaviors arise from interactions among simple, specialized agents without being explicitly programmed. This is the most advanced tier of multi-agent design. Tier 1 is deterministic (rule-based). Tier 2 is orchestrated (human-designed coordination, like LangGraph). **Tier 3 is emergent** — agents dynamically form teams, assign roles, and coordinate without centralized control. Think swarm intelligence, not conductor-led orchestra. For NexSidi, you're building Tier 2 (orchestrated LangGraph pipeline). Emergent architectures are a research frontier, not a production requirement.

---

## The NexSidi v2.0 stack: every decision with rationale

Here are the five critical technology decisions, each with a clear verdict and buy-vs-build determination.

**Frontend: Next.js** (ADAPT — use with shadcn/ui template). React Router v7 absorbed Remix; they're now the same framework. Next.js wins the 30-day sprint because AI coding assistants have extensive Next.js training data, the template ecosystem is massive (shadcn/ui + dashboard templates save 3–5 days), file-based routing eliminates boilerplate, and GCP Cloud Run deployment is well-documented. React Router v7's framework mode is technically sound but has fewer resources and AI tool familiarity.

**Backend: FastAPI (Python)** (BUILD). The performance debate is settled by one fact: **your bottleneck is LLM API latency (2–30 seconds per call), not HTTP throughput**. FastAPI handles ~4,000 req/s with tuning — 100× more than NexSidi needs. Go Fiber and Rust Axum offer 5–10× better raw throughput, but maintaining two languages (Go/Rust for API + Python for LangGraph/LangChain) doubles complexity for a solo founder. The "two-language problem" is a velocity killer on a 30-day sprint. If performance issues emerge later, extract hot paths to Go microservices incrementally.

**Database: PostgreSQL + pgvector on Cloud SQL** (BUY). Recent benchmarks show pgvector with HNSW indexing **outperforms Pinecone** on identical compute in both accuracy (0.99 recall) and throughput, at **75–79% lower cost**. Confident AI migrated from Pinecone to pgvector for this reason. One database handles relational data (users, projects, billing with ACID transactions) and vector embeddings. Dedicated vector DBs become necessary only above **100M vectors and 10,000 QPS** — orders of magnitude beyond NexSidi's near-term needs.

**Queue: Redis + Celery** (BUY Redis via GCP Memorystore, ADAPT Celery). Kafka and Redpanda are designed for millions of events per second — NexSidi needs thousands per hour at most. Celery's built-in task chaining (chord, chain, group) maps perfectly to multi-agent pipelines: parallel agents → convergence → next step. Redis doubles as cache layer and pub/sub for WebSocket real-time updates. Estimated cost: **₹3,000–5,000/month** on GCP Memorystore.

**Orchestration: LangGraph** (ADAPT). LangGraph 1.0 (released October 2025) is now production-proven at Uber, LinkedIn, Replit, and Elastic. NexSidi's 5 agents map directly to graph nodes with conditional edges. First-class human-in-the-loop support is essential for project approval workflows. A Grid Dynamics case study documented reliability issues with pre-1.0 LangGraph, but the 1.0 release added built-in durable execution. Temporal.io offers superior durability guarantees but adds significant complexity — defer it to Phase 2 if Celery's retry logic proves insufficient. AutoGen's conversational agent pattern is less suited to NexSidi's structured pipeline flow.

---

## Buy vs. Build decision matrix

| Component | Decision | Tool/Service | Monthly Cost |
|---|---|---|---|
| Frontend framework | ADAPT | Next.js + shadcn/ui + template | Free |
| Backend API | BUILD | FastAPI + Uvicorn | Free |
| Database | BUY | GCP Cloud SQL (PostgreSQL + pgvector) | ₹4,000–8,000 |
| Cache/Queue broker | BUY | GCP Memorystore (Redis) | ₹3,000–5,000 |
| Task queue | ADAPT | Celery 5.x | Free |
| AI orchestration | ADAPT | LangGraph 1.0 | Free |
| LLM providers | BUY | Vertex AI (Gemini) + Claude API | ₹4,000–16,000 |
| Compute | BUY | GCP Cloud Run (2 services) | ₹2,000–6,000 |
| Monitoring | BUY | LangSmith (free tier) + GCP Monitoring | Free–₹2,400 |
| CI/CD | BUY | GCP Cloud Build + Artifact Registry | ₹500–1,000 |

**Total estimated monthly cost: ₹15,000–40,000** (~$180–480). With ₹1.14L GCP credits, that's **3–7 months of runway** before any revenue is needed to cover infrastructure.

---

## How every component connects

The system flows as follows. Users interact with a **Next.js frontend** deployed on Cloud Run, which communicates via REST and WebSocket with a **FastAPI backend** on a second Cloud Run service. When a user submits a project, the API validates the request, calculates complexity-based pricing, and dispatches a Celery task to **Redis (Memorystore)**. 

Celery workers on a dedicated Cloud Run worker service pick up tasks and execute the **LangGraph agent pipeline**: Chat Agent receives the brief → Requirements Agent structures it into specs (human-in-the-loop checkpoint for approval) → Code Generation Agent produces FastAPI backend + React frontend → QA Agent reviews and tests the output (second checkpoint) → Deploy Agent packages and delivers the completed project. Each agent reads and writes to **PostgreSQL (Cloud SQL)** for durable state — project records, user data, billing, pgvector embeddings of requirements and generated code for semantic memory.

Redis serves triple duty: Celery task broker, result backend, and pub/sub channel for pushing real-time progress updates to the frontend via WebSocket. LLM calls route to **Vertex AI** (Gemini models, covered by GCP credits) for bulk generation tasks, with Claude or GPT-4 available for complex reasoning steps that benefit from higher-capability models.

---

## 30-day sprint plan built for velocity

**Week 1 (Days 1–7):** Foundation. Stand up Cloud Run + FastAPI scaffold, provision Cloud SQL with pgvector, create Next.js dashboard shell with auth (WorkOS or NextAuth), implement LangGraph pipeline with 2 agents (Chat + Requirements) as proof of concept.

**Week 2 (Days 8–14):** Core pipeline. Complete all 5 agents in LangGraph, integrate Celery task queue, build project submission flow, add WebSocket for real-time progress updates, implement basic pgvector storage for project embeddings.

**Week 3 (Days 15–21):** Features and polish. Human-in-the-loop approval workflows, complexity-based pricing calculator, generated project download and preview, error handling and retry logic around LLM calls.

**Week 4 (Days 22–30):** Production hardening. CI/CD pipeline via Cloud Build, monitoring and alerting, load testing with 10 concurrent projects, payment integration (Razorpay for INR), soft launch.

---

## Conclusion: what this architecture enables

This stack deliberately trades theoretical scalability for **shipping velocity**. Python everywhere means one mental model, one dependency ecosystem, and maximum leverage from AI coding assistants that know Python and FastAPI deeply. PostgreSQL as the single data store eliminates operational complexity while pgvector provides "good enough" vector search that benchmarks show is actually better than dedicated alternatives at this scale.

The three patterns worth borrowing from Netflix-scale systems right now are **circuit breakers** around LLM API calls (use Python's `tenacity` library), **the two-phase planning-then-execution pattern** from Cursor's apply model (frontier model plans, fast model executes), and **tiered memory** (Redis for hot state, Postgres for durable knowledge). Everything else — GraphQL Federation, service meshes, Kafka, Zuul — solves problems NexSidi won't have for years.

The most underappreciated insight from this research: **Cursor's "fast apply" model is not a diff algorithm — it's a fine-tuned LLM**. This same principle applies to NexSidi's code generation. Don't build complex deterministic code assembly pipelines. Fine-tune or prompt-engineer your way to whole-file generation, use speculative techniques where possible, and let the model do the heavy lifting. The architecture should make the AI's job easier, not replace it with hand-coded logic.