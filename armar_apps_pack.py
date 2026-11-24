#!/usr/bin/env python3
"""Arma «Apps del pack»: un zip chico por app de cada pack y apps-pack.json.

OQB Updater (Apps › Del pack) lee apps-pack.json, mira qué pack hay en la SD
(su clave es el nombre del zip del pack) y muestra cada app de ese pack con su
estado. Cada app se instala desde su propio zip: reinstalar una sola baja solo
esa. Nada de Atmosphère, hekate ni del arranque va aquí: eso es el pack completo.

Uso:
    python3 armar_apps_pack.py SALIDA CLAVE=CARPETA [CLAVE=CARPETA ...] [--base URL]

    CLAVE    nombre del zip del pack (OQB-HATS-PACK-OC-23.0.zip)
    CARPETA  el pack descomprimido
    SALIDA   donde quedan los zips y apps-pack.json
    --base   de donde baja la app los zips (por defecto, el release apps-pack)

Un zip se llama por su contenido (id-crc.zip): si una app no cambia entre una
pasada y otra, su zip es el mismo y la app no la ve como nueva. Los dos packs
comparten el zip de las apps que son iguales.

Cada archivo lleva su modo:
    0  siempre: se escribe al instalar y cuenta para el estado;
    1  si falta: ajustes; solo si no existe, para no pisar los del usuario;
    2  si es nueva: los boot2.flag; solo si la app no estaba (si alguien
       apagó un sysmodule, actualizarlo no lo vuelve a prender).
"""

import json
import os
import struct
import sys
import zipfile
import zlib

BASE = "https://github.com/ohquebacan/OQB-HATS-PACK/releases/download/apps-pack/"

# id, nombre, tipo, reinicio, rutas (prefijos). El primero que encaja se lleva el archivo.
GRUPOS = [
    ("sphaira", "sphaira", "Menú Homebrew", False, ["hbmenu.nro", "switch/sphaira/"], "switch/sphaira/sphaira.nro"),
    ("horizon-oc", "Horizon-OC", "Sysmodule y overlay", True,
     ["atmosphere/contents/00FF0000636C6BFF/", "switch/.overlays/horizon-oc-overlay.ovl", "config/horizon-oc/",
      "config/ultrahand/assets/notifications/hoc.rgba"], "switch/.overlays/horizon-oc-overlay.ovl"),
    ("sys-clk", "sys-clk", "Sysmodule y overlay", True,
     ["atmosphere/contents/00FF0000636C6BFF/", "switch/.overlays/sys-clk-overlay.ovl", "switch/sys-clk-manager.nro",
      "config/sys-clk/"], "switch/.overlays/sys-clk-overlay.ovl"),
    ("saltynx", "SaltyNX", "Sysmodule", True,
     ["atmosphere/contents/0000000000534C56/", "SaltySD/", "atmosphere/exefs_patches/SaltyNX_Fixes/"]),
    ("emuiibo", "emuiibo", "Sysmodule y overlay", True,
     ["atmosphere/contents/0100000000000352/", "switch/.overlays/emuiibo.ovl", "emuiibo/"], "switch/.overlays/emuiibo.ovl"),
    ("missioncontrol", "MissionControl", "Sysmodule", True,
     ["atmosphere/contents/010000000000bd00/", "config/MissionControl/"]),
    ("sys-patch", "sys-patch", "Sysmodule y overlay", True,
     ["atmosphere/contents/420000000000000B/", "switch/.overlays/sys-patch-overlay.ovl"], "switch/.overlays/sys-patch-overlay.ovl"),
    ("ultrahand", "Ultrahand", "Menú de overlays", True,
     ["switch/.overlays/ovlmenu.ovl", "atmosphere/contents/420000000007E51A/", "atmosphere/contents/420000000007E51B/",
      "config/ultrahand/", "switch/Ultrahand-Reload/"], "switch/.overlays/ovlmenu.ovl"),
    ("noexes", "Noexes", "Sysmodule", True, ["atmosphere/contents/054e4f4558454000/"]),
    ("breeze", "Breeze", "Trucos", False, ["switch/breeze/"], "switch/breeze/Breeze.nro"),
    ("breezehand", "Breezehand", "Overlay", False, ["switch/.overlays/breezehand.ovl"]),
    ("edizon", "EdiZon SE", "Trucos", False, ["switch/EdiZon/", "switch/.overlays/ovlEdiZon.ovl"], "switch/EdiZon/EdiZon.nro"),
    ("nxthemes", "NXThemes Installer", "Temas", False, ["switch/NXThemesInstaller.nro", "themes/"], "switch/NXThemesInstaller.nro"),
    ("dbi", "DBI", "Instalador", False, ["switch/DBI/"], "switch/DBI/DBI.nro"),
    ("goldleaf", "Goldleaf", "Instalador", False, ["switch/Goldleaf/"]),
    ("appstore", "Homebrew App Store", "Tienda", False, ["switch/appstore/"]),
    ("quickntp", "QuickNTP", "Overlay", False, ["switch/.overlays/QuickNTP.ovl", "config/quickntp.ini"], "switch/.overlays/QuickNTP.ovl"),
    ("status-monitor", "Status Monitor", "Overlay", False,
     ["switch/.overlays/Status-Monitor-Overlay.ovl", "config/status-monitor/"], "switch/.overlays/Status-Monitor-Overlay.ovl"),
    ("fpslocker", "FPSLocker", "Overlay", False, ["switch/.overlays/FPSLocker.ovl"]),
    ("ovl-sysmodules", "Sysmodules (overlay)", "Overlay", False, ["switch/.overlays/ovlSysmodules.ovl"]),
    ("akira", "Akira", "App", False, ["switch/akira.nro"]),
    ("jksv", "JKSV BACÁN", "Partidas", False, ["switch/JKSV.nro"]),
    ("checkpoint", "Checkpoint", "Partidas", False, ["switch/Checkpoint.nro"]),
    ("linkalho", "Linkalho", "App", False, ["switch/linkalho.nro"]),
    ("themezer", "Themezer-NX", "Temas", False, ["switch/themezer-nx.nro"]),
    ("daybreak", "Daybreak", "Firmware", False, ["switch/daybreak.nro"]),
    ("haze", "Haze (USB a la PC)", "App", False, ["switch/haze.nro"]),
    ("benchmark-toolbox", "Benchmark Toolbox", "Rendimiento", False, ["switch/Benchmark-Toolbox.nro"]),
    ("battery-desync", "Battery Desync Fix", "Sistema", False, ["switch/battery_desync_fix_v1.5.1.nro"]),
    ("90dns", "90DNS Setter OQB", "Sistema", False, ["switch/OQB-90DNS.nro"]),
    ("90dns-tester", "90DNS Tester", "Sistema", False, ["switch/Switch_90DNS_tester.nro"]),
    ("reboot-hekate", "Reboot to Hekate", "Sistema", False, ["switch/reboot_to_hekate.nro"]),
    ("reboot-payload", "Reboot to Payload", "Sistema", False, ["switch/reboot_to_payload.nro"]),
    ("reset-parental", "Reset Parental Controls", "Sistema", False, ["switch/reset_parental_controls.nro"]),
]

# Lo que nunca va: arranque, Atmosphère, hekate, payloads y la propia app
# (se actualiza sola desde Inicio; reemplazarla mientras corre no conviene).
FUERA = [
    "atmosphere/package3", "atmosphere/stratosphere.romfs", "atmosphere/kips/", "atmosphere/kip_patches/",
    "atmosphere/exefs_patches/", "atmosphere/config", "atmosphere/hosts/", "atmosphere/fatal_errors/",
    "atmosphere/flags/", "atmosphere/hbl", "atmosphere/reboot_payload.bin", "atmosphere/exosphere.bin",
    "atmosphere/contents/010000000000000D/", "atmosphere/contents/0100000000001013/", "atmosphere/patches.ini",
    "bootloader/", "boot.dat", "boot.ini", "fusee.bin", "payload.bin", "update.bin", "exosphere.ini", "warmboot_mariko/",
    "config/aio-switch-updater/", "config/fusecheck/", "config/lockpick_rcm_pro/", "NSP FORWARDERS NRO/",
    "switch/OQB-updater.nro", "switch/tinfoil/", "hekate_ctcaer",
]

# Nombre de la app en nx-links.json, para usar su ícono.
CATALOGO = {
    "sphaira": "sphaira", "akira": "Akira", "jksv": "JKSV BACAN", "checkpoint": "Checkpoint", "dbi": "DBI",
    "goldleaf": "Goldleaf", "edizon": "EdiZon SE", "breezehand": "Breezehand", "ultrahand": "Ultrahand Overlay",
    "emuiibo": "emuiibo", "missioncontrol": "MissionControl", "sys-patch": "sys-patch", "status-monitor": "Status Monitor Overlay",
    "fpslocker": "FPSLocker", "quickntp": "QuickNTP", "nxthemes": "NXThemes Installer", "themezer": "Themezer-NX",
    "linkalho": "Linkalho", "appstore": "Homebrew App Store", "horizon-oc": "Horizon-OC", "sys-clk": "sys-clk",
    "saltynx": "SaltyNX", "breeze": "Breeze", "ovl-sysmodules": "ovl-sysmodules",
}


def version_nro(ruta):
    """display_version del NACP de un .nro u .ovl, o ''."""
    with open(ruta, "rb") as f:
        b = f.read()
    if b[0x10:0x14] != b"NRO0":
        return ""
    fin = struct.unpack("<I", b[0x18:0x1C])[0]
    a = b[fin:]
    if a[:4] != b"ASET":
        return ""
    off, tam = struct.unpack("<QQ", a[0x18:0x28])
    n = a[off:off + tam]
    if len(n) < 0x3070:
        return ""
    return n[0x3060:0x3070].split(b"\0")[0].decode("utf-8", "replace").strip()


def crc_de(ruta):
    c = 0
    with open(ruta, "rb") as f:
        for trozo in iter(lambda: f.read(1 << 20), b""):
            c = zlib.crc32(trozo, c)
    return c & 0xFFFFFFFF


def modo_de(rel):
    if rel.startswith("config/") or rel.endswith("/dbi.config") or rel.endswith("/locations.conf"):
        return 1
    if rel.endswith("/flags/boot2.flag"):
        return 2
    return 0


def repartir(carpeta):
    archivos = []
    for dp, dn, fn in os.walk(carpeta):
        dn.sort()
        for f in sorted(fn):
            if f.startswith("._") or f == ".DS_Store":
                continue
            archivos.append(os.path.relpath(os.path.join(dp, f), carpeta).replace(os.sep, "/"))
    grupos, sueltos = {}, []
    # Un TID que es de dos grupos (sys-clk / Horizon-OC) va al que tenga su overlay en este pack.
    presentes = set(archivos)
    for rel in archivos:
        if any(rel == p or rel.startswith(p) for p in FUERA):
            continue
        for g in GRUPOS:
            gid, rutas = g[0], g[4]
            principal = g[5] if len(g) > 5 else None
            if principal and principal.endswith(".ovl") and gid in ("horizon-oc", "sys-clk") and principal not in presentes:
                continue
            if any(rel == p or rel.startswith(p) for p in rutas):
                grupos.setdefault(gid, []).append(rel)
                break
        else:
            sueltos.append(rel)
    return grupos, sueltos


def main():
    args = sys.argv[1:]
    base = BASE
    if "--base" in args:
        i = args.index("--base")
        base = args[i + 1]
        del args[i:i + 2]
    if len(args) < 2:
        print(__doc__)
        return 2
    salida, packs = args[0], [a.split("=", 1) for a in args[1:]]
    os.makedirs(salida, exist_ok=True)
    catalogo = {}
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "nx-links.json"), encoding="utf-8") as f:
            catalogo = json.load(f).get("apps", {})
    except OSError:
        pass
    por_id = {g[0]: g for g in GRUPOS}
    manifiesto = {"version": 1, "base": base, "packs": {}, "apps": {}}
    for clave, carpeta in packs:
        grupos, sueltos = repartir(carpeta)
        if sueltos:
            print(f"{clave}: sin grupo (no van):")
            for s in sueltos:
                print("   ", s)
        lista = []
        for gid in [g[0] for g in GRUPOS if g[0] in grupos]:
            g = por_id[gid]
            filas = []
            for rel in grupos[gid]:
                ruta = os.path.join(carpeta, rel)
                v = version_nro(ruta) if rel.lower().endswith((".nro", ".ovl")) else ""
                filas.append([rel, f"{crc_de(ruta):08x}", os.path.getsize(ruta), modo_de(rel), v])
            if not any(f[3] == 0 for f in filas):
                continue
            huella = zlib.crc32(json.dumps(filas, ensure_ascii=False).encode()) & 0xFFFFFFFF
            ident = f"{gid}-{huella:08x}"
            principal = g[5] if len(g) > 5 else next((f[0] for f in filas if f[0].lower().endswith((".nro", ".ovl"))), "")
            version = next((f[4] for f in filas if f[0] == principal), "")
            zipname = ident + ".zip"
            destino = os.path.join(salida, zipname)
            if not os.path.exists(destino):
                with zipfile.ZipFile(destino + ".tmp", "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
                    for f in filas:
                        z.write(os.path.join(carpeta, f[0]), f[0])
                os.replace(destino + ".tmp", destino)
            entrada = {
                "nombre": g[1], "tipo": g[2], "version": version, "principal": principal,
                "zip": zipname, "bytes": os.path.getsize(destino), "reinicio": g[3], "archivos": filas,
            }
            url = catalogo.get(CATALOGO.get(gid, ""))
            if isinstance(url, str):
                entrada["catalogo"] = url
            manifiesto["apps"][ident] = entrada
            lista.append(ident)
        manifiesto["packs"][clave] = lista
        print(f"{clave}: {len(lista)} apps")
    with open(os.path.join(salida, "apps-pack.json"), "w", encoding="utf-8") as f:
        json.dump(manifiesto, f, ensure_ascii=False, indent=1)
        f.write("\n")
    usados = {a["zip"] for a in manifiesto["apps"].values()}
    total = sum(os.path.getsize(os.path.join(salida, z)) for z in usados)
    print(f"{len(usados)} zips, {total / 1e6:.1f} MB en total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
