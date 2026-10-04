#!/usr/bin/env python3
"""Comprueba que todos los enlaces de nx-links.json siguen vivos.

Existe por los enlaces que apuntan a /releases/latest/download/: esos no fallan
cuando sale una versión nueva, fallan cuando el autor renombra su archivo. Y
cuando eso pasa no avisa nadie — la entrada simplemente deja de descargar, en
silencio, hasta que alguien la prueba.

Uso:
    python3 check_links.py [ruta/a/nx-links.json]

Devuelve 0 si todos responden y 1 si alguno no, que es lo que hace fallar el
workflow y manda el aviso.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

TIMEOUT = 30
# Un fallo suelto suele ser la red, no un enlace roto. Solo se da por roto lo
# que falla las tres veces: avisar en falso es la forma de que el aviso se
# ignore.
INTENTOS = 3
ESPERA_ENTRE_INTENTOS = 5
# Cortesía con los servidores, y de paso evita que GitHub nos limite.
PAUSA = 0.3

AGENTE = "OQB-HATS-PACK link check (+https://github.com/ohquebacan/OQB-HATS-PACK)"


def recorrer(nodo, camino=()):
    """Devuelve (camino, url) por cada cadena del json, a cualquier profundidad.

    El catálogo no es plano: 'cfws' anida otro diccionario dentro, así que
    recorrer solo el primer nivel dejaría enlaces sin comprobar."""
    if isinstance(nodo, dict):
        for clave, valor in nodo.items():
            yield from recorrer(valor, camino + (clave,))
    elif isinstance(nodo, str) and nodo.startswith(("http://", "https://")):
        yield camino, nodo


def pedir(url, metodo):
    peticion = urllib.request.Request(url, method=metodo, headers={"User-Agent": AGENTE})
    if metodo == "GET":
        # Solo el primer byte: no hace falta bajar el archivo entero para saber
        # que está ahí.
        peticion.add_header("Range", "bytes=0-0")
    with urllib.request.urlopen(peticion, timeout=TIMEOUT) as respuesta:
        return respuesta.status


def comprobar(url):
    """(ok, detalle). Sigue los redirects, que es lo que hace la propia app."""
    ultimo = "sin respuesta"
    for intento in range(1, INTENTOS + 1):
        for metodo in ("HEAD", "GET"):
            try:
                codigo = pedir(url, metodo)
                if 200 <= codigo < 300:
                    return True, f"HTTP {codigo}"
                ultimo = f"HTTP {codigo}"
            except urllib.error.HTTPError as error:
                ultimo = f"HTTP {error.code}"
                # 404 es definitivo: el archivo no está. No hay por qué insistir
                # ni probar el otro método.
                if error.code == 404:
                    return False, ultimo
                # 403 y 405 suelen ser que el servidor no admite HEAD; se
                # reintenta con GET antes de darlo por malo.
                if error.code not in (403, 405):
                    break
            except Exception as error:  # red, DNS, TLS, timeout
                ultimo = f"{type(error).__name__}: {error}"
                break
        if intento < INTENTOS:
            time.sleep(ESPERA_ENTRE_INTENTOS)
    return False, ultimo


def main():
    ruta = sys.argv[1] if len(sys.argv) > 1 else "nx-links.json"
    with open(ruta, encoding="utf-8") as fichero:
        catalogo = json.load(fichero)

    # Varias secciones comparten la misma url; se comprueba una vez y se
    # informa de todos los sitios donde aparece.
    donde = {}
    for camino, url in recorrer(catalogo):
        donde.setdefault(url, []).append("/".join(camino))

    print(f"{len(donde)} enlaces distintos en {len(list(recorrer(catalogo)))} entradas\n")

    rotos = []
    for numero, (url, sitios) in enumerate(sorted(donde.items()), start=1):
        ok, detalle = comprobar(url)
        marca = "ok  " if ok else "ROTO"
        print(f"{marca} [{numero:>2}/{len(donde)}] {sitios[0]} — {detalle}")
        if not ok:
            rotos.append((sitios, url, detalle))
        time.sleep(PAUSA)

    print()
    if not rotos:
        print(f"Todos responden ({len(donde)} enlaces).")
    else:
        print(f"{len(rotos)} enlaces no responden:")
        for sitios, url, detalle in rotos:
            print(f"  · {', '.join(sitios)}")
            print(f"    {url}")
            print(f"    {detalle}")

    # El resumen del job, para no tener que abrir el log.
    resumen = os.environ.get("GITHUB_STEP_SUMMARY")
    if resumen:
        with open(resumen, "a", encoding="utf-8") as salida:
            if not rotos:
                salida.write(f"### Catálogo correcto\n\nLos {len(donde)} enlaces responden.\n")
            else:
                salida.write(f"### {len(rotos)} enlaces rotos\n\n")
                salida.write("| Entrada | Enlace | Motivo |\n|---|---|---|\n")
                for sitios, url, detalle in rotos:
                    salida.write(f"| {', '.join(sitios)} | `{url}` | {detalle} |\n")
                salida.write(
                    "\nUn enlace a `/releases/latest/download/` que da 404 suele "
                    "significar que el autor renombró su archivo: hay que mirar su "
                    "último release y actualizar el nombre en `nx-links.json`.\n"
                )

    return 1 if rotos else 0


if __name__ == "__main__":
    sys.exit(main())
