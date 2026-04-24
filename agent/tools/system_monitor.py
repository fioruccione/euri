"""
System monitor tools per Euri — Workstation Linux (Pop!_OS, doppia RTX 4060 Ti).
Usa psutil per CPU/RAM/processi; shutil per disco; subprocess per uptime e nvidia-smi.
"""
import shutil
import subprocess
import time
from agent.executor import ToolResult

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False


def tool_cpu_usage(params: dict, **kwargs) -> ToolResult:
    process_name = params.get("process_name")
    try:
        if process_name:
            if not _HAS_PSUTIL:
                return ToolResult(success=False, output="psutil non disponibile per filtrare per processo.")
            total = 0.0
            count = 0
            for proc in psutil.process_iter(["name", "cpu_percent"]):
                if process_name.lower() in proc.info["name"].lower():
                    total += proc.cpu_percent(interval=0.1)
                    count += 1
            if count == 0:
                return ToolResult(success=True, output=f"Nessun processo '{process_name}' trovato in esecuzione.")
            return ToolResult(success=True, output=f"Il processo '{process_name}' usa il {total:.1f}% della CPU ({count} istanze).")

        if _HAS_PSUTIL:
            pct = psutil.cpu_percent(interval=0.5)
        else:
            # Fallback: usa top -l 2 (macOS)
            out = subprocess.check_output(
                ["top", "-l", "2", "-n", "0", "-stats", "cpu"],
                text=True, timeout=5
            )
            # L'ultimo campione ha "CPU usage: X.X% user, Y.Y% sys, Z.Z% idle"
            lines = [l for l in out.splitlines() if "CPU usage" in l]
            if not lines:
                return ToolResult(success=False, output="Impossibile leggere la CPU.")
            last = lines[-1]
            idle_str = last.split("idle")[0].rsplit(",", 1)[-1].strip().replace("%", "")
            pct = round(100 - float(idle_str), 1)

        return ToolResult(success=True, output=f"La CPU è al {pct:.1f}% di utilizzo.")
    except Exception as e:
        return ToolResult(success=False, output="Errore durante la lettura della CPU.", error=str(e))


def tool_ram_usage(params: dict, **kwargs) -> ToolResult:
    try:
        if _HAS_PSUTIL:
            vm = psutil.virtual_memory()
            total_gb = vm.total / 1024**3
            used_gb = vm.used / 1024**3
            free_gb = vm.available / 1024**3
            pct = vm.percent
        else:
            # Fallback: vm_stat + sysctl
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True, timeout=3)
            total_bytes = int(out.strip())
            total_gb = total_bytes / 1024**3

            vm_out = subprocess.check_output(["vm_stat"], text=True, timeout=3)
            page_size = 4096
            lines = {l.split(":")[0].strip(): l.split(":")[1].strip().rstrip(".") for l in vm_out.splitlines() if ":" in l}
            free_pages = int(lines.get("Pages free", 0))
            inactive_pages = int(lines.get("Pages inactive", 0))
            free_gb = (free_pages + inactive_pages) * page_size / 1024**3
            used_gb = total_gb - free_gb
            pct = round(used_gb / total_gb * 100, 1)

        return ToolResult(
            success=True,
            output=f"RAM: {used_gb:.1f} GB usati su {total_gb:.0f} GB totali. Libera circa {free_gb:.1f} GB. Utilizzo al {pct:.0f}%.",
            raw_data={"total_gb": round(total_gb, 1), "used_gb": round(used_gb, 1), "free_gb": round(free_gb, 1), "percent": pct},
        )
    except Exception as e:
        return ToolResult(success=False, output="Errore durante la lettura della RAM.", error=str(e))


def tool_disk_usage(params: dict, **kwargs) -> ToolResult:
    """Legge sempre il volume radice '/'."""
    try:
        usage = shutil.disk_usage("/")
        total_gb = usage.total / 1024**3
        used_gb = usage.used / 1024**3
        free_gb = usage.free / 1024**3
        pct = round(used_gb / total_gb * 100, 1)
        return ToolResult(
            success=True,
            output=f"Disco principale: {used_gb:.0f} GB usati su {total_gb:.0f} GB. Liberi {free_gb:.0f} GB ({pct:.0f}% usato).",
            raw_data={"total_gb": round(total_gb, 1), "used_gb": round(used_gb, 1), "free_gb": round(free_gb, 1), "percent": pct},
        )
    except Exception as e:
        return ToolResult(success=False, output="Errore durante la lettura del disco.", error=str(e))


def tool_top_processes(params: dict, **kwargs) -> ToolResult:
    n = int(params.get("n", 5))
    sort_by = params.get("sort_by", "cpu")
    try:
        if _HAS_PSUTIL:
            attr = "cpu_percent" if sort_by == "cpu" else "memory_percent"
            # Prima passata per inizializzare i contatori cpu_percent
            try:
                list(psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]))
            except Exception:
                pass
            time.sleep(0.5)
            # Seconda passata: valori reali
            procs = []
            for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
                try:
                    info = proc.info
                    # cpu_percent può essere None per processi zombie/sistema
                    info["cpu_percent"] = info.get("cpu_percent") or 0.0
                    info["memory_percent"] = info.get("memory_percent") or 0.0
                    procs.append(info)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
            procs.sort(key=lambda p: p.get(attr) or 0, reverse=True)
            top = procs[:n]
            if sort_by == "cpu":
                lines = [f"{p['name'][:20]} — {p['cpu_percent']:.1f}% CPU" for p in top]
            else:
                lines = [f"{p['name'][:20]} — {p['memory_percent']:.1f}% RAM" for p in top]
        else:
            # Fallback: ps aux
            col = "pcpu" if sort_by == "cpu" else "pmem"
            out = subprocess.check_output(
                ["ps", "aux", "-r"],
                text=True, timeout=5
            )
            rows = out.strip().splitlines()[1:]  # salta header
            lines = []
            for row in rows[:n]:
                parts = row.split(None, 10)
                if len(parts) < 11:
                    continue
                cpu_p = parts[2]
                mem_p = parts[3]
                name = parts[10].split("/")[-1][:20]
                if sort_by == "cpu":
                    lines.append(f"{name} — {cpu_p}% CPU")
                else:
                    lines.append(f"{name} — {mem_p}% RAM")

        label = "CPU" if sort_by == "cpu" else "RAM"
        output = f"Top {n} processi per {label}: " + ". ".join(lines) + "."
        return ToolResult(success=True, output=output)
    except Exception as e:
        return ToolResult(success=False, output="Errore durante la lettura dei processi.", error=str(e))


def tool_uptime(params: dict, **kwargs) -> ToolResult:
    try:
        if _HAS_PSUTIL:
            boot_ts = psutil.boot_time()
            elapsed = time.time() - boot_ts
        else:
            out = subprocess.check_output(["sysctl", "-n", "kern.boottime"], text=True, timeout=3)
            # Output: "{ sec = 1712345678, usec = 0 } Mon Apr  1 10:00:00 2026"
            import re
            m = re.search(r"sec\s*=\s*(\d+)", out)
            if not m:
                return ToolResult(success=False, output="Impossibile leggere l'uptime.")
            boot_ts = int(m.group(1))
            elapsed = time.time() - boot_ts

        days = int(elapsed // 86400)
        hours = int((elapsed % 86400) // 3600)
        minutes = int((elapsed % 3600) // 60)

        parts = []
        if days:
            parts.append(f"{days} {'giorno' if days == 1 else 'giorni'}")
        if hours:
            parts.append(f"{hours} {'ora' if hours == 1 else 'ore'}")
        if minutes or not parts:
            parts.append(f"{minutes} {'minuto' if minutes == 1 else 'minuti'}")

        return ToolResult(success=True, output=f"La workstation è accesa da {', '.join(parts)}.")
    except Exception as e:
        return ToolResult(success=False, output="Errore durante la lettura dell'uptime.", error=str(e))


def tool_gpu_usage(params: dict, **kwargs) -> ToolResult:
    """Legge utilizzo GPU NVIDIA tramite nvidia-smi."""
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=5,
        )
        lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
        parts = []
        for i, line in enumerate(lines, 1):
            fields = [f.strip() for f in line.split(",")]
            if len(fields) >= 5:
                name, util, mem_used, mem_total, temp = fields[:5]
                parts.append(
                    f"GPU {i}: {name}, utilizzo {util}%, "
                    f"VRAM {mem_used} MB su {mem_total} MB, temperatura {temp}°C"
                )
        if parts:
            return ToolResult(success=True, output=". ".join(parts) + ".")
        return ToolResult(success=False, output="Nessuna GPU rilevata da nvidia-smi.")
    except FileNotFoundError:
        return ToolResult(success=False, output="nvidia-smi non trovato. GPU non leggibile.")
    except Exception as e:
        return ToolResult(success=False, output="Errore durante la lettura della GPU.", error=str(e))
