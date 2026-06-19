"""
Obsidian Sync — Ponte bidirezionale tra Redis e Obsidian Vault.

Sync Out: Scrive Memory e Insight come file Markdown.
Sync In: Watchdog osserva il vault e aggiorna Redis se tu modifichi/crei file.
"""
import os
import time
import threading
from pathlib import Path
from loguru import logger
import yaml
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

import config
from utils.date_utils import from_timestamp
from core.domain_gater import assign_domain
from core.pulse import pulse_emit

# Per evitare loop infiniti (Euri scrive -> Watchdog legge -> Euri scrive)
# manteniamo un set dei file modificati da Euri negli ultimi secondi.
_ignore_files = set()


def _mark_ignored(filepath: str):
    """Segna un file come 'scritto da Euri' per ignorare l'evento watchdog."""
    _ignore_files.add(filepath)
    # Rimuovi dal set dopo 2 secondi (sufficienti per far scattare l'evento in locale)
    threading.Timer(2.0, lambda: _ignore_files.discard(filepath)).start()


# ── SYNC OUT (Euri → Obsidian) ───────────────────────────────────────────

def write_memory(doc: dict):
    """Scrive una memory su Obsidian."""
    if not config.OBSIDIAN_SYNC_ENABLED:
        return
        
    vault_path = Path(config.OBSIDIAN_VAULT_PATH)
    domain = doc.get("domain", "generale")
    mem_id = doc["id"]
    
    # Crea la cartella del dominio
    dir_path = vault_path / "Memories" / domain
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"Obsidian Sync: impossibile creare cartella {dir_path}: {e}")
        return
        
    # Format the date for the filename and content
    dt = from_timestamp(doc["created_at"])
    date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
    safe_date = dt.strftime("%Y%m%d_%H%M%S")
    
    filepath = dir_path / f"Memory_{safe_date}_{mem_id[:8]}.md"
    
    # Prepara il frontmatter YAML
    frontmatter = {
        "id": mem_id,
        "type": "memory",
        "domain": domain,
        "source": doc.get("source", "user"),
        "created_at": date_str
    }
    if doc.get("tags"):
        frontmatter["tags"] = doc["tags"]
        
    content = f"""---
{yaml.dump(frontmatter, default_flow_style=False, sort_keys=False)}---
# Memoria ({date_str})

{doc["content"]}
"""
    try:
        _mark_ignored(str(filepath.absolute()))
        filepath.write_text(content, encoding="utf-8")
        logger.debug(f"Obsidian Sync: Scritta memoria {filepath.name}")
    except Exception as e:
        logger.error(f"Obsidian Sync: errore scrittura {filepath}: {e}")


def write_insight(doc: dict):
    """Scrive un insight promosso su Obsidian."""
    if not config.OBSIDIAN_SYNC_ENABLED:
        return
        
    vault_path = Path(config.OBSIDIAN_VAULT_PATH)
    ins_id = doc["id"]
    
    dir_path = vault_path / "Insights"
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error(f"Obsidian Sync: impossibile creare cartella {dir_path}: {e}")
        return
        
    # Bug fix V2.17 (28/05/2026): usare promoted_at, non created_at.
    # Il seed di un CANDIDATE può avere settimane prima della promotion; il
    # nome file Obsidian deve riflettere l'evento di promozione, non l'origine.
    # Fallback a created_at per retrocompat con eventuali doc legacy.
    ts = doc.get("promoted_at") or doc["created_at"]
    dt = from_timestamp(ts)
    date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
    safe_date = dt.strftime("%Y%m%d_%H%M%S")

    filepath = dir_path / f"Insight_{safe_date}.md"
    
    frontmatter = {
        "id": ins_id,
        "type": "insight",
        "status": doc.get("status", "promoted"),
        "created_at": date_str
    }
    
    # Crea i Wiki-Link
    dom_a = doc.get("domain_a", "sconosciuto")
    dom_b = doc.get("domain_b", "sconosciuto")
    
    content = f"""---
{yaml.dump(frontmatter, default_flow_style=False, sort_keys=False)}---
# Insight Promosso
Collegamento scoperto tra: [[{dom_a}]] e [[{dom_b}]]

{doc["content"]}

---
*Generato dal Dream Engine di Euri il {date_str}*
"""
    try:
        _mark_ignored(str(filepath.absolute()))
        filepath.write_text(content, encoding="utf-8")
        logger.info(f"Obsidian Sync: Scritto insight {filepath.name} nel Vault")
    except Exception as e:
        logger.error(f"Obsidian Sync: errore scrittura insight {filepath}: {e}")


# ── SYNC IN (Obsidian → Euri) ────────────────────────────────────────────

class ObsidianHandler(FileSystemEventHandler):
    """Inoltra gli eventi dei file markdown al sync_manager."""
    def __init__(self, sync_manager):
        self.sm = sync_manager
        
    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".md"):
            self.sm.handle_file_event(event.src_path)
            
    def on_modified(self, event):
        if not event.is_directory and event.src_path.endswith(".md"):
            self.sm.handle_file_event(event.src_path)

            
class ObsidianSyncManager:
    def __init__(self, r, embedder):
        self.r = r
        self.embedder = embedder
        self.observer = None
        self._lock = threading.Lock()
        
    def start_watcher(self):
        if not config.OBSIDIAN_SYNC_ENABLED:
            return
            
        vault_path = Path(config.OBSIDIAN_VAULT_PATH)
        # Assicurati che Dropzone esista
        (vault_path / "Dropzone").mkdir(parents=True, exist_ok=True)
        
        self.observer = Observer()
        event_handler = ObsidianHandler(self)
        self.observer.schedule(event_handler, str(vault_path), recursive=True)
        self.observer.start()
        logger.info(f"Obsidian Sync: Watcher avviato su {vault_path}")

    def stop_watcher(self):
        if self.observer:
            self.observer.stop()
            self.observer.join()
            
    def handle_file_event(self, filepath: str):
        """Elabora un file markdown appena salvato dall'utente."""
        if filepath in _ignore_files:
            return

        # Senso "vault": una modifica dal mondo (Stefano edita una nota), non un
        # self-write di Euri (già filtrato sopra). source=extero.
        pulse_emit(self.r, "vault", "extero", "change",
                   payload={"file": os.path.basename(filepath)}, salience=0.6)

        # Per evitare colli di bottiglia da salvataggi continui dell'editor,
        # facciamo un debounce o eseguiamo in un thread staccato.
        threading.Thread(target=self._process_file, args=(filepath,), daemon=True).start()
        
    def _process_file(self, filepath: str):
        time.sleep(1.0) # Debounce per permettere al file system di sbloccare il file
        try:
            path = Path(filepath)
            if not path.exists():
                return
                
            content = path.read_text(encoding="utf-8").strip()
            if not content:
                return
                
            # Parsiamo eventuale YAML frontmatter
            frontmatter = {}
            body = content
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    try:
                        frontmatter = yaml.safe_load(parts[1]) or {}
                        body = parts[2].strip()
                    except Exception:
                        pass
                        
            doc_id = frontmatter.get("id")
            doc_type = frontmatter.get("type")
            
            # 1. È una memory esistente modificata a mano
            if doc_type == "memory" and doc_id:
                logger.info(f"Obsidian Sync: Rilevata modifica manuale alla memoria {doc_id[:8]}")
                # Aggiorniamo Redis
                vec = self.embedder.encode(body)
                if vec is not None:
                    # mark-after-act (Codex #6): content ed embedding sono DUE viste della stessa
                    # memoria — se il secondo write fallisce divergono (keyword vede un testo, il
                    # vector un altro). Pipeline transazionale: vanno insieme o niente.
                    try:
                        pipe = self.r.pipeline(transaction=True)
                        pipe.json().set(f"euri:memory:{doc_id}", "$.content", body)
                        pipe.json().set(f"euri:memory:{doc_id}", "$.embedding", vec.tolist())
                        pipe.execute()
                    except Exception as e:
                        logger.warning(
                            f"INTEGRITÀ Obsidian sync: content/embedding di {doc_id[:8]} "
                            f"non aggiornati atomicamente: {e}"
                        )
                return
                
            # 2. È un file nuovo inserito dall'utente (es. nella Dropzone)
            if not doc_type:
                # Controlliamo se lo avevamo già inglobato
                # Fallback per ora: se non ha frontmatter, lo inglobiamo come memoria testuale pura.
                # Nota: per evitare di ingerire lo stesso file 100 volte a ogni salvataggio,
                # modifichiamo il file aggiungendo l'ID.
                
                logger.info(f"Obsidian Sync: Nuovo file rilevato: {path.name}. Ingestion...")
                from core.memory_manager import MemoryManager
                mm = MemoryManager(self.r, self.embedder)
                
                # Assegna dominio tramite directory genitore o auto-assign
                parent_dir = path.parent.name
                if parent_dir and parent_dir not in ("Dropzone", "Memories", "Insights", "EuriVault"):
                    domain = parent_dir.lower()
                else:
                    domain = assign_domain(body)
                    
                # Salva su Redis
                mid = mm.save_memory(
                    content=body, 
                    category="obsidian", 
                    source="obsidian_vault"
                )
                
                # Sovrascriviamo il dominio in Redis (il memory_manager farà assign_domain, noi lo forziamo se è dalla directory)
                if parent_dir not in ("Dropzone", "Memories", "Insights", "EuriVault"):
                    self.r.json().set(f"euri:memory:{mid}", "$.domain", domain)
                else:
                    # Usiamo quello calcolato dal memory_manager
                    doc = self.r.json().get(f"euri:memory:{mid}", "$")
                    if doc:
                        domain = doc[0].get("domain", "generale")

                # Ora aggiorniamo il file su disco mettendo il frontmatter così non lo processiamo più come "nuovo"
                frontmatter_str = yaml.dump({
                    "id": mid,
                    "type": "memory",
                    "domain": domain,
                    "source": "obsidian_vault"
                }, default_flow_style=False, sort_keys=False)
                
                new_content = f"---\n{frontmatter_str}---\n{body}"
                
                _mark_ignored(str(path.absolute()))
                path.write_text(new_content, encoding="utf-8")
                logger.success(f"Obsidian Sync: Ingestion completata per {path.name} (dominio: {domain})")

        except Exception as e:
            logger.error(f"Obsidian Sync: errore processamento {filepath}: {e}")
