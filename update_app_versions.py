#!/usr/bin/env python3
"""Actualiza app-versions.json con la fecha de subida de cada app del catálogo.

Lo mismo que update_pack_versions.py, pero para las apps: el enlace
/releases/latest/download/ sigue igual cuando sale una versión nueva, y un
asset vuelto a subir conserva su tag. Lo que sí cambia es la fecha de subida
del archivo, y eso es lo que las apps comparan con la que anotaron al instalar.

Clave: la URL tal como está en nx-links.json. Valor: {"fecha", "version"} y,
cuando el archivo trae un .nro, la versión que el autor puso dentro (NACP):
  - "nro_version": la del .nro, si el enlace es un .nro;
  - "nros": [{"ruta", "version"}], cada .nro de un zip con su ruta en la SD.
Con eso las apps leen el .nro que hay en la SD y comparan, como hace el gestor de
homebrew de HomeNX, sin tener que haberlo instalado con nosotros. Los archivos
solo se descargan cuando cambia su fecha.

Uso:
    python3 update_app_versions.py            # actualiza el archivo
    python3 update_app_versions.py --check    # solo dice si cambiaría (CI)
"""

import io
import json
import re
import struct
import subprocess
import sys
import urllib.request
import zipfile
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


def version_nacp(nro):
    """La versión visible (display_version del NACP) de un .nro, o None."""
    try:
        fin = struct.unpack_from("<I", nro, 0x18)[0]
        if nro[fin:fin + 4] != b"ASET":
            return None
        inicio, tam = struct.unpack_from("<QQ", nro, fin + 0x18)
        if tam < 0x3070:
            return None
        crudo = nro[fin + inicio + 0x3060:fin + inicio + 0x3070]
        v = crudo.split(b"\0", 1)[0].decode("utf-8", "replace").strip()
        return v or None
    except (struct.error, IndexError):
        return None


def versiones_del_archivo(url):
    """Lo que hay dentro del archivo: la versión de su .nro o de los de un zip."""
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "OQB-catalogo"}), timeout=300) as r:
        datos = r.read()
    if url.lower().endswith(".nro"):
        v = version_nacp(datos)
        return {"nro_version": v} if v else {}
    if not url.lower().endswith(".zip"):
        return {}
    nros = []
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        for nombre in z.namelist():
            if nombre.lower().endswith(".nro") and not nombre.startswith("__MACOSX"):
                v = version_nacp(z.read(nombre))
                if v:
                    nros.append(OrderedDict(ruta="/" + nombre.lstrip("/"), version=v))
    return {"nros": nros} if nros else {}


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
                previo = actual.get(url, {})
                if previo.get("fecha") == datos["fecha"] and "revisado" in previo:
                    datos.update((k, v) for k, v in previo.items() if k in ("nro_version", "nros", "revisado"))
                else:
                    try:
                        datos.update(versiones_del_archivo(url))
                        datos["revisado"] = True
                    except Exception as error:  # se reintenta en la próxima pasada
                        print(f"aviso: no se pudo abrir {url}: {error}")
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
