#!/usr/bin/env python3
"""Mantiene la sección Ports del catálogo igual que el ports.json de
alucardiio-yt/archives-nx.

Lo corre el workflow de app-versions cada 6 horas, antes de leer las fechas:
así un port nuevo, renombrado o quitado allá aparece igual aquí sin tocar nada
a mano.

En nx-links.json:
  - "apps_ports": nombre -> url, en el orden de ports.json (la categoría Ports);
  - no van en "apps" (Todas las aplicaciones y el buscador): serían más de cien
    entradas de ruido. Solo se ven en su sección;
  - "iconos_ports": url -> url del ícono, para que update_app_versions.py lo
    achique y lo meta en iconos.zip;
  - "ports": juego -> {"partes": [url, ...]} para la pestaña Ports (4.6.0):
    las entradas «… (PARTE N)» de un mismo juego van juntas y en orden, y una
    «(SOLO ACTUALIZACION) …» lleva "actualizacion": true. "apps_ports" sigue
    igual para las versiones anteriores.
Un port que deja de estar en ports.json sale de las dos.

Si ports.json no se puede leer o no tiene el formato esperado no se cambia
nada: mejor una lista vieja que una vacía. Tampoco si de una vez desaparece
más de un cuarto de los ports (un archivo movido, a medio subir o reemplazado):
eso se revisa a mano.

Cada lectura buena queda copiada tal cual en ports-origen.json, con su
historia en git: si el autor lo mueve o lo borra, la lista sigue aquí.

Los ports propios van en "ports_propios" (nombre -> {"partes": [url, ...],
"icono": url}) y nadie los toca: se suman primero a "apps_ports", "iconos_ports"
y "ports" en cada pasada.

Los cambios a ports de alucardiio-yt van en "ports_cambios" (nombre tal como
está en ports.json -> {"url": nueva, "icono": nuevo}): se aplican en su lugar
de la lista en cada pasada. Si el nombre ya no existe, se avisa y no pasa nada.

Uso:
    python3 sync_ports.py            # actualiza nx-links.json
    python3 sync_ports.py --check    # solo dice si cambiaría
"""

import json
import re
import sys
import urllib.request
from collections import OrderedDict

ORIGEN = "https://raw.githubusercontent.com/alucardiio-yt/archives-nx/main/ports.json"
CATALOGO = "nx-links.json"
AGENTE = "OQB-HATS-PACK catalogo (+https://github.com/ohquebacan/OQB-HATS-PACK)"
MINIMO = 10  # menos que esto es un ports.json roto o a medio subir
MAX_QUITADOS = 0.25  # más que esto de una vez: no se aplica
RESPALDO = "ports-origen.json"
PARTE = re.compile(r"^(.*?)\s*\(\s*PARTE\s*(\d+)\s*\)\s*$", re.IGNORECASE)
ACTUALIZACION = re.compile(r"^\(\s*SOLO\s+ACTUALIZACI[OÓ]N\s*\)\s*", re.IGNORECASE)


def leer_origen():
    pedido = urllib.request.Request(ORIGEN, headers={"User-Agent": AGENTE})
    with urllib.request.urlopen(pedido, timeout=60) as r:
        datos = json.loads(r.read().decode("utf-8"), object_pairs_hook=OrderedDict)
    if not isinstance(datos, dict):
        raise ValueError("ports.json no es un objeto")
    leer_origen.crudo = datos
    ports = OrderedDict()
    iconos = OrderedDict()
    for nombre, entrada in datos.items():
        if not isinstance(entrada, dict):
            continue
        url = entrada.get("url")
        if not isinstance(url, str) or not url.startswith("https://") or not url.lower().endswith(".zip"):
            print(f"aviso: {nombre}: sin url de zip, se salta")
            continue
        nombre = " ".join(str(nombre).split())
        if not nombre or nombre in ports:
            continue
        ports[nombre] = url
        icono = entrada.get("icon")
        if isinstance(icono, str) and icono.startswith("https://"):
            iconos[url] = icono
    if len(ports) < MINIMO:
        raise ValueError(f"solo {len(ports)} ports")
    return ports, iconos


def agrupar(ports):
    """Un juego por entrada, con sus partes en orden: «X (PARTE 1)» y
    «X (PARTE 2)» son el juego X en dos zips."""
    juegos = OrderedDict()
    for nombre, url in ports.items():
        m = PARTE.match(nombre)
        juego, numero = (m.group(1).strip(), int(m.group(2))) if m else (nombre, 0)
        actualizacion = bool(ACTUALIZACION.match(juego))
        juego = ACTUALIZACION.sub("", juego).strip() or juego
        entrada = juegos.setdefault(juego, {"partes": [], "actualizacion": actualizacion})
        entrada["partes"].append((numero, url))
    salida = OrderedDict()
    for juego, entrada in juegos.items():
        datos = OrderedDict(partes=[u for _, u in sorted(entrada["partes"], key=lambda p: p[0])])
        if entrada["actualizacion"]:
            datos["actualizacion"] = True
        salida[juego] = datos
    return salida


def aplicar_cambios(catalogo, ports, iconos):
    for nombre, cambio in (catalogo.get("ports_cambios") or {}).items():
        if not isinstance(cambio, dict):
            continue
        if nombre not in ports:
            print(f"aviso: ports_cambios: «{nombre}» ya no está en ports.json")
            continue
        url = cambio.get("url")
        if isinstance(url, str) and url.startswith("https://"):
            viejo = ports[nombre]
            ports[nombre] = url
            if viejo in iconos:
                iconos[url] = iconos.pop(viejo)
        icono = cambio.get("icono")
        if isinstance(icono, str) and icono.startswith("https://"):
            iconos[ports[nombre]] = icono


def propios_de(catalogo):
    """Los ports propios en la forma de ports.json: nombre (con «(PARTE N)» si
    son varias partes) -> url, y url -> ícono."""
    planos, iconos = OrderedDict(), OrderedDict()
    for nombre, datos in (catalogo.get("ports_propios") or {}).items():
        if not isinstance(datos, dict):
            continue
        partes = [u for u in datos.get("partes", []) if isinstance(u, str) and u.startswith("https://")]
        for i, url in enumerate(partes, 1):
            planos[f"{nombre} (PARTE {i})" if len(partes) > 1 else nombre] = url
            if isinstance(datos.get("icono"), str):
                iconos[url] = datos["icono"]
        # DLC y actualizaciones de un port propio: van en la misma ficha, en su
        # sección (OQB Updater 4.8.0). En apps_ports e iconos_ports igual que las
        # partes, para que se midan y tengan ícono; agrupar() no los separa.
        for clave in ("dlc", "actualizaciones"):
            extras = [u for u in datos.get(clave, []) if isinstance(u, str) and u.startswith("https://")]
            for i, url in enumerate(extras, 1):
                planos[f"{nombre} [{clave}] (PARTE {i})"] = url
                if isinstance(datos.get("icono"), str):
                    iconos[url] = datos["icono"]
            if extras:
                propios_de.extras.setdefault(nombre, OrderedDict())[clave] = extras
    return planos, iconos


propios_de.extras = OrderedDict()


def main():
    solo_comprobar = "--check" in sys.argv
    try:
        ports, iconos = leer_origen()
    except Exception as error:
        print(f"aviso: no se pudo leer ports.json ({error}); no se cambia nada")
        return 0

    with open(CATALOGO, encoding="utf-8") as fichero:
        catalogo = json.load(fichero, object_pairs_hook=OrderedDict)

    aplicar_cambios(catalogo, ports, iconos)
    propios, iconos_propios = propios_de(catalogo)
    # El freno de abajo mira solo los de alucardiio-yt.
    anteriores = OrderedDict((n, u) for n, u in catalogo.get("apps_ports", OrderedDict()).items() if n not in propios)
    urls_anteriores = set(anteriores.values()) if isinstance(anteriores, dict) else set()
    urls_nuevas = set(ports.values())

    quitados = [n for n in anteriores if n not in ports] if isinstance(anteriores, dict) else []
    if anteriores and len(quitados) > MAX_QUITADOS * len(anteriores):
        print(f"aviso: ports.json quitaría {len(quitados)} de {len(anteriores)} ports de una vez; "
              "probablemente se movió o se reemplazó. No se cambia nada (revisar a mano).")
        return 0

    # Los propios primero, después los de alucardiio-yt.
    ports = OrderedDict(list(propios.items()) + [(n, u) for n, u in ports.items() if n not in propios])
    iconos = OrderedDict(list(iconos_propios.items()) + [(u, i) for u, i in iconos.items() if u not in iconos_propios])
    urls_nuevas = set(ports.values())

    # La copia tal cual del origen, solo cuando la lectura es buena.
    if not solo_comprobar:
        with open(RESPALDO, "w", encoding="utf-8") as fichero:
            json.dump(leer_origen.crudo, fichero, ensure_ascii=False, indent=2)
            fichero.write("\n")

    # Los ports van solo en su sección: en «apps» (Todas las aplicaciones,
    # buscador) serían más de cien entradas de ruido. Si alguno quedó ahí, sale.
    todas = OrderedDict((n, u) for n, u in catalogo.get("apps", OrderedDict()).items()
                        if u not in urls_anteriores and u not in urls_nuevas)

    nuevo = OrderedDict()
    for clave, valor in catalogo.items():
        if clave in ("apps_ports", "iconos_ports", "ports"):
            continue
        nuevo[clave] = todas if clave == "apps" else valor
        # La categoría va después de las otras categorías de apps.
        if clave == "apps_sistema":
            nuevo["apps_ports"] = ports
    if "apps_ports" not in nuevo:
        nuevo["apps_ports"] = ports
    nuevo["iconos_ports"] = iconos
    nuevo["ports"] = agrupar(ports)
    for nombre, extras in propios_de.extras.items():
        for clave, urls in extras.items():
            nuevo["ports"].pop(f"{nombre} [{clave}]", None)
            if nombre in nuevo["ports"]:
                nuevo["ports"][nombre][clave] = urls

    if nuevo == catalogo:
        print("sin cambios")
        return 0
    agregados = [n for n in ports if n not in anteriores]
    quitados = [n for n in anteriores if n not in ports]
    print(f"ports: {len(ports)} ({len(agregados)} nuevos, {len(quitados)} quitados)")
    for n in agregados[:20]:
        print(f"  + {n}")
    for n in quitados[:20]:
        print(f"  - {n}")
    if solo_comprobar:
        return 1
    with open(CATALOGO, "w", encoding="utf-8") as fichero:
        json.dump(nuevo, fichero, ensure_ascii=False, indent=4)
        fichero.write("\n")
    print(f"{CATALOGO} actualizado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
