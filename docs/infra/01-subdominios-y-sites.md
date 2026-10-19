# Infra 01: Subdominios, sites y base de datos en CloudPanel

Guía paso a paso para dejar preparada la infraestructura de Tera-pi en la VPS de Hostinger con CloudPanel.

**Al terminar tendrás:**

| Pieza | Dirección | Tipo de site en CloudPanel | Usuario del site |
| --- | --- | --- | --- |
| Frontend (Angular) | `https://tera-pi.pygmalion.cloud` | Static HTML | `pygmalion-tera-pi` |
| Backend (FastAPI) | `https://api.tera-pi.pygmalion.cloud` | Python 3.12, puerto 8090 | `pygmalion-api-tera-pi` |
| Base de datos | Solo `localhost`, sin acceso desde internet | Dentro del site del backend | Usuario de BD `tera_pi_app` |

**Tiempo estimado:** 45–60 minutos, más la espera de propagación del DNS.

**Datos que vas a usar:**

- IP de la VPS: `93.127.162.184`
- Panel: `https://93.127.162.184:8443`
- Dominio: `pygmalion.cloud` (el site actual `pygmalion.cloud` no se toca)

---

## Paso 0: Antes de empezar

1. **Haz un snapshot de la VPS.** En el panel de Hostinger (hPanel) → **VPS** → tu servidor → **Snapshots y copias de seguridad** → **Crear snapshot**. Si algo sale mal, vuelves a este punto en minutos.
2. **Ten a mano un gestor de contraseñas** (Bitwarden, 1Password, KeePass…). Vas a crear cuatro contraseñas nuevas:
    - usuario del site del frontend;
    - usuario del site del backend;
    - usuario de la base de datos;
    - (más adelante) la API key de ElevenLabs.

> **Por qué un usuario por site:** CloudPanel crea un usuario de Linux por cada site, y cada uno solo puede tocar sus propios ficheros. Si alguien compromete el frontend, no puede leer el código ni las credenciales del backend. Es el principio de **mínimo privilegio**.

---

## Paso 1: Crear los registros DNS

Los registros DNS le dicen a internet que `tera-pi.pygmalion.cloud` y `api.tera-pi.pygmalion.cloud` apuntan a tu VPS. **CloudPanel no gestiona DNS**: se hace donde estén los *nameservers* de tu dominio.

### 1.1 Averigua dónde está tu DNS

El DNS de `pygmalion.cloud` se gestiona en el **Administrador de DNS de Hostinger**: hPanel → **Dominios** → `pygmalion.cloud` → **DNS / Nameservers**.

### 1.2 Crea los dos registros A

En el editor de DNS, añade estos dos registros:

| Tipo | Nombre (Host) | Apunta a (Valor) | TTL |
| --- | --- | --- | --- |
| A | `tera-pi` | `93.127.162.184` | 3600 (o el valor por defecto) |
| A | `api.tera-pi` | `93.127.162.184` | 3600 (o el valor por defecto) |

Detalles que suelen confundir:

- En el campo **Nombre** se pone solo la parte de delante (`tera-pi`, `api.tera-pi`), **sin** `.pygmalion.cloud`. El panel lo añade solo.
- Si el panel tiene un selector de proxy (como la nube naranja de Cloudflare), **desactívalo** de momento. Con el proxy activado, Let's Encrypt puede fallar en el paso 4.
- Si en la zona hay registros **CAA**, comprueba que alguno permite `letsencrypt.org`. Si no hay ningún CAA, no hace falta hacer nada.

### 1.3 Comprueba que resuelven

Desde PowerShell en tu PC:

```powershell
nslookup tera-pi.pygmalion.cloud 8.8.8.8
nslookup api.tera-pi.pygmalion.cloud 8.8.8.8
```

Las dos respuestas deben terminar con `Address: 93.127.162.184`. Si dice `Non-existent domain`, espera 10–15 minutos y repite. Puede tardar hasta unas horas, pero en Hostinger suele ser rápido.

Puedes seguir con los pasos 2, 3 y 5 mientras se propaga. El paso 4 (SSL) sí necesita que el DNS ya resuelva.

---

## Paso 2: Crear el site del frontend (Static HTML)

1. Abre `https://93.127.162.184:8443` e inicia sesión. El navegador avisará de que el certificado no es de confianza: es normal en el acceso por IP.
2. En el menú superior, entra en **Sites**.
3. Pulsa **+ Add Site**.
4. Elige **Create a Static HTML Site**.
5. Rellena el formulario:

    | Campo | Valor |
    | --- | --- |
    | Domain Name | `tera-pi.pygmalion.cloud` |
    | Site User | `pygmalion-tera-pi` |
    | Site User Password | Genera una larga y guárdala en tu gestor |

6. Pulsa **Create**.

CloudPanel crea:

- el usuario de Linux `pygmalion-tera-pi`;
- la carpeta `/home/pygmalion-tera-pi/htdocs/tera-pi.pygmalion.cloud/`, donde irá la build de Angular;
- el virtual host de Nginx para ese dominio.

### 2.1 Página provisional para probar

1. En el site recién creado, abre la pestaña **File Manager**.
2. Crea un fichero `index.html` con este contenido:
    ```html
    <!doctype html>
    <html lang="es"><meta charset="utf-8"><title>Tera-pi</title>
    <body style="font-family:sans-serif;text-align:center;padding-top:20vh">
    <h1>Tera-pi</h1><p>Próximamente.</p></body></html>
    ```
3. Cuando el DNS ya resuelva, abre `http://tera-pi.pygmalion.cloud`. Debes ver "Tera-pi, Próximamente".

### 2.2 Preparar el site para una SPA de Angular

Angular gestiona las rutas en el navegador (`/progreso`, `/sesion`…). Si alguien recarga en `/progreso`, Nginx buscará un fichero `progreso` que no existe y devolverá un 404. La solución es que cualquier ruta desconocida sirva `index.html`.

1. En el site, abre la pestaña **Vhost**.
2. Busca el bloque `location / {`.
3. Asegúrate de que dentro tenga esta línea (si hay otra `try_files`, sustitúyela):
    ```nginx
    try_files $uri $uri/ /index.html;
    ```
4. Pulsa **Save**. Si CloudPanel da un error de sintaxis al guardar, no se aplica nada: copia el mensaje y lo revisamos.

---

## Paso 3: Crear el site del backend (Python)

1. **Sites** → **+ Add Site**.
2. Elige **Create a Python Site**.
3. Rellena el formulario:

    | Campo | Valor |
    | --- | --- |
    | Domain Name | `api.tera-pi.pygmalion.cloud` |
    | Python Version | `3.12` |
    | App Port | `8090` |
    | Site User | `pygmalion-api-tera-pi` |
    | Site User Password | Genera una larga y guárdala en tu gestor |

4. Pulsa **Create**.

CloudPanel crea el usuario `pygmalion-api-tera-pi`, la carpeta `/home/pygmalion-api-tera-pi/htdocs/api.tera-pi.pygmalion.cloud/` y un **proxy inverso**: Nginx recibe las peticiones HTTPS en `api.tera-pi.pygmalion.cloud` y las reenvía a `127.0.0.1:8090`, donde escuchará FastAPI.

> **El App Port es interno.** El puerto 8090 solo escucha en `127.0.0.1` y **no** se abre en el firewall. Desde internet solo se entra por 443 (HTTPS) a través de Nginx. Cada site Python o Node.js necesita un puerto distinto: si algún día creas otro, usa 8091, 8092…

### 3.1 Qué esperar ahora: un 502 (y es correcto)

Si abres `http://api.tera-pi.pygmalion.cloud` verás **502 Bad Gateway**. Es lo esperado: Nginx intenta reenviar al puerto 8090, pero todavía no hay ninguna aplicación escuchando. Cuando despleguemos FastAPI con un servicio de systemd, el 502 desaparecerá.

### 3.2 Prueba opcional: ver el proxy en acción (2 minutos)

Si quieres comprobar que el proxy funciona antes de tener backend:

No hace falta crear usuarios SSH en los sites. Entra con tu usuario de administración y cambia al usuario del site con `sudo`:

1. Conéctate desde PowerShell (con `root` hasta que hagas el endurecimiento de la VPS; después, con tu usuario):
    ```powershell
    ssh root@93.127.162.184
    ```
2. Cambia al usuario del site:
    ```bash
    sudo -iu pygmalion-api-tera-pi
    whoami   # debe responder: pygmalion-api-tera-pi
    ```
3. Arranca un servidor de prueba en el puerto del site:
    ```bash
    cd ~/htdocs/api.tera-pi.pygmalion.cloud
    echo "Hola desde el puerto 8090" > hola.txt
    python3 -m http.server 8090 --bind 127.0.0.1
    ```
4. Abre `http://api.tera-pi.pygmalion.cloud/hola.txt`: verás el texto. Ese es el camino que seguirá cada petición a FastAPI.
5. Vuelve a la terminal, pulsa `Ctrl+C`, borra el fichero (`rm hola.txt`) y sal del usuario del site con `exit`.

> **Por qué no crear usuarios SSH en los sites:** cada acceso SSH es una puerta más que proteger. Basta con uno, tu usuario de administración con clave SSH, y desde él se cambia al usuario de cada site con `sudo -iu`. Cuando automaticemos los despliegues (por ejemplo con GitHub Actions), crearemos un usuario SSH específico, solo con clave y solo para eso.

---

## Paso 4: Certificados SSL (Let's Encrypt)

**Requisito:** el paso 1.3 ya devuelve tu IP para ambos subdominios.

Para **cada uno** de los dos sites:

1. **Sites** → pulsa en el site → pestaña **SSL/TLS**.
2. **Actions** → **New Let's Encrypt Certificate**.
3. Comprueba que aparece el dominio correcto y pulsa **Create and Install**.
4. Abre `https://tera-pi.pygmalion.cloud` y `https://api.tera-pi.pygmalion.cloud`. El candado debe aparecer sin avisos (en la API seguirás viendo el 502, ahora por HTTPS).
5. Comprueba que `http://` redirige a `https://`. Si no redirige, apúntalo y lo añadimos al vhost.

CloudPanel renueva los certificados de Let's Encrypt automáticamente antes de que caduquen (duran 90 días).

**Si falla la emisión:**

| Mensaje o síntoma | Causa probable | Solución |
| --- | --- | --- |
| DNS problem / NXDOMAIN | El DNS aún no ha propagado | Espera y repite el paso 1.3 |
| Connection refused / timeout | El puerto 80 está cerrado en un firewall | Abre el 80 en CloudPanel (Admin Area → Security) y en el firewall de Hostinger, si lo usas |
| CAA record prevents issuance | Un registro CAA no permite Let's Encrypt | Añade un CAA `0 issue "letsencrypt.org"` o elimina el que bloquea |
| Funciona en un subdominio y no en el otro | Error tecleando un registro A | Revisa el registro del que falla |

---

## Paso 5: Base de datos del backend

La base de datos va **dentro del site del backend**, porque solo la usa él.

1. **Sites** → `api.tera-pi.pygmalion.cloud` → pestaña **Databases**.
2. Pulsa **Add Database**.
3. Rellena:

    | Campo | Valor |
    | --- | --- |
    | Database Name | `tera_pi` |
    | Database User Name | `tera_pi_app` |
    | Database User Password | Genera una larga (30+ caracteres) y guárdala en tu gestor |

4. Pulsa **Add Database**.

### 5.1 Averigua el motor y la versión

Lo necesitamos para el ADR-002. Elige uno de estos caminos:

- **phpMyAdmin:** en la pestaña Databases, pulsa **Manage** junto a `tera_pi`. En la página de inicio de phpMyAdmin, en el panel **Servidor de base de datos**, aparece "Tipo de servidor" (MySQL o MariaDB) y la versión.
- **SSH:** conéctate a la VPS y ejecuta `mysql --version`.

### 5.2 Reglas de seguridad de la base de datos

- **No abras el puerto 3306** en ningún firewall. El backend se conecta por `localhost`.
- **El usuario `tera_pi_app` solo tiene permisos sobre `tera_pi`.** El backend nunca usa el usuario root de MySQL.
- **Las credenciales nunca van a git.** Irán en un fichero `.env` en el servidor, fuera de la carpeta pública, con permisos solo para `pygmalion-api-tera-pi`.

### 5.3 Administrarla desde tu PC (túnel SSH)

Si quieres usar DBeaver, MySQL Workbench o similar desde tu PC sin abrir puertos:

```powershell
ssh -L 3307:127.0.0.1:3306 juanjo@93.127.162.184
```

Mientras esa ventana esté abierta, conecta tu cliente a `127.0.0.1`, puerto `3307`, usuario `tera_pi_app`. El tráfico viaja cifrado dentro de SSH. (Usa tu usuario de SSH; si aún no has hecho el endurecimiento de la VPS, será `root` temporalmente.)

---

## Paso 6: Checklist final

- [ ] Snapshot de la VPS creado
- [ ] `nslookup tera-pi.pygmalion.cloud 8.8.8.8` devuelve `93.127.162.184`
- [ ] `nslookup api.tera-pi.pygmalion.cloud 8.8.8.8` devuelve `93.127.162.184`
- [ ] Site `tera-pi.pygmalion.cloud` creado (Static HTML, usuario `pygmalion-tera-pi`)
- [ ] `try_files $uri $uri/ /index.html;` en el vhost del frontend
- [ ] Site `api.tera-pi.pygmalion.cloud` creado (Python 3.12, puerto 8090, usuario `pygmalion-api-tera-pi`)
- [ ] Certificado Let's Encrypt instalado en ambos sites
- [ ] `https://tera-pi.pygmalion.cloud` muestra la página provisional con candado
- [ ] `https://api.tera-pi.pygmalion.cloud` devuelve 502 con candado (correcto por ahora)
- [ ] Base de datos `tera_pi` con usuario `tera_pi_app` creada
- [ ] Motor y versión de la base de datos anotados
- [ ] Las cuatro contraseñas guardadas en el gestor

**Cuando termines, envía a Claude:** el motor y la versión de la base de datos, y cualquier paso que haya dado un error, con el mensaje exacto.
