#!/usr/bin/env python3
"""Actualiza pack-versions.json con la fecha real de cada pack publicado.

El problema que resuelve: el tag de un release no cambia cuando se vuelve a
subir el zip. El release V1.4 se publicó el 27 de septiembre y su zip se
reemplazó el 2 de octubre — quien lo descargó el 28 tiene otro archivo y no hay
forma de que lo sepa. Lo que sí cambia es la fecha de subida del asset, y de ahí
sale esto.

La app lee el json publicado y lo compara con lo que el usuario instaló.

Uso:
    python3 update_pack_versions.py            # actualiza el archivo
    python3 update_pack_versions.py --check    # solo dice si cambiaría (CI)

Lo que NO toca:
  - una entrada con "fijado": true, por si querés congelar una fecha a mano.
  - el campo "nota", que es tuyo y la app muestra tal cual.
"""

import json
import os
import subprocess
import sys
from collections import OrderedDict

REPO = "ohquebacan/OQB-HATS-PACK"
ARCHIVO = "pack-versions.json"

# Solo estos assets. Un release trae más cosas (el pack viejo, el de overclock)
# y no todas son packs que la app ofrezca.
PACKS = (
    "OQB-HATS-PACK-23.0.zip",
    "OQB-HATS-PACK-OVER-CLOCK.zip",
)


def releases():
    """Todos los releases con sus assets, vía la API de GitHub."""
    salida = subprocess.run(
        ["gh", "api", f"repos/{REPO}/releases", "--paginate"],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(salida)


def fechas_publicadas():
    """{nombre de archivo: fecha de subida mas reciente}.

    Se queda con la más reciente porque el mismo nombre puede existir en varios
    releases; la que vale es la del último que lo sirvió."""
    fechas = {}
    for release in releases():
        for asset in release.get("assets", []):
            nombre = asset.get("name")
            if nombre not in PACKS:
                continue
            subido = asset.get("updated_at", "")[:10]  # YYYY-MM-DD
            if subido and subido > fechas.get(nombre, ""):
                fechas[nombre] = subido
    return fechas


def leer_actual():
    if not os.path.exists(ARCHIVO):
        return OrderedDict()
    with open(ARCHIVO, encoding="utf-8") as fichero:
        return json.load(fichero, object_pairs_hook=OrderedDict)


def main():
    solo_comprobar = "--check" in sys.argv

    actual = leer_actual()
    publicadas = fechas_publicadas()

    nuevo = OrderedDict()
    cambios = []
    for nombre in PACKS:
        previo = actual.get(nombre, OrderedDict())
        entrada = OrderedDict()
        entrada["fecha"] = previo.get("fecha", "")
        entrada["nota"] = previo.get("nota", "")
        entrada["fijado"] = bool(previo.get("fijado", False))

        if nombre not in publicadas:
            print(f"aviso: {nombre} no está en ningún release; se deja como estaba")
        elif entrada["fijado"]:
            print(f"fijado: {nombre} se deja en {entrada['fecha']}")
        elif entrada["fecha"] != publicadas[nombre]:
            cambios.append((nombre, entrada["fecha"] or "(nada)", publicadas[nombre]))
            entrada["fecha"] = publicadas[nombre]

        nuevo[nombre] = entrada

    if not cambios:
        print("sin cambios")
        return 0

    for nombre, antes, ahora in cambios:
        print(f"{nombre}: {antes} -> {ahora}")

    if solo_comprobar:
        return 1

    with open(ARCHIVO, "w", encoding="utf-8") as fichero:
        json.dump(nuevo, fichero, ensure_ascii=False, indent=4)
        fichero.write("\n")
    print(f"{ARCHIVO} actualizado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
