#!/usr/bin/env python3
"""Actualiza app-versions.json con la fecha de subida de cada app del catálogo.

Lo mismo que update_pack_versions.py, pero para las apps: el enlace
/releases/latest/download/ sigue igual cuando sale una versión nueva, y un
asset vuelto a subir conserva su tag. Lo que sí cambia es la fecha de subida
del archivo, y eso es lo que las apps comparan con la que anotaron al instalar.

Clave: la URL tal como está en nx-links.json. Valor: {"fecha", "version"}.

Uso:
    python3 update_app_versions.py            # actualiza el archivo
    python3 update_app_versions.py --check    # solo dice si cambiaría (CI)
"""

import json
import re
import subprocess
import sys
from collections import OrderedDict

CATALOGO = "nx-links.json"
ARCHIVO = "app-versions.json"
ENLACE = re.compile(r"^https://github\.com/([^/]+/[^/]+)/releases/(?:latest/download|download/([^/]+))/([^/]+)$")


def urls_de_apps():
    with open(CATALOGO, encoding="utf-8") as fichero:
        catalogo = json.load(fichero, object_pairs_hook=OrderedDict)
    urls = []
    for clave, seccion in catalogo.items():
        if clave.startswith("apps") and clave != "apps_descripciones" and isinstance(seccion, dict):
            urls += [u for u in seccion.values() if isinstance(u, str) and u not in urls]
    return urls


def release(repo, tag, cache):
    """El release, pidiéndolo una sola vez por repo y tag."""
    clave = (repo, tag)
    if clave not in cache:
        ruta = f"repos/{repo}/releases/" + ("latest" if tag is None else f"tags/{tag}")
        salida = subprocess.run(["gh", "api", ruta], capture_output=True, text=True)
        cache[clave] = json.loads(salida.stdout) if salida.returncode == 0 else None
    return cache[clave]


def main():
    solo_comprobar = "--check" in sys.argv
    try:
        with open(ARCHIVO, encoding="utf-8") as fichero:
            actual = json.load(fichero, object_pairs_hook=OrderedDict)
    except FileNotFoundError:
        actual = OrderedDict()

    cache = {}
    nuevo = OrderedDict()
    for url in urls_de_apps():
        m = ENLACE.match(url)
        datos = None
        if m:
            repo, tag, archivo = m.groups()
            r = release(repo, tag, cache)
            asset = next((a for a in (r or {}).get("assets", []) if a.get("name") == archivo), None)
            if asset:
                datos = OrderedDict(fecha=asset["updated_at"], version=r.get("tag_name", ""))
        if datos is None:
            # Sin respuesta (o un enlace que no es de GitHub): se deja lo que había,
            # para no avisar de una actualización que no existe.
            print(f"aviso: no se pudo leer {url}")
            if url in actual:
                nuevo[url] = actual[url]
            continue
        nuevo[url] = datos

    if nuevo == actual:
        print("sin cambios")
        return 0
    for url, datos in nuevo.items():
        if actual.get(url) != datos:
            print(f"{url}: {actual.get(url, {}).get('fecha', '(nada)')} -> {datos['fecha']}")
    if solo_comprobar:
        return 1
    with open(ARCHIVO, "w", encoding="utf-8") as fichero:
        json.dump(nuevo, fichero, ensure_ascii=False, indent=4)
        fichero.write("\n")
    print(f"{ARCHIVO} actualizado")
    return 0


if __name__ == "__main__":
    sys.exit(main())
