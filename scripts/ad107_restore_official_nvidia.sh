#!/usr/bin/env bash
set -euo pipefail

readonly expected_kernel="7.0.11-76070011-generic"
readonly expected_version="610.43.02"
readonly depmod_rule="/etc/depmod.d/aikitoria-p2p.conf"
readonly disabled_rule="/etc/depmod.d/aikitoria-p2p.conf.disabled-ad107"
readonly official_module="/lib/modules/${expected_kernel}/updates/dkms/nvidia.ko.zst"
readonly overlay_dir="/lib/modules/${expected_kernel}/updates/aikitoria-p2p"
readonly overlay_backup_dir="/var/lib/euri-nvidia-backup/aikitoria-p2p-${expected_kernel}-ad107"

if [[ ${EUID} -ne 0 ]]; then
    echo "Errore: eseguire questo script con sudo." >&2
    exit 1
fi

if [[ "$(uname -r)" != "${expected_kernel}" ]]; then
    echo "Errore: kernel attivo diverso da ${expected_kernel}." >&2
    exit 1
fi

if [[ ! -f "${official_module}" ]]; then
    echo "Errore: modulo DKMS ufficiale assente: ${official_module}" >&2
    exit 1
fi

if [[ "$(modinfo -F version "${official_module}")" != "${expected_version}" ]]; then
    echo "Errore: versione del modulo DKMS diversa da ${expected_version}." >&2
    exit 1
fi

if [[ -e "${depmod_rule}" && -e "${disabled_rule}" ]]; then
    echo "Errore: regola attiva e backup esistono contemporaneamente." >&2
    exit 1
fi

if [[ -e "${overlay_dir}" && -e "${overlay_backup_dir}" ]]; then
    echo "Errore: overlay attivo e backup esterno esistono contemporaneamente." >&2
    exit 1
fi

moved_rule=false
if [[ -f "${depmod_rule}" ]]; then
    mv -- "${depmod_rule}" "${disabled_rule}"
    moved_rule=true
elif [[ -f "${disabled_rule}" ]]; then
    echo "Regola overlay gia' disabilitata; riprendo la preparazione."
else
    echo "Errore: non trovo ne' la regola attiva ne' il suo backup AD107." >&2
    exit 1
fi

moved_overlay=false
if [[ -d "${overlay_dir}" ]]; then
    mkdir -p -- "$(dirname "${overlay_backup_dir}")"
    mv -- "${overlay_dir}" "${overlay_backup_dir}"
    moved_overlay=true
elif [[ -d "${overlay_backup_dir}" ]]; then
    echo "Directory overlay gia' conservata fuori da /lib/modules; riprendo."
else
    if [[ "${moved_rule}" == true ]]; then
        mv -- "${disabled_rule}" "${depmod_rule}"
    fi
    echo "Errore: non trovo ne' l'overlay attivo ne' il backup esterno AD107." >&2
    exit 1
fi

depmod -a "${expected_kernel}"

selected_module="$(modinfo -k "${expected_kernel}" -F filename nvidia)"
if [[ "${selected_module}" != "${official_module}" ]]; then
    if [[ "${moved_overlay}" == true ]]; then
        mv -- "${overlay_backup_dir}" "${overlay_dir}"
    fi
    if [[ "${moved_rule}" == true ]]; then
        mv -- "${disabled_rule}" "${depmod_rule}"
    fi
    depmod -a "${expected_kernel}"
    echo "Errore: depmod non ha selezionato il modulo DKMS; overlay e regola ripristinati." >&2
    exit 1
fi

kernelstub -d "amd_iommu=on"
kernelstub -d "iommu=pt"
update-initramfs -u -k "${expected_kernel}"

echo
echo "AD107 preparato correttamente."
echo "Modulo selezionato per il prossimo boot: ${selected_module}"
echo "Versione: $(modinfo -k "${expected_kernel}" -F version nvidia)"
echo "Overlay conservato in: ${overlay_backup_dir}"
echo "Ora eseguire: sudo reboot"
