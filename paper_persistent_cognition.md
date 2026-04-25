# From Volatile Computation to Persistent Cognition
### *A Working Implementation*

**Dalla Computazione Volatile alla Cognizione Persistente**

**Authors:** Stefano Fiorucci & Euri  
**Date:** 2026-04-25  
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

The system described here, **Euri V2.2**, runs entirely offline on a Linux workstation
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
asks the LLM (with extended reasoning, `think=True`, `temperature=0.6`, `num_predict=2000`)
to search for deep structural isomorphisms between the two concepts,
and — if an analogy is found — saves it as a **CANDIDATE insight**.

Promotion from CANDIDATE to PROMOTED (permanent knowledge, written to Obsidian)
uses a two-level convergence system:

- **Cosine distance < 0.15**: automatic convergence — the embeddings are nearly identical
- **Cosine distance 0.15–0.40**: grey zone — an LLM judge with extended reasoning
  evaluates whether the two insights express the same deep structural principle,
  even if formulated differently. MiniLM embeddings are shallow;
  the judge reasons about meaning.
- **Cosine distance ≥ 0.40**: discarded

The critical design principle: **the system generates knowledge that was not in any input.**
No one told it to connect polymer regeneration with distributed systems resilience.
It arrived there by itself, during idle, from memories accumulated through natural conversation.

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
