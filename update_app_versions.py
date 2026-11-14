#!/usr/bin/env python3
"""Actualiza app-versions.json con la fecha de subida de cada app del catálogo.

Lo mismo que update_pack_versions.py, pero para las apps: el enlace
/releases/latest/download/ sigue igual cuando sale una versión nueva, y un
asset vuelto a subir conserva su tag. Lo que sí cambia es la fecha de subida
del archivo, y eso es lo que las apps comparan con la que anotaron al instalar.

Clave: la URL tal como está en nx-links.json. Valor: {"fecha", "version"} y,
cuando el archivo trae un .nro, la versión que el autor puso dentro (NACP):
  - "nro_version": la del .nro, si el enlace es un .nro;
  - "nros": [{"ruta", "version"}], cada .nro de un zip con su ruta en la SD;
  - "marcas": rutas en la SD de archivos propios de un zip sin .nro (sysmodules,
    plugins), para saber si está instalado;
  - "icono": el ícono del .nro (el principal si es un zip), achicado, guardado en
    iconos/ con su hash por nombre. Todos van además en iconos.zip, que OQB
    Updater baja una sola vez y solo vuelve a pedir si falta alguno.
Con eso las apps leen el .nro que hay en la SD y comparan, como hace el gestor de
homebrew de HomeNX, sin tener que haberlo instalado con nosotros. Los archivos
solo se descargan cuando cambia su fecha.

Uso:
    python3 update_app_versions.py            # actualiza el archivo
    python3 update_app_versions.py --check    # solo dice si cambiaría (CI)
"""

import hashlib
import io
import json
import os
import re
import struct
import subprocess
import sys
import time
import urllib.request
import zipfile
from collections import OrderedDict

CATALOGO = "nx-links.json"
# Sube cuando cambia lo que se saca de cada archivo: obliga a volver a abrirlos.
REVISION = 4
ARCHIVO = "app-versions.json"
ICONOS = "iconos"
ICONOS_ZIP = "iconos.zip"
LADO_ICONO = 96
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


def icono_nro(nro):
    """El JPEG del ícono de un .nro (sección icon de ASET), o None."""
    try:
        fin = struct.unpack_from("<I", nro, 0x18)[0]
        if nro[fin:fin + 4] != b"ASET":
            return None
        inicio, tam = struct.unpack_from("<QQ", nro, fin + 0x8)
        if tam == 0 or tam > (1 << 20):
            return None
        return nro[fin + inicio:fin + inicio + tam]
    except (struct.error, IndexError):
        return None


# Ícono propio: una imagen en iconos-propios/ con el nombre del archivo
# publicado más .png o .jpg. Sirve para un archivo sin .nro (un NSP) y también
# para reemplazar el ícono que trae el .nro cuando no nos gusta.
PROPIOS = "iconos-propios"


def icono_propio(url):
    base = url.rsplit("/", 1)[-1]
    for ext in (".png", ".jpg"):
        ruta = os.path.join(PROPIOS, base + ext)
        if os.path.exists(ruta):
            with open(ruta, "rb") as fichero:
                return guardar_icono(fichero.read())
    return None


def guardar_icono(crudo):
    """Lo achica a LADO_ICONO y lo guarda en iconos/; devuelve su nombre."""
    if not crudo:
        return None
    try:
        from PIL import Image
        imagen = Image.open(io.BytesIO(crudo)).convert("RGB")
        imagen = imagen.resize((LADO_ICONO, LADO_ICONO), Image.LANCZOS)
        salida = io.BytesIO()
        imagen.save(salida, "JPEG", quality=88, optimize=True)
        datos = salida.getvalue()
    except Exception as error:  # sin Pillow o un JPEG raro: tal cual
        print(f"aviso: ícono sin achicar: {error}")
        datos = crudo
    nombre = hashlib.sha1(datos).hexdigest()[:12] + ".jpg"
    os.makedirs(ICONOS, exist_ok=True)
    with open(os.path.join(ICONOS, nombre), "wb") as fichero:
        fichero.write(datos)
    return nombre


def armar_zip_de_iconos(datos_por_url):
    """iconos.zip con los íconos en uso, siempre igual para los mismos archivos
    (orden y fecha fijos): así solo cambia en git cuando cambia un ícono."""
    nombres = sorted({d["icono"] for d in datos_por_url.values() if d.get("icono")})
    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_STORED) as z:
        for nombre in nombres:
            ruta = os.path.join(ICONOS, nombre)
            if not os.path.exists(ruta):
                continue
            info = zipfile.ZipInfo(nombre, date_time=(2026, 1, 1, 0, 0, 0))
            with open(ruta, "rb") as fichero:
                z.writestr(info, fichero.read())
    nuevo = salida.getvalue()
    try:
        with open(ICONOS_ZIP, "rb") as fichero:
            if fichero.read() == nuevo:
                return False
    except FileNotFoundError:
        pass
    with open(ICONOS_ZIP, "wb") as fichero:
        fichero.write(nuevo)
    # Los que ya no usa ninguna app no se quedan en el repo.
    for nombre in os.listdir(ICONOS):
        if nombre.endswith(".jpg") and nombre not in nombres:
            os.remove(os.path.join(ICONOS, nombre))
    return True


def descargar(url, asset_api=None):
    """El archivo completo. GitHub a veces responde 504 en el enlace normal
    (releases/download) mientras la API sí lo entrega: se intenta el enlace dos
    veces y después la API del asset."""
    intentos = [(url, False), (url, False)]
    if asset_api:
        intentos.append((asset_api, True))
    ultimo = None
    for i, (desde, por_api) in enumerate(intentos):
        if i:
            time.sleep(5)
        try:
            pedido = urllib.request.Request(desde, headers={"User-Agent": "OQB-catalogo"})
            if por_api:
                pedido.add_header("Accept", "application/octet-stream")
                # Sin redirigir: la API manda a un enlace firmado de S3, que
                # rechaza el pedido si además lleva la credencial.
                if os.environ.get("GH_TOKEN"):
                    pedido.add_unredirected_header("Authorization", "Bearer " + os.environ["GH_TOKEN"])
            with urllib.request.urlopen(pedido, timeout=300) as r:
                return r.read()
        except Exception as error:
            ultimo = error
            print(f"aviso: intento {i + 1} con {desde}: {error}")
    raise ultimo


def versiones_del_archivo(url, asset_api=None):
    """Lo que hay dentro del archivo: la versión de su .nro o de los de un zip."""
    datos = descargar(url, asset_api)
    if url.lower().endswith(".nro"):
        v = version_nacp(datos)
        salida = {"nro_version": v} if v else {}
        icono = guardar_icono(icono_nro(datos))
        if icono:
            salida["icono"] = icono
        return salida
    if not url.lower().endswith(".zip"):
        return {}
    nros = []
    icono = None
    with zipfile.ZipFile(io.BytesIO(datos)) as z:
        nombres = [n for n in z.namelist() if not n.startswith("__MACOSX") and not n.endswith("/")]
        quitar = envoltorio(nombres)
        for nombre in nombres:
            if nombre.lower().endswith(".nro"):
                crudo = z.read(nombre)
                v = version_nacp(crudo)
                ruta = "/" + nombre[len(quitar):].lstrip("/")
                if v:
                    nros.append(OrderedDict(ruta=ruta, version=v))
                # El de la app es el que va en /switch/ (los otros suelen ser módulos).
                if icono is None or (ruta.lower().startswith("/switch/") and not icono[0]):
                    icono = (ruta.lower().startswith("/switch/"), crudo)
    nombre_icono = guardar_icono(icono_nro(icono[1])) if icono else None
    extra = {"icono": nombre_icono} if nombre_icono else {}
    if nros:
        return {"nros": nros, **extra}
    marcas = marcas_de([n[len(quitar):] for n in nombres])
    return ({"marcas": marcas} if marcas else {}) | extra


# Carpetas que van tal cual en la raíz de la SD.
RAIZ = ("switch", "atmosphere", "config", "bootloader", "emuiibo", "themes", "tico", "mods")


def envoltorio(nombres):
    """La carpeta que envuelve todo el zip ("app_v1.4.0/switch/..."), que las
    apps quitan al extraer; "" si no hay."""
    primeras = {n.split("/", 1)[0] for n in nombres if "/" in n}
    if len(primeras) == 1 and all("/" in n for n in nombres):
        unica = primeras.pop()
        if unica.lower() not in RAIZ:
            return unica + "/"
    return ""


# Archivos que muchos zips comparten: no dicen qué app está instalada.
COMUNES = ("atmosphere/config", "atmosphere/hosts", "bootloader/", "config/", "hbmenu.nro", "atmosphere/package3", "atmosphere/stratosphere")


def marcas_de(nombres):
    """Hasta 3 archivos que delatan la app: primero el exefs de su sysmodule,
    después lo que va en su propia carpeta."""
    propios = [n for n in nombres if not n.lower().startswith(COMUNES)]
    def peso(n):
        bajo = n.lower()
        if bajo.startswith("atmosphere/contents/") and bajo.endswith(("exefs.nsp", "/main")):
            return 0
        if bajo.startswith(("atmosphere/contents/", "switch/")):
            return 1
        return 2
    return ["/" + n.lstrip("/") for n in sorted(propios, key=peso)[:3]]


def fecha_por_cabeceras(url):
    """Para enlaces que no son releases de GitHub (GitLab, por ejemplo): la
    fecha de modificación que da el servidor, en el mismo formato."""
    pedido = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "OQB-catalogo"})
    with urllib.request.urlopen(pedido, timeout=60) as r:
        ultima = r.headers.get("Last-Modified")
    if not ultima:
        return None
    from email.utils import parsedate_to_datetime
    return parsedate_to_datetime(ultima).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        asset_api = None
        if m:
            repo, tag, archivo = m.groups()
            r = release(repo, tag, cache)
            asset = next((a for a in (r or {}).get("assets", []) if a.get("name") == archivo), None)
            if asset:
                datos = OrderedDict(fecha=asset["updated_at"], version=r.get("tag_name", ""))
                if asset.get("url"):
                    asset_api = asset["url"]
        else:
            try:
                fecha = fecha_por_cabeceras(url)
            except Exception as error:
                fecha = None
                print(f"aviso: {url}: {error}")
            if fecha:
                datos = OrderedDict(fecha=fecha, version="")
        if datos is not None:
            previo = actual.get(url, {})
            if previo.get("fecha") == datos["fecha"] and previo.get("revisado") == REVISION:
                datos.update((k, v) for k, v in previo.items() if k in ("nro_version", "nros", "marcas", "icono", "revisado"))
            else:
                try:
                    datos.update(versiones_del_archivo(url, asset_api))
                    datos["revisado"] = REVISION
                except Exception as error:
                    # Se deja la entrada anterior completa (fecha incluida): así
                    # no queda una fecha nueva sin versión ni ícono, el ícono no
                    # se borra de iconos.zip y la próxima pasada lo reintenta
                    # porque la fecha sigue siendo distinta.
                    print(f"aviso: no se pudo abrir {url}: {error}")
                    datos = None
        if datos is None:
            # Sin respuesta (o un enlace que no es de GitHub): se deja lo que había,
            # para no avisar de una actualización que no existe.
            print(f"aviso: no se pudo leer {url}")
            if url in actual:
                nuevo[url] = actual[url]
            continue
        propio = icono_propio(url)
        if propio:
            datos["icono"] = propio
        nuevo[url] = datos

    iconos_cambiaron = False if solo_comprobar else armar_zip_de_iconos(nuevo)
    if nuevo == actual:
        print("sin cambios" + (" (salvo iconos.zip)" if iconos_cambiaron else ""))
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
