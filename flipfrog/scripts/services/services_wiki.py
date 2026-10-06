"""Explicaciones en español para los servicios systemd que
services_popup.py muestra por default -- ver CLAUDE.md sección
servicios systemd. No cubre las ~380 unidades completas, solo cae a
describe() para el resto."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from i18n import t

SERVICE_WIKI = {
    # ---- Núcleo systemd / arranque (críticos, no tocar) -----------------
    "systemd-journald.service": t("servicios", "desc_systemd_journald"),
    "systemd-logind.service": t("servicios", "desc_systemd_logind"),
    "systemd-udevd.service": t("servicios", "desc_systemd_udevd"),
    "systemd-resolved.service": t("servicios", "desc_systemd_resolved"),
    "systemd-timesyncd.service": t("servicios", "desc_systemd_timesyncd"),
    "systemd-userdbd.service": t("servicios", "desc_systemd_userdbd"),
    "systemd-user-sessions.service": t("servicios", "desc_systemd_user_sessions"),
    "systemd-remount-fs.service": t("servicios", "desc_systemd_remount_fs"),
    "systemd-sysctl.service": t("servicios", "desc_systemd_sysctl"),
    "systemd-modules-load.service": t("servicios", "desc_systemd_modules_load"),
    "systemd-random-seed.service": t("servicios", "desc_systemd_random_seed"),
    "systemd-boot-random-seed.service": t("servicios", "desc_systemd_boot_random_seed"),
    "systemd-vconsole-setup.service": t("servicios", "desc_systemd_vconsole_setup"),
    "systemd-tmpfiles-setup.service": t("servicios", "desc_systemd_tmpfiles_setup"),
    "systemd-tmpfiles-setup-dev.service": t("servicios", "desc_systemd_tmpfiles_setup_dev"),
    "systemd-tmpfiles-setup-dev-early.service": t("servicios", "desc_systemd_tmpfiles_setup_dev_early"),
    "systemd-udev-trigger.service": t("servicios", "desc_systemd_udev_trigger"),
    "systemd-udev-load-credentials.service": t("servicios", "desc_systemd_udev_load_credentials"),
    "systemd-userdb-load-credentials.service": t("servicios", "desc_systemd_userdb_load_credentials"),
    "systemd-update-utmp.service": t("servicios", "desc_systemd_update_utmp"),
    "systemd-journal-flush.service": t("servicios", "desc_systemd_journal_flush"),
    "kmod-static-nodes.service": t("servicios", "desc_kmod_static_nodes"),
    "lvm2-monitor.service": t("servicios", "desc_lvm2_monitor"),
    "alsa-restore.service": t("servicios", "desc_alsa_restore"),

    # ---- Arranque gráfico / Plymouth -------------------------------------
    "plymouth-start.service": t("servicios", "desc_plymouth_start"),
    "plymouth-read-write.service": t("servicios", "desc_plymouth_read_write"),
    "plymouth-quit.service": t("servicios", "desc_plymouth_quit"),
    "plymouth-quit-wait.service": t("servicios", "desc_plymouth_quit_wait"),
    "sddm.service": t("servicios", "desc_sddm"),

    # ---- Red -------------------------------------------------------------
    "NetworkManager.service": t("servicios", "desc_networkmanager"),
    "NetworkManager-wait-online.service": t("servicios", "desc_networkmanager_wait_online"),
    "NetworkManager-dispatcher.service": t("servicios", "desc_networkmanager_dispatcher"),
    "wpa_supplicant.service": t("servicios", "desc_wpa_supplicant"),
    "avahi-daemon.service": t("servicios", "desc_avahi_daemon"),
    "ufw.service": t("servicios", "desc_ufw"),

    # ---- Hardware / sesión --------------------------------------------
    "bluetooth.service": t("servicios", "desc_bluetooth"),
    "upower.service": t("servicios", "desc_upower"),
    "power-profiles-daemon.service": t("servicios", "desc_power_profiles_daemon"),
    "udisks2.service": t("servicios", "desc_udisks2"),
    "polkit.service": t("servicios", "desc_polkit"),
    "coolercontrold.service": t("servicios", "desc_coolercontrold"),
    "ananicy-cpp.service": t("servicios", "desc_ananicy_cpp"),
    "mssql-server.service": t("servicios", "desc_mssql_server"),
    "limine-snapper-sync.service": t("servicios", "desc_limine_snapper_sync"),
    "getty@.service": t("servicios", "desc_getty"),

    # ---- Bus de mensajes --------------------------------------------------
    "dbus-broker.service": t("servicios", "desc_dbus_broker"),
}

GENERIC_PREFIX = t("servicios", "generic_prefix")


def describe(name, systemd_description):
    """Texto final para el tooltip de una fila -- prioriza la explicación
    curada; si no hay, cae a la descripción real de systemd (con un
    prefijo que aclara que no está curada); si tampoco hay eso (varios
    `systemd-*` internos vienen con Description= vacío), un mensaje
    genérico. Nunca devuelve texto vacío."""
    curated = SERVICE_WIKI.get(name)
    if curated:
        return curated
    if systemd_description:
        return GENERIC_PREFIX + systemd_description
    return t("servicios", "sin_descripcion")
