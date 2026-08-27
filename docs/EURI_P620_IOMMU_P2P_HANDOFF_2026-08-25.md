# P620 — checkpoint test IOMMU/P2P

**Data:** 25 agosto 2026
**Scopo:** verificare se la disattivazione di AMD-IOMMU rende disponibile il
peer-to-peer CUDA fra le due RTX 4060 Ti, senza cambiare modello o runtime.

## Baseline prima del riavvio

- BIOS Lenovo: `S07KT6EA`.
- Kernel: `7.0.11-76070011-generic`.
- Driver NVIDIA open: `580.159.03`.
- GPU: 2x RTX 4060 Ti 16 GB, stesso root complex, PCIe 4.0 x8 massimo.
- BAR1: 16 GiB per GPU.
- IOMMU corrente: AMD-Vi attivo, dominio predefinito `Translated`.
- `nvidia-smi topo -p2p r`: `CNS` in entrambe le direzioni.
- `nvidia-smi topo -p2p w`: `CNS` in entrambe le direzioni.
- Baseline Qwen conservata: mediana `14,318 token/s`.
- Nessuna opzione modello, quantizzazione o Ollama deve cambiare in questa fase.

## Modifica autorizzata

Aggiungere con `kernelstub` il solo parametro kernel:

```text
amd_iommu=off
```

Rollback, se il boot o le periferiche presentano problemi:

```bash
sudo kernelstub -d "amd_iommu=off"
sudo reboot
```

Se necessario, dal menu systemd-boot si puo' avviare la vecchia immagine e
rimuovere il parametro.

## Verifiche obbligatorie dopo il riavvio

Prima di impostare `GGML_CUDA_P2P=1`:

```bash
cat /proc/cmdline
journalctl -b -k --no-pager | grep -i -E 'iommu|amd-vi'
nvidia-smi topo -m
nvidia-smi topo -p2p p
nvidia-smi topo -p2p r
nvidia-smi topo -p2p w
```

### Gate

- Se `r` e `w` diventano `OK`, eseguire un A/B con lo stesso modello, prompt,
  seed, contesto e 512 token: prima senza, poi con `GGML_CUDA_P2P=1`.
- Se restano `CNS`/`NS`, non abilitare P2P nel runtime. IOMMU off non e'
  sufficiente e il passo successivo e' valutare la patch driver usata sul
  riferimento Enrico (`610.43.02` open), verificandone prima la compatibilita'
  con RTX 4060 Ti, kernel e user-space correnti.
- Non installare o copiare la patch di Enrico alla cieca.

## Esito del test

Il riavvio con `amd_iommu=off` e' riuscito, ma non ha abilitato il P2P:

- parametro presente in `/proc/cmdline`;
- nessun gruppo IOMMU esposto;
- topologia ancora `PHB`;
- `nvidia-smi topo -p2p p`: `NS`;
- `nvidia-smi topo -p2p r` e `w`: `CNS` in entrambe le direzioni;
- entrambe le GPU, Redis e Ollama operativi senza errori systemd.

Il gate non e' quindi superato: `GGML_CUDA_P2P=1` non e' stato abilitato.
Il checkpoint successivo e' in
`docs/EURI_P620_P2P_DRIVER_HANDOFF_2026-08-25.md`.
