"""Migrazione una-tantum: i todo del silo euri:todo:* diventano memorie-impegno
(memorie con due_at + status), poi il silo viene smontato (chiavi + idx:todos).

Decisione di Stefano 13/07/2026: gli impegni si assorbono nel modello memoria
("tutto è memoria viva") invece di cucire il silo invisibile al piano
conversazionale. Il Poseidon (unico pending, scaduto dal 22/06) è ancora
attuale → riprogrammato a domani ore 09:00 con reminded_count azzerato, così
il reminder efferente lo consegna di nuovo alla prima presenza.

Idempotente: se non ci sono più chiavi euri:todo:* non fa nulla.
Da lanciare a daemon fermo (ricrea idx:memories per aggiungere il campo status).
"""
import json
import sys
from datetime import timedelta

sys.path.insert(0, "/home/fio/Euri")

from loguru import logger

from core.embedder import Embedder
from core.memory_manager import MemoryManager
from utils.date_utils import now
from utils.redis_client import get_client, init_indexes


def main():
    r = get_client()
    todo_keys = list(r.scan_iter(match="euri:todo:*"))
    if not todo_keys:
        print("Nessun euri:todo:* da migrare — già fatto?")
    else:
        print(f"Da migrare: {len(todo_keys)} todo")

    # Migra/crea l'indice memorie col campo status PRIMA di scrivere impegni
    init_indexes(r)

    embedder = Embedder()
    embedder.load()
    memory = MemoryManager(r, embedder)

    tomorrow_9 = (now() + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)

    for key in todo_keys:
        # r.json().get torna già decodificato (e registra i callback JSON sul client,
        # quindi niente execute_command grezzo qui: dopo il primo uso cambierebbe forma)
        data = r.json().get(key, "$")
        if not data:
            r.delete(key)
            continue
        old = data[0]
        content = old.get("content", "").strip()
        status = old.get("status", "pending")
        if not content:
            r.delete(key)
            continue

        historical_fields = {}
        if old.get("created_at"):
            historical_fields["created_at"] = old["created_at"]
        if status != "pending":
            historical_fields["completed_at"] = old.get("completed_at")
            if old.get("due_at"):
                historical_fields["due_at"] = old["due_at"]

        if status == "pending":
            # Poseidon: ancora attuale → riprogrammato, reminder riarmato
            due_at = tomorrow_9
            mid = memory.save_memory(content, category="impegno", source="user",
                                     tags=old.get("tags") or [],
                                     due_at=due_at, status="pending",
                                     final_fields=historical_fields)
        else:
            mid = memory.save_memory(content, category="impegno", source="user",
                                     tags=old.get("tags") or [],
                                     status="done",
                                     final_fields=historical_fields)
        if not mid:
            logger.error(f"Migrazione fallita per {key} — silo NON toccato")
            return 1

        due_str = tomorrow_9.strftime("%d/%m %H:%M") if status == "pending" else "—"
        print(f"  {key} → euri:memory:{mid}  [{status}] due={due_str}  {content[:60]}")
        r.delete(key)

    try:
        r.ft("idx:todos").dropindex()
        print("idx:todos smontato")
    except Exception:
        print("idx:todos già assente")

    pend = memory.get_pending_todos()
    print(f"\nVerifica post-migrazione: {len(pend)} impegni pending su idx:memories")
    for t in pend:
        print(f"  [{t.get('status')}] due={t.get('_due_at')}  {t.get('content','')[:60]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
