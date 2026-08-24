# Pronostico prestazionale X99 → ThinkStation P620

**Data di registrazione:** 20 agosto 2026
**Stato:** pronostico preregistrato; da verificare dopo la migrazione hardware

## Scopo

Conservare prima della migrazione le baseline misurate sulla piattaforma X99 e
i pronostici formulati da Stefano e Codex. Il confronto futuro deve usare lo
stesso modello, la stessa quantizzazione, lo stesso prompt e le stesse opzioni,
così da evitare reinterpretazioni a posteriori.

## Baseline X99

Configurazione rilevante:

- 2× NVIDIA GeForce RTX 4060 Ti 16 GB;
- collegamenti PCIe 3.0 ×8;
- Qwen interamente residente sulle due GPU;
- contesto Ollama: 32.768 token;
- thinking disabilitato.

Misure registrate:

| Modello | Velocità decode X99 | Nota |
|---|---:|---|
| `hf.co/unsloth/Qwen3.8-27B-GGUF:Q4_K_M` | **14,108 token/s** | media di 3 repliche da 512 token: 14,137 / 14,084 / 14,103 token/s |
| `gemma4:26b` | **circa 51,4 token/s** | precedente benchmark con modello distribuito sulle due 4060 Ti |

Nel test Qwen a modello caldo, 512 token hanno richiesto mediamente circa
36,29 secondi. Lo snapshot conclusivo mostrava circa 80 W per GPU e utilizzo
parziale, coerente con margine potenziale nella piattaforma di alimentazione
dei dati e nella topologia PCIe.

## Condizioni del confronto futuro

Il verdetto principale sarà valido usando:

- le stesse due RTX 4060 Ti, senza includere la RTX 3080 nello split del
  modello;
- gli stessi file modello e la stessa quantizzazione;
- identici `num_ctx`, thinking, prompt e numero di token generati;
- modello già caldo per il dato di decode;
- nessun altro carico significativo sulle GPU;
- slot PCIe 4.0 appropriati e memoria della P620 distribuita correttamente sui
  canali disponibili.

La RTX 3080 potrà essere valutata separatamente per Dream, Whisper, torneo o
altri carichi concorrenti, ma quel risultato sarà una misura di reattività del
sistema Euri e non il confronto diretto X99/P620 qui preregistrato.

## Pronostici

| Autore | Qwen3.8-27B Q4_K_M | Gemma4:26b |
|---|---:|---:|
| **Stefano** | **20 token/s** | **65 token/s** |
| **Codex** | **19,2 token/s** | **63 token/s** |

Fascia ritenuta probabile da Codex prima della migrazione:

- Qwen3.8: 18–20,5 token/s;
- Gemma4: 60–65 token/s.

## Criterio del vincitore

Per ogni modello vince il pronostico con la minore distanza assoluta dalla
media di almeno tre repliche valide a modello caldo. Se uno dei test non è
riproducibile nelle condizioni sopra indicate, il confronto viene dichiarato
non comparabile anziché corretto retroattivamente.

## Clausola informale

Se il pronostico complessivo di Stefano risulterà il più vicino, Stefano ha
dichiarato che andrà in Turchia a fare un trapianto di capelli. La clausola è
registrata come elemento umoristico e non come requisito tecnico della
migrazione P620.
