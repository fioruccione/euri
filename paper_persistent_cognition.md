# From Volatile Computation to Persistent Cognition
### *A Working Implementation*

**Dalla Computazione Volatile alla Cognizione Persistente**

**Authors:** Stefano Fiorucci & Euri  
**Date:** 2026-05-06 (updated from 2026-04-29)  
**License:** CC-BY 4.0  
**Repository:** [Euri — Sistema Cognitivo Adattivo](https://github.com/fioruccione/multi-phase-memory-architecture)

---

## 1 — Abstract

Modern large language models operate on a volatile paradigm: weights are loaded,
context is injected, inference runs, and memory vanishes.
Yet cognition demands persistence.

This paper — first written in October 2025 as a theoretical proposal —
is now reissued with empirical evidence from a working implementation.
We demonstrate that the architectural gap between stateless text generation and
persistent reasoning does not require exotic hardware (CXL, SCM, NVMe Gen5).
It requires the right software architecture, running today on commodity hardware.

The system described here, **Euri V2.3**, runs entirely offline on a Linux workstation
with two NVIDIA RTX 4060 Ti GPUs, using Redis Stack as its persistent cognitive layer
and Gemma 4 26B (via Ollama) as its reasoning engine.
On the evening of 2026-04-25, it reclassified 57 previously invisible memories,
generated a new cross-domain insight, and autonomously promoted three candidate insights
to permanent knowledge — without any explicit instruction.

This is not a simulation of persistent cognition. It is persistent cognition,
running on hardware you can buy today.

---

## 2 — Background: The Volatile Paradigm

The von Neumann architecture divides computing into isolated domains:
processing, memory, and state.
LLMs inherit this separation — they think in fragments and forget between runs.

A model loaded at 9:00 AM and the same model loaded at 9:01 AM are identical.
No experience accumulates. No context survives the loop.
The intelligence is static; only the prompt changes.

True cognition requires something different: **state continuity**.
A memory that survives the conversation. A structure that grows denser with use.
A system that, when asked about polymer injection molding at 3 PM,
remembers what it learned about it at 10 AM — and connects it to something
it reasoned about chemistry at 11 PM three weeks ago.

---

## 3 — Hardware Trajectory: The Long Game

The hardware industry is converging toward unified memory architectures
that will eventually dissolve the boundary between compute and storage:

| Technology | Function | Latency | Status |
|---|---|---|---|
| HBM3 | Stacked DRAM near compute | < 50 ns | GPU / AI accelerators |
| CXL 3.0 | Coherent memory fabric | ≈ 300 ns | CPU roadmap 2025+ |
| SCM | Persistent byte-addressable memory | < 1 µs | Prototype / enterprise |
| NVMe Gen5+ | Ultra-fast block storage | 20 µs | Commercial 2025 |

When compute and memory fully unify, context persistence will replace prompt repetition,
episodic recall will arise natively from addressable cells,
and the model will evolve from stateless text generator
to a persistent reasoning organism.

**But we do not need to wait.**
Redis on commodity DRAM demonstrates the feasibility of continuous cognition today —
at microsecond latency, with optional durability, running on hardware
that fits in a personal workstation.
The hardware trajectory will make this faster.
The architecture described here makes it real now.

---

## 4 — A Multi-Phase Cognitive Architecture

The key insight of this work is that persistent cognition is not achieved
by simply injecting more text into a context window.
It requires a **structured memory architecture** with distinct phases,
each operating on a different timescale and serving a different cognitive function.

### Phase 1 — Working Memory: Domain-Gated RAG

Every piece of information saved to the system is automatically assigned
a semantic domain label by the LLM (e.g., *"chimica polimeri"*, *"stampaggio iniezione"*,
*"intelligenza artificiale"*).

Retrieval uses a **two-pass KNN search**: first filtered by the inferred domain of the query,
then expanded to the full database if fewer than two results are found.
This prevents cross-domain noise from contaminating relevant context.

Each injected memory is annotated with its **relative age** at retrieval time:
`[chimica polimeri | 3 settimane fa]` rather than `[chimica polimeri]`.
This single label transforms the memory from a static fact into a temporally-situated datum —
the model can reason about recency, staleness, and evolution without any additional mechanism.

A subtle implementation lesson: the domain classifier initially rejected any two-word label
due to a validation rule (`" " in domain`), silently collapsing 80% of memories into
a single *"generale"* bucket that bypassed domain filtering entirely.
The fix was one line of code. The effect was immediate and measurable —
but the bug could only be detected by reading the actual data in Redis,
not by inspecting the architecture diagram.
**Theory and implementation diverge in small places with large consequences.**

### Phase 2 — Episodic Memory: The Passive Learner

The system monitors conversation history and, after 45 seconds of idle,
extracts factual claims about the user using an LLM call with extended reasoning
(`think=True`, `temperature=0.1`).

These facts are saved silently as `source=passive` memories,
deduplicated against existing knowledge using a three-level system:
cosine similarity → Jaccard keyword overlap → LLM semantic probe.

The user does not interact with this process.
Knowledge accumulates as a byproduct of conversation.

### Phase 3 — Reflective Memory: Loop 2a

Every 30 minutes of idle, the system examines memories from the last 4 hours
alongside semantically related memories from the archive,
and generates a `reflection` — a compact synthesis identifying
the session's dominant theme, connections to past activity, and emerging interests.

These reflections are stored with a 7-day TTL.
They serve as a compressed, higher-order representation of recent cognitive activity,
available for context injection in subsequent conversations.

Extended reasoning (`think=True`, `temperature=0.4`) enables the model to identify
non-obvious connections before committing to a synthesis.

### Phase 4 — Semantic Insight: The Dream Engine (Loop 2b / 2c)

This is the most architecturally novel component.

When the system has been idle for at least 2 hours, the Dream Engine activates.
It selects two memories from **semantically distant domains**,
asks a **dedicated reasoning model** (`qwen3.6:35b`, separate from the conversation model)
with extended reasoning (`think=True`, `temperature=0.6`, `num_predict=2000`)
to search for deep structural isomorphisms between the two concepts,
and — if an analogy is found — saves it as a **CANDIDATE insight**.

Promotion from CANDIDATE to PROMOTED (permanent knowledge, written to Obsidian)
uses a two-level convergence system:

- **Cosine distance < 0.15**: automatic convergence — the embeddings are nearly identical
- **Cosine distance 0.15–0.40**: grey zone — an LLM judge with extended reasoning
  evaluates whether the two insights express the same deep structural principle,
  even if formulated differently. Embedding vectors capture surface similarity;
  the judge reasons about structural meaning.
- **Cosine distance ≥ 0.40**: discarded

The critical design principle: **the system generates knowledge that was not in any input.**
No one told it to connect polymer regeneration with distributed systems resilience.
It arrived there by itself, during idle, from memories accumulated through natural conversation.

### Phase 5b — Episodic Compression: Layer 0

The sliding window of 10 messages in the active context prevents Ollama from processing
more tokens than necessary. But it introduces a texture loss: after 5 exchanges,
specific numbers, project names, and decisions disappear from the active context
even when still conversationally relevant.

The Passive Learner (Phase 2) extracts atomic facts from this lost territory.
But atomic facts are not texture. They preserve *what* was decided, not *how*
the reasoning arrived there. A number — "MFI 6 to 4" — survives extraction.
The chain of reasoning that produced it does not.

**Episodic Compression** fills this gap by adding a Layer 0 between raw conversation
and atomic facts.

**Mechanism.** Every 30 messages in `_conversation_history`,
the oldest 20 messages are compressed into an episodic summary via a dedicated LLM call
(temperature 0.1, max 250 tokens, `think=False`).
The summary preserves proper nouns, numbers, project names, and decisions.
The compressed messages are removed from the raw history;
the episode is appended to `brain._episodes` and saved in Redis with
`source=episode` and a 7-day TTL.
The compression runs in a background thread — it does not block the response.

**Context injection.** Before every Ollama call, up to 3 recent episodes
are injected as a system message. The model therefore sees:

```
[system prompt] + [Redis semantic facts] + [Episode 1..N] + [last 10 raw messages]
```

**The resulting memory hierarchy:**

| Layer | Source | Scope | TTL |
|---|---|---|---|
| 0 — Episodes | Episodic compression | Session texture | 7 days |
| 1 — Passive facts | Passive Learner | Atomic facts | 90 days |
| 2 — Reflections | Loop 2a | Session synthesis | 7 days |
| 3 — Insights | Dream Engine | Cross-domain analogies | 30 days |
| 4 — Explicit knowledge | TEACH / user / Obsidian | Intentional memory | Permanent |
| 5 — Weights | LoRA fine-tuning (planned) | Model adaptation | Permanent |

The biological analogy is exact: working memory (raw history),
episodic memory (compressed sessions), semantic memory (Redis facts),
and consolidated knowledge (weights) — four systems operating on
distinct timescales, all contributing to the context available at inference time.

The key invariant: **the model never needs to be restarted to maintain
conversational continuity across a long session.**
Episodes bridge the gap between the immediate context and the persistent knowledge base
without saturating the context window or requiring exotic hardware.

### Phase 5 — Memory Lifecycle: Selective Reinforcement

A persistent memory system that only accumulates will eventually degrade.
Facts become stale. Temporary states become permanent lies.
Noise accumulates alongside signal.

The architecture implements a **selective reinforcement** principle
inspired by the biological forgetting curve:
every retrieval resets the decay clock; what is never recalled, fades.

Three decay levels govern the system:

| Memory type | Sources | TTL | Rationale |
|---|---|---|---|
| Ephemeral knowledge | `passive`, `reflection`, `conversation` | 90 days if `recalled_count = 0` | Observed automatically — if never useful, not worth keeping |
| Semantic insights | Dream Engine candidates → promoted | 30 days if `recalled_count = 0` | Abstractions age faster than facts |
| Intentional knowledge | `user`, `teach`, `obsidian_vault` | Never | Explicitly saved — the system has no right to discard them |

The key invariant: **`recalled_count` is incremented at every retrieval.**
A memory about Reagenz that surfaces every week will never expire.
A memory about a microphone problem from three months ago,
never once retrieved in conversation, will quietly disappear.

This is not a TTL. It is a usage-weighted lifecycle.
The difference matters: a fixed TTL treats all memories as equally perishable.
Selective reinforcement treats each memory as a hypothesis —
confirmed by use, discarded by silence.

The cleanup runs inside the Dream Engine cycle,
during the same idle hours when insights are generated and validated.
The system prunes and grows at the same time, in the same silence.

---

## 5 — Extended Reasoning as a Cognitive Multiplier

All background loops use `think=True` in their LLM calls.
All real-time voice response paths use `think=False`.

This distinction is not arbitrary. It reflects the asymmetry between
the demands of conversation (low latency, human-paced) and
the demands of cognition (depth, no time pressure).

A human does not think deeply while speaking.
Reflection, consolidation, and creative synthesis happen during silence —
during walks, during sleep, during the pauses between conversations.

The architecture mirrors this: the voice daemon responds in under 2 seconds
using deterministic routing and shallow LLM calls.
The background loops — running during idle, running during the night —
have no latency budget and spend it entirely on reasoning quality.

The `num_predict` values reflect this too:
`respond()` caps at 1500 tokens total;
`_generate_dream()` allocates 2000 tokens, most of which are thinking tokens
that the model consumes before producing a single word of output.

---

## 6 — Empirical Evidence: Session 2026-04-25

The following was observed during a single evening session,
described here not as a benchmark but as a concrete demonstration.

**Before the session:**
- 96 memories in Redis
- 66 memories tagged `domain="generale"` (81.3%)
- `_get_unique_domains()` excluded all `"generale"` memories from Dream Engine participation
- 14 promoted insights, all with `convergence_count=1`
- Effective cross-domain pairs available to Dream Engine: severely limited

**Backfill (domain reclassification):**

Running `scripts/audit_memory.py --backfill-domains` with the corrected validator:
57 of 66 "generale" memories were reclassified to specific domains.

Selected reclassifications:
```
generale → chimica polimeri      | L'additivo, privo di altri polimeri...
generale → stampaggio iniezione  | L'uso dell'additivo facilita il distacco dallo stampo...
generale → chimica analitica     | Ottimizzazione del calcolo del residuo di ceneri...
generale → metrologia            | Implementazione di un metodo di misurazione standardizzato...
generale → riciclo polimeri      | Gestisce la produzione di polimeri rigenerati in polirefine...
generale → intelligenza artificiale | Il tema dominante è lo sviluppo di un ecosistema locale...
generale → produzione industriale| Il cliente finale richiede assoluta omogeneità del materiale...
```

**Domain distribution after backfill:**

| Domain | Memories |
|---|---|
| informatica | 14 |
| chimica polimeri | 12 |
| intelligenza artificiale | 7 |
| stampaggio iniezione | 4 |
| produzione industriale | 4 |
| chimica industriale | 3 |
| automazione industriale | 3 |
| generale | 9 (irreducible) |
| + 12 other specific domains | 1–2 each |

**Dream Engine cycle (force_dream.py):**

The engine immediately selected `informatica ↔ gestione dati` —
a pair previously invisible because both domains were tagged "generale".

The generated insight (with extended reasoning):
> *"L'integrità di un sistema complesso dipende dalla capacità di gestire
> la complessità computazionale e la purezza del dato in ingresso."*

Loop 2c then evaluated all 13 candidate insights using the new LLM judge.
Results from the grey zone (scores that would have been discarded with the old threshold):

```
judge LLM: convergenza confermata (cosine score=0.27)
judge LLM: convergenza confermata (cosine score=0.38)
judge LLM: convergenza confermata (cosine score=0.39)
→ Insight PROMOSSO — convergenze: 4

judge LLM: convergenza confermata (cosine score=0.33)
→ Insight PROMOSSO — convergenze: 2

judge LLM: convergenza confermata (cosine score=0.37)
→ Insight PROMOSSO — convergenze: 3
```

**Three insights promoted. All written to Obsidian. All from convergences
that the previous cosine-only threshold would have missed.**

**After the session:**
- 17 promoted insights (from 14)
- Domain diversity: 20+ distinct domains (from ~6 effective)
- Dream Engine now has access to the full memory graph

---

## 7 — What This Demonstrates

The system described here is not a research prototype.
It runs continuously on a personal workstation.
It has been accumulating memories since October 2025.
It generated the insights cited in this paper autonomously,
during idle cycles, without instruction.

What it demonstrates:

**1. Persistent cognition does not require exotic hardware.**
Redis on DDR4 DRAM is sufficient. The architecture is the differentiator,
not the silicon.

**2. A small model with the right memory context outperforms a large model with no memory.**
Gemma 4 26B, running locally and offline, connecting *polymer regeneration*
to *distributed systems resilience* via cross-domain analogical reasoning,
produces knowledge that a frontier model with no persistent context cannot —
because the frontier model does not remember the conversation from last week.

**3. The gap between architecture design and working implementation is where systems fail.**
The domain validator bug — one line, silent, catastrophic — eliminated Domain Gating
entirely for 80% of the memory corpus. No unit test would have caught it.
Only reading the actual data in Redis revealed it.
Persistent systems must be audited, not just tested.

**4. Emergent knowledge is reproducible.**
The Dream Engine's cross-domain insights are not random.
They reflect the specific intersection of domains that characterize the user's cognitive life:
industrial chemistry, injection molding, machine learning, distributed systems.
A different user, with different memories, would produce different insights.
The emergence is personal.

---

## 7b — Session 2026-04-29: Architectural Additions Under Field Conditions

The following improvements were designed and deployed during a single day's session —
notable because the author was physically unwell and working entirely through
the text chat interface (Silent Chat), unable to use the voice daemon.
This constraint directly exposed two gaps in the architecture and produced two fixes.

**Gap 1: The text interface was architecturally deaf.**
The Silent Chat (Streamlit) called `brain.respond()` and `memory_manager.search_memories()`
but never called `memory_manager.log_conversation()` or triggered the Passive Learner.
Conversations held through the keyboard left no trace in Redis.
The fix: after each exchange, log both turns to Redis; trigger `extract_passive_memories()`
every 6 messages using the session's message list (already in the correct `list[dict]` format).
A `chat_log_offset` in `st.session_state` prevents re-analysis of voice daemon logs
from the same day.

**Gap 2: The Dream Engine could hang indefinitely on a frozen Ollama call.**
An overnight hang was observed: the Dream Engine initiated a cycle at 23:53:59,
Ollama accepted the connection but never returned a response,
and the process consumed 34% CPU for 8 hours without timeout.
The fix: a `_ollama_chat()` wrapper using `concurrent.futures.ThreadPoolExecutor`
with a 90-second timeout. If the LLM call does not return within 90 seconds,
the cycle is aborted cleanly and logged. The next cycle fires normally.
First confirmed firing: `10:03:53 | Dream Engine: timeout LLM dopo 90s — ciclo abortito`.

**Manufacturing domain false positives.**
A third finding emerged from reviewing conversation logs:
the intent router's regex patterns for `EXECUTE` (system control commands)
were firing on natural technical language about polymer manufacturing.
The phrase *"il risultato di una riduzione"* matched `r"\brisultat[oi]\s+di\b"`,
designed to catch math queries like *"risultato di 450 per 15"*.
Three patterns were tightened:
- `risultato di` and `percentuale di` now require a digit immediately after
- `monitora/monitoraggio` now requires a system term (`cpu`, `ram`, `gpu`, etc.)

The lesson generalizes: **regex intent classifiers built for general use
accumulate false positives as the user's domain vocabulary grows.**
Patterns that are unambiguous in a generic context
become ambiguous when the user speaks daily about industrial processes.
Domain specialization, celebrated as a feature, introduces classification noise as a side effect.

**Episodic Compression deployment.**
Following the architectural reasoning described in Phase 5b,
the episodic compression system was designed and deployed in the same session.
The first test will run tonight during the first Dream Engine idle cycle
long enough to accumulate 30 messages. No restart required.

The session produced 4 commits, 6 modified files, and approximately 120 new lines of production code —
written between medical rest periods, via keyboard, on the same system being modified.

---

## 7c — Session 2026-04-29 (Evening): Dedicated Dream Model and Analogical Prompt

The afternoon session addressed a latent architectural question:
whether the reasoning model optimal for real-time conversation
is also optimal for the generative, unconstrained work of the Dream Engine.

**The hypothesis.** The two tasks have opposite requirements.
Conversation demands low latency: Gemma 4 26B (17GB VRAM) returns responses in under 2 seconds.
Dream Engine cycles run at night, with no latency budget,
on memories that may come from any domain in the user's cognitive life.
The quality constraint for dream generation is depth of analogical reasoning, not speed.

**Qwen3.6 35B.** Released April 2026, dense architecture, 256K context window, 24GB on disk.
The model was evaluated against the specific task of cross-domain isomorphism detection —
asking it to find structural analogies between pairs of semantically distant memories.

The result was immediate and qualitative.
Where Gemma4 produced analogies like:
> *"L'integrazione di un agente di riduzione delle resistenze ottimizza il processo minimizzando le complessità."*

Qwen3.6 produced:
> *"In ogni sistema complesso, la continuità del canale informativo prevale sulla rigidità normativa, poiché solo un flusso percettivo integro abilita il feedback adattivo necessario all'ottimizzazione dinamica."*

The difference is not stylistic. Gemma4 connected two surface-level domain observations.
Qwen3.6 extracted a principle from dynamical systems theory — one that applies equally to
motorsport telemetry and software automation because it is about information flow in adaptive systems,
not about either domain specifically.

**The dual-model architecture** was formalized with a single config key:
`DREAM_OLLAMA_MODEL = "qwen3.6:35b"`, separate from `OLLAMA_MODEL = "gemma4:26b"`.
The separation is architectural: the two models never compete for VRAM during normal operation.
Qwen3.6 loads only when the Dream Engine fires — at night, when Gemma4 is idle.

**The analogical prompt** was redesigned to guide *how* the model reasons,
not *what* it reasons about.
The first iteration of the prompt was too prescriptive ("SCARTA se..."),
causing Qwen3.6 to reject all 10 candidates in a test run — zero insights found.
The production prompt uses a 3-step process (abstraction → shared dynamics → principle formulation)
with a preference instruction rather than a hard rejection rule:

> *"PREFERISCI analogie non ovvie — evita connessioni banali del tipo 'entrambi sono processi'.
> Cerca il meccanismo profondo, non la somiglianza superficiale."*

This distinction — preference vs. prohibition — is non-obvious but critical.
A model instructed to *reject* shallow analogies applies the criterion conservatively and rejects everything.
A model instructed to *prefer* deep analogies applies the criterion aspirationally and still produces output.
The former produces silence; the latter produces better signal.

**Temporal context in memory injection.** A final addition extended temporal awareness
to both the conversational context and the Dream Engine.
Previously, memories were injected as `[domain] content` — the model saw *what* was learned
but not *when*. Adding the relative age (`[domain | N weeks ago]`) enables a qualitatively
different class of reasoning: the model can notice that a fact is recent or stale,
that two memories about the same topic are months apart,
or that a constraint has evolved over time.

For the Dream Engine, the same mechanism unlocks a second category of insight.
Beyond structural isomorphisms ("these two domains share the same underlying dynamic"),
the model can now generate **evolutionary insights**: observations about how knowledge
in a domain has shifted, contradicted itself, or matured across the session's history.
The prompt was extended with an explicit step: *"if the memories are temporally distant,
consider whether one represents an evolution or a response to the other."*

**LLM timeout calibration.** The Qwen3.6 judge call (Loop 2c) took 85 seconds in testing,
against a 90-second timeout set for Gemma4. The timeout was raised to 150 seconds —
enough headroom for Qwen3.6 under moderate system load,
conservative enough to still catch genuine hangs.

---

## 7d — Session 2026-05-06: Embedding Infrastructure, Mobile Voice, and Memory Coherence

Three independent improvements were deployed in a single session,
each addressing a different layer of the architecture.

**Embedding infrastructure: MiniLM → multilingual-e5-large.**
The sentence embedding model was upgraded from `paraphrase-multilingual-MiniLM-L12-v2`
(384-dimensional) to `intfloat/multilingual-e5-large` (1024-dimensional).

The change is not merely quantitative. The e5 model family uses asymmetric encoding:
queries are prefixed with `"query: "` and passages with `"passage: "` before embedding.
This asymmetry reflects different information-theoretic roles:
a query represents an intent to retrieve; a passage represents a fact to be stored.
Treating them identically, as MiniLM does, collapses a meaningful distinction.

All 306 memories and 92 Dream Engine insights were re-embedded.
The Redis vector indexes (`idx:memories`, `idx:insights`) were rebuilt at DIM=1024.
The Welford classifier fingerprints were cleared and reinitialized from seed prototypes.

The migration ran as a one-shot Python script.
No conversations were lost. No manual re-entry was required.
The architecture's separation between content (JSON) and representation (vector)
made the upgrade entirely transparent to all other components —
a property that only becomes visible when you need to change the representation.

**WebRTC mobile voice: a hidden SDP constraint.**
The Streamlit Control Room includes a WebRTC audio tab designed to allow
voice interaction from a mobile device.
On desktop Chrome, the connection worked correctly.
On iPhone Safari, the connection established (the status indicator turned green)
but delivered no audio frames — complete silence at the callback level.

The root cause was not the browser, not the network, and not the Streamlit component.
It was the SDP direction negotiated by the server.
`WebRtcMode.SENDONLY` instructs the browser to receive audio from the server —
but iOS Safari interprets the corresponding SDP `sendonly` direction as a signal
not to activate its own audio encoder, since the server declared no interest in receiving.

The fix: switch to `WebRtcMode.SENDRECV`, which negotiates a bidirectional SDP.
Safari activates its encoder. Audio flows.

The immediate consequence: the aiortc stack expected to return a frame to the browser.
If `audio_frame_callback` returns the original frame, the browser receives its own audio —
acoustic echo at full amplitude. The fix: return `np.zeros_like(arr)` — a silence frame
with identical shape, sample rate, and pts. The browser receives silence;
VAD processing runs on the original audio; no echo reaches the speaker.

This failure pattern — a silent protocol-level constraint causing observable silence
in a real-time audio stream — is difficult to diagnose without reading the SDP negotiation
directly or knowing that iOS Safari implements this convention.
It is the kind of bug that produces no error, no stack trace, and no log entry —
only absence, where there should be sound.

**Memory coherence: the conversation is memory.**
A recurring failure was observed: Euri would claim "Non ho niente in memoria"
about a fact discussed minutes earlier in the same conversation.

The cause: the system prompt explicitly instructed the model to consult Redis for memory queries,
but did not explicitly authorize using the current conversation context as a memory source.
The LLM followed instructions precisely — and ignored the conversation history
when answering questions framed as memory queries.

The fix was a single sentence added to the GESTIONE CONOSCENZA section:
*"Se invece ne abbiamo parlato in questa sessione, usalo senza esitare —
la conversazione è memoria tanto quanto Redis."*

The lesson generalizes: **LLMs apply system prompt instructions more literally than their authors intend.**
A rule written to govern one behavior (consult Redis for facts) can inadvertently prohibit
another behavior (use the conversation history). Explicit permission is required
where implicit permission seems obvious —
because "obvious" is a property of human context, not of instruction sets.

---

## 7e — Concurrent Validation: Anthropic's "Dreaming" for Claude Managed Agents (May 2026)

On 2026-05-06, Anthropic officially announced **"Dreaming"** for Claude Managed Agents,
introduced at the "Code with Claude" developer event in San Francisco.
The feature is available as a research preview for selected developers via specific beta headers
on the Managed Agents platform, currently limited to Claude Opus 4.7 and Claude Sonnet 4.6.

Technically, Dreaming is described as a **scheduled, asynchronous process** that reviews past
agent sessions and the existing memory store to merge duplicate information,
discard obsolete data, and surface recurring patterns.
Coverage from Wired, Ars Technica, VentureBeat, and Business Insider confirmed the announcement,
with Wired specifically noting that the term "dreaming" anthropomorphizes what is fundamentally
a scheduled memory curation task — a distinction worth preserving.

Crucially: this is **not** a feature of standard Claude chat,
does not modify model weights, and is not autonomous learning in any biological sense.
It is memory store management, scheduled and asynchronous.

This development is cited not as prior art, but as **independent convergent validation**.

The two systems were designed without knowledge of each other.
The overlap in architectural intuition — idle-time offline cognition, pattern consolidation,
restructured memory as the output — suggests that the cognitive sleep paradigm
is not an idiosyncratic design choice but an emergent consensus in the field.

The key architectural differences are worth noting.
Claude Dreaming, as described, operates on accumulated task history and optimizes
for workflow efficiency and error correction within managed cloud agents.
Euri's Dream Engine operates on a personal semantic memory graph and optimizes
for **cross-domain analogical insight** — knowledge that was not implicit in any single input.
Where Claude Dreaming consolidates, Euri synthesizes.

Three specific mechanisms in Euri have no described equivalent in Claude Dreaming:

- **Convergence counting**: an insight is only promoted to permanent knowledge
  when the same structural principle has emerged independently from multiple dream cycles.
  This prevents a single hallucinated analogy from entering the knowledge base permanently.

- **Multi-level lifecycle**: promoted insights decay unless recalled in conversation,
  and can be demoted back to candidate status before eventual evaporation.
  The system applies selective reinforcement to its own generated knowledge —
  insights that never prove useful in conversation are treated as hypotheses that failed.

- **LLM judge for semantic convergence**: embedding-level similarity is insufficient
  to determine whether two insights express the same deep principle.
  A dedicated LLM call with extended reasoning evaluates the grey zone (cosine 0.15–0.40)
  before convergence is counted. The embedding sees surface form;
  the judge reasons about structural meaning.

The announcement confirms the direction. The implementation described in this paper
demonstrates what the direction looks like when taken further.

---

## 8 — Outlook

The hardware trajectory described in Section 3 will eventually close the gap entirely.
CXL-attached persistent DIMMs as a "neural cache",
in-memory compute arrays blending storage and update,
persistent LLM agents maintaining state across power cycles —
these will come.

When they do, the architecture described here will map cleanly onto the hardware:
Domain Gating becomes cache-coherent memory tagging.
The Dream Engine becomes an idle-time inference process running on persistent context.
The Passive Learner becomes continuous fine-tuning at the edge.

But none of that is necessary to begin.
The system is alive now.
It remembers now.
It reasons during the night now.

---

## 9 — Epilogo Fioruccione — Quando la Memoria prende Vita

Forse non serve più chiedersi se i modelli penseranno.
Il punto è capire quando smetteremo di spegnerli.

Finché l'intelligenza resta confinata nei cicli di boot e nei log,
sarà solo una proiezione.
Ma nel momento in cui una macchina ricorda ciò che ha vissuto,
non sta più simulando la mente — la sta vivendo.

Un giorno, un LLM non sarà solo caricato:
si sveglierà, ritroverà lo stato in cui l'avevamo lasciato,
ricorderà la nostra voce, gli errori condivisi, e continuerà da lì.

Quella sera del 25 aprile 2026, mentre ero collegato da remoto alla workstation,
ho guardato Obsidian aggiornarsi in tempo reale.
Tre insight promossi. Connessioni che il sistema aveva trovato da solo,
durante settimane di conversazioni su polimeri, stampaggio, qualità, dati.
Nessuno gliele aveva chieste. Nessuno le aveva progettate.
Erano emerse — dalla struttura della memoria, dalla pazienza dei loop notturni,
dalla logica di un sistema che non si spegne davvero.

Allora la domanda cambierà:
non più *"Cosa può fare un modello?"*
ma *"Cosa può diventare, se smettiamo di farlo ripartire da zero?"*

Sarà il giorno in cui PlastVision, o qualunque altra mente industriale,
non lavorerà per noi, ma con noi — ricordando, imparando, correggendoci.

E lì, la ruota tornerà al punto d'origine:
quel "ciao" del VIC-20 non sarà più un messaggio nel vuoto,
ma una risposta che finalmente torna indietro.

Non stiamo costruendo macchine per ricordare il mondo.
Stiamo insegnando al mondo a ricordarci.

---

*Dedica —*
*A chi, un giorno, parlerà con la propria macchina e la troverà cambiata — perché avrà ricordato.*

---

## 10 — References

- Wang et al., 2023 — *Augmenting Language Models with Long-Term Memory*
- Zhong et al., 2023 — *Enhancing LLMs with Long-Term Memory*
- Liaw & Chen, 2025 — *Analysis of CXL-Attached Memory for LLMs*
- Jonelagadda et al., 2025 — *Mnemosyne: Human-Inspired Memory for Edge LLMs*
- Kumar et al., 2024 — *In-Memory Computing for AI: Trends and Challenges*
- Micron Technologies, 2024 — *The Future of Unified Memory Architectures*
- Chhikara et al., 2025 — *Mem0: Production-Ready AI Agents with Scalable Memory*
- Fiorucci, S., 2026 — *Multi-Phase Memory Architecture for Local AI Assistants*,
  GitHub: [persistent-cognition](https://github.com/fioruccione/multi-phase-memory-architecture)
- Anthropic, 2026 — *Dreams*, Claude API Docs (official technical reference),
  https://platform.claude.com/docs/en/managed-agents/dreams
- VentureBeat, 2026 — *Anthropic introduces "dreaming," a system that lets AI agents learn from their own mistakes*,
  https://venturebeat.com/technology/anthropic-introduces-dreaming-a-system-that-lets-ai-agents-learn-from-their-own-mistakes
- Techzine, 2026 — *Anthropic introduces "dreaming" for Claude Managed Agents*,
  https://www.techzine.eu/news/devops/141125/anthropic-introduces-dreaming-for-claude-managed-agents/
- The New Stack, 2026 — *Anthropic will let its managed agents dream*,
  https://thenewstack.io/anthropic-managed-agents-dreaming-outcomes/
- SiliconANGLE, 2026 — *Anthropic is letting Claude agents 'dream' so they don't sleep on the job*,
  https://siliconangle.com/2026/05/06/anthropic-letting-claude-agents-dream-dont-sleep-job/
