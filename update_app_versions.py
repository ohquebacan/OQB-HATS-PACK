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
  - "bytes": el tamaño del zip, solo en los ports (la pestaña Ports revisa el
    espacio antes de instalar un juego en varias partes);
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
# Los ports se ven como portadas grandes en su pestaña (150-200 px): a 96 se
# veían borrosos. Sus íconos originales son de 256.
LADO_PORT = 192
ICONO_PORT_V = 3  # sube cuando cambia lo que se saca del ícono: rehace solo el ícono
# El ícono del acceso directo de cada port en el menú HOME: JPEG normal (no
# progresivo) de 256 y hasta 128 KB, que es lo único que el HOME dibuja. Sin
# esto, un port con ícono raro quedaba con el ícono cargando para siempre.
ACCESOS_PORTS = "iconos-acceso-directo/ports"
URL_ACCESOS = "https://raw.githubusercontent.com/ohquebacan/OQB-HATS-PACK/main/" + ACCESOS_PORTS + "/"
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


def guardar_icono(crudo, lado=LADO_ICONO):
    """Lo achica a `lado` y lo guarda en iconos/; devuelve su nombre."""
    if not crudo:
        return None
    try:
        from PIL import Image
        imagen = Image.open(io.BytesIO(crudo)).convert("RGB")
        imagen = imagen.resize((lado, lado), Image.LANCZOS)
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


def pedir_rango(url, rango, tamano=None):
    pedido = urllib.request.Request(url, headers={"User-Agent": "OQB-catalogo", "Range": rango})
    with urllib.request.urlopen(pedido, timeout=120) as r:
        if r.status != 206:
            raise ValueError(f"el servidor no da rangos (HTTP {r.status})")
        if tamano is not None:
            tamano.append(int(r.headers.get("Content-Range", "/0").rsplit("/", 1)[-1]))
        return r.read()


def indice_zip(url):
    """Los nombres de los archivos de un zip remoto sin bajarlo: el final (donde
    está el índice) y el índice, con dos pedidos parciales. Los ports pesan
    hasta 2 GB; bajarlos enteros cada vez que cambian no tiene sentido."""
    # El tamaño con un pedido de 1 byte: el servidor de GitHub no acepta
    # «los últimos N bytes» (bytes=-N), solo rangos con inicio y fin.
    tamano = []
    pedir_rango(url, "bytes=0-0", tamano)
    total = tamano[0]
    if total <= 0:
        raise ValueError("sin tamaño")
    cola = pedir_rango(url, f"bytes={max(0, total - 65536)}-{total - 1}")
    fin = cola.rfind(b"PK\x05\x06")
    if fin < 0:
        raise ValueError("no es un zip")
    tam, desde = struct.unpack("<II", cola[fin + 12:fin + 20])
    if desde == 0xFFFFFFFF or tam == 0xFFFFFFFF:  # zip64
        z64 = cola.rfind(b"PK\x06\x06")
        if z64 < 0:
            raise ValueError("zip64 sin su registro")
        tam, desde = struct.unpack("<QQ", cola[z64 + 40:z64 + 56])
    indice = pedir_rango(url, f"bytes={desde}-{desde + tam - 1}")
    nombres, p, descomprimido = [], 0, 0
    while p + 46 <= len(indice) and indice[p:p + 4] == b"PK\x01\x02":
        comp, desc = struct.unpack("<II", indice[p + 20:p + 28])
        largo, extra, comentario = struct.unpack("<HHH", indice[p + 28:p + 34])
        nombres.append(indice[p + 46:p + 46 + largo].decode("utf-8", "replace"))
        if desc == 0xFFFFFFFF:
            # zip64: el tamaño real va en el extra 0x0001 (primero el descomprimido).
            datos_extra = indice[p + 46 + largo:p + 46 + largo + extra]
            q = 0
            while q + 4 <= len(datos_extra):
                ident, tam = struct.unpack("<HH", datos_extra[q:q + 4])
                if ident == 1 and tam >= 8:
                    desc = struct.unpack("<Q", datos_extra[q + 4:q + 12])[0]
                    break
                q += 4 + tam
        descomprimido += desc
        p += 46 + largo + extra + comentario
    indice_zip.descomprimido = descomprimido
    return nombres, total


# Lo que varios ports traen igual (los núcleos de RetroArch) no sirve para saber
# si un port está: instalar Metroid Fusion hacía ver instalado Zero Mission.
COMPARTIDOS_PORTS = ("/retroarch/",)


def sin_compartidos(marcas):
    return [m for m in marcas if not m.lower().startswith(COMPARTIDOS_PORTS)]


def icono_acceso(crudo):
    """La portada en JPEG normal de 256 para el acceso directo; devuelve su URL."""
    from PIL import Image
    imagen = Image.open(io.BytesIO(crudo)).convert("RGB").resize((256, 256), Image.LANCZOS)
    for calidad in (92, 85, 75):
        salida = io.BytesIO()
        imagen.save(salida, "JPEG", quality=calidad, progressive=False, optimize=True)
        datos = salida.getvalue()
        if len(datos) <= 0x20000:
            break
    nombre = hashlib.sha1(datos).hexdigest()[:12] + ".jpg"
    os.makedirs(ACCESOS_PORTS, exist_ok=True)
    with open(os.path.join(ACCESOS_PORTS, nombre), "wb") as fichero:
        fichero.write(datos)
    return URL_ACCESOS + nombre


def icono_de_port(url, url_icono):
    """El ícono de ports.json a LADO_PORT y el del acceso directo, o {} si no se pudo."""
    if not url_icono:
        return {}
    try:
        pedido = urllib.request.Request(url_icono, headers={"User-Agent": "OQB-catalogo"})
        with urllib.request.urlopen(pedido, timeout=60) as r:
            crudo = r.read()
        icono = guardar_icono(crudo, LADO_PORT)
        if not icono:
            return {}
        salida = {"icono": icono, "icono_v": ICONO_PORT_V}
        try:
            salida["icono_acceso"] = icono_acceso(crudo)
        except Exception as error:
            print(f"aviso: ícono de acceso directo de {url}: {error}")
        return salida
    except Exception as error:
        print(f"aviso: ícono de {url}: {error}")
        return {}


def versiones_de_port(url, url_icono):
    """Para un port: qué archivos deja en la SD (para saber si está instalado) y
    su ícono, el de ports.json."""
    todos, total = indice_zip(url)
    nombres = [n for n in todos if not n.startswith("__MACOSX") and not n.endswith("/")]
    quitar = envoltorio(nombres)
    propios = [n[len(quitar):] for n in nombres]
    # Primero el .nro del port: es lo que mejor dice que está instalado.
    nros = ["/" + n.lstrip("/") for n in propios if n.lower().startswith("switch/") and n.lower().endswith(".nro")]
    propios_port = [n for n in propios if not ("/" + n.lstrip("/")).lower().startswith(COMPARTIDOS_PORTS)]
    marcas = (nros[:1] + [m for m in marcas_de(propios_port) if m not in nros[:1]])[:3]
    salida = {"marcas": marcas} if marcas else {}
    salida["bytes"] = total
    # Lo que ocupa en la SD: la pestaña Ports lo instala mientras baja (sin el zip).
    salida["descomprimido"] = indice_zip.descomprimido
    salida.update(icono_de_port(url, url_icono))
    return salida


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

    with open(CATALOGO, encoding="utf-8") as fichero:
        catalogo = json.load(fichero, object_pairs_hook=OrderedDict)
    ports = set((catalogo.get("apps_ports") or {}).values())
    iconos_ports = catalogo.get("iconos_ports") or {}

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
            # Un port sin el tamaño descomprimido se vuelve a mirar una vez (tres pedidos parciales).
            if previo.get("fecha") == datos["fecha"] and previo.get("revisado") == REVISION and (url not in ports or "descomprimido" in previo):
                datos.update((k, v) for k, v in previo.items() if k in ("nro_version", "nros", "marcas", "icono", "revisado", "bytes", "icono_v", "icono_acceso", "descomprimido"))
                # Marcas de antes con archivos compartidos: se quitan sin volver a leer el zip.
                if url in ports and datos.get("marcas"):
                    limpias = sin_compartidos(datos["marcas"])
                    if limpias:
                        datos["marcas"] = limpias
                # Un port con el ícono chico de antes: solo se rehace el ícono (un pedido).
                if url in ports and previo.get("icono_v") != ICONO_PORT_V:
                    datos.update(icono_de_port(url, iconos_ports.get(url)))
            else:
                try:
                    if url in ports:
                        datos.update(versiones_de_port(url, iconos_ports.get(url)))
                    else:
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
    if not solo_comprobar and os.path.isdir(ACCESOS_PORTS):
        # Los de accesos directos que ya no usa ningún port no se quedan.
        en_uso = {d["icono_acceso"].rsplit("/", 1)[-1] for d in nuevo.values() if d.get("icono_acceso")}
        for nombre in os.listdir(ACCESOS_PORTS):
            if nombre.endswith(".jpg") and nombre not in en_uso:
                os.remove(os.path.join(ACCESOS_PORTS, nombre))
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
