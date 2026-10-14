# Infra 02: Seguridad del servidor

Registro de los cambios de seguridad hechos en la VPS (`srv1066983.hstgr.cloud`, `93.127.162.184`): qué se cambió, por qué y cómo deshacerlo. Si algo cambia en el servidor, se actualiza este documento.

**Última actualización:** 12 de octubre de 2026

## Estado actual

| Área | Estado | Detalle |
| --- | --- | --- |
| Sistema | ✅ Al día | Ubuntu 24.04.4 LTS, actualizado y reiniciado el 12/10/2026 |
| Huella SSH del servidor | ✅ Verificada | `SHA256:jhb6tX2mqRPnqbAf8e1+HORwZAU7rtFrpkRMf0L0I+s` (ED25519) |
| Firewall (`ufw`) | ✅ Activo | Solo 22, 80, 443 (TCP y UDP) y 8443 |
| MySQL (Percona 8.4) | ✅ Solo `localhost` | Escucha en `127.0.0.1:3306` y `127.0.0.1:33060` |
| Usuario de BD de Tera-pi | ✅ Mínimo privilegio | `terapiapp`, solo con permisos sobre la base de datos `terapi` |
| HTTPS | ✅ Let's Encrypt | `tera-pi.pygmalion.cloud` y `api.tera-pi.pygmalion.cloud`, renovación automática por CloudPanel |
| Acceso SSH | ✅ Solo con clave | Administración con `juanjoadmin` (clave + `sudo` con contraseña); `root` solo con clave; contraseñas desactivadas |

## Cambios realizados

### 1. Actualización del sistema y reinicio (12/10/2026)

El servidor llevaba 21 semanas sin reiniciarse, con un reinicio pendiente: estaba ejecutando un kernel antiguo, sin los parches de seguridad ya descargados.

```bash
apt update && apt upgrade -y
reboot
```

**Comprobación:** `[ -f /var/run/reboot-required ]` ya no existe y `nginx` y `mysql` arrancaron `active`.

### 2. MySQL escucha solo en `localhost`

**Antes:** MySQL escuchaba en todas las interfaces (`*:3306` y `*:33060`). El firewall bloqueaba esos puertos, pero era la única capa de protección.

**Por qué:** defensa en profundidad. Si un día el firewall se desactiva o alguien abre el puerto por error, la base de datos, con datos de salud, no debe quedar expuesta.

**Antes de cambiarlo se comprobó** que nada externo usaba MySQL por red:

- No había conexiones activas desde otras IP.
- El único contenedor Docker (`n8n-automat`) no usa MySQL: no tiene `DB_TYPE`, así que usa su SQLite interno.

**Cambio:** fichero `/etc/mysql/mysql.conf.d/zz-bind-localhost.cnf`. El prefijo `zz-` hace que se lea el último y prevalezca sobre cualquier otro ajuste:

```ini
# MySQL solo escucha en localhost (defensa en profundidad, ver ADR-002 de Tera-pi)
[mysqld]
bind-address = 127.0.0.1
mysqlx-bind-address = 127.0.0.1
```

```bash
mysqld --validate-config --user=mysql   # validar antes de reiniciar
systemctl restart mysql
ss -tlnp | grep -E ':3306|:33060'       # debe mostrar 127.0.0.1
```

**Cómo deshacerlo:**

```bash
rm /etc/mysql/mysql.conf.d/zz-bind-localhost.cnf && systemctl restart mysql
```

**Importante para el futuro:** si algún contenedor Docker necesita MySQL, no podrá usar `127.0.0.1`, porque dentro de un contenedor esa dirección es el propio contenedor. Habría que añadir también la IP del puente de Docker (normalmente `172.17.0.1`) en `bind-address` y permitirlo en el firewall solo desde la red de Docker.

### 3. Firewall: rango de puertos sobrante eliminado

**Antes:** la regla `8433:8443/tcp` abría 11 puertos (del 8433 al 8443), seguramente por una errata, cuando CloudPanel solo necesita el 8443. Esa regla estaba en `ufw` pero no aparecía en el panel de firewall.

```bash
ufw allow 8443/tcp               # primero la regla correcta, para no perder el acceso al panel
ufw delete allow 8433:8443/tcp
```

**Reglas actuales** (`ufw status`), iguales en IPv4 e IPv6:

| Puerto | Para qué |
| --- | --- |
| 22/tcp | SSH |
| 80/tcp | HTTP (redirige a HTTPS; necesario para Let's Encrypt) |
| 443 (TCP y UDP) | HTTPS; el UDP es para HTTP/3, ahora desactivado en los vhosts |
| 8443/tcp | Panel de CloudPanel |

**Nota:** hay dos sitios donde se ven reglas de firewall, el panel y `ufw` en la terminal, y no siempre coinciden. La fuente de verdad es `ufw status` en el servidor.

### 4. Acceso SSH solo con clave y usuario de administración propio

**Antes:** se entraba como `root` con contraseña (`PermitRootLogin yes`, `PasswordAuthentication yes`). El puerto 22 está abierto a todo internet y hay bots probando contraseñas a diario.

**Por qué no se usó el usuario `juanjo`:** es el **usuario del site `pygmalion.cloud`** que creó CloudPanel. Darle `sudo` mezclaría dos papeles: un fallo en esa web daría a un atacante el control de todo el servidor. Se creó un administrador aparte que no es dueño de ninguna web.

**Usuario de administración:**

```bash
adduser --gecos "" juanjoadmin   # contraseña fuerte: la pedirá sudo
usermod -aG sudo juanjoadmin
```

Clave pública copiada desde el PC (Windows) a `/home/juanjoadmin/.ssh/authorized_keys` (carpeta `700`, fichero `600`; con permisos más abiertos SSH ignora la clave).

**Antes de cerrar el acceso se revisó quién entraba** (`journalctl -u ssh`, últimos 30 días):

| Entrada | Quién era |
| --- | --- |
| `password root` desde `83.57.51.231` | El administrador, desde casa |
| `publickey root` desde `169.254.0.1` | El Terminal del navegador de Hostinger (dirección interna), con una clave suya |
| `password root` desde `93.127.162.184` | También el Terminal del navegador: siempre segundos después de la entrada anterior |

No había procesos automáticos (despliegues, `gitlab-runner`) que dependieran del acceso con contraseña.

**Cambio:** fichero `/etc/ssh/sshd_config.d/01-hardening.conf`. Empieza por `01-` porque en `sshd` **gana la primera aparición** de cada directiva, y `50-cloud-init.conf` activaba las contraseñas:

```
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
```

Se usa `prohibit-password` y no `no` para que `root` siga pudiendo entrar **con clave**: así se conserva el acceso interno de Hostinger como vía de emergencia.

```bash
sudo sshd -t                 # validar antes de reiniciar
sudo systemctl restart ssh   # no corta las sesiones abiertas
sudo sshd -T | grep -E "^(permitrootlogin|passwordauthentication|kbdinteractiveauthentication|pubkeyauthentication) "
```

**Comprobación desde el PC:** `ssh -o PubkeyAuthentication=no juanjoadmin@93.127.162.184` y lo mismo con `root` responden `Permission denied (publickey)` sin pedir contraseña.

**Cómo deshacerlo** (desde una sesión abierta, o desde el Terminal del navegador de Hostinger):

```bash
sudo rm /etc/ssh/sshd_config.d/01-hardening.conf && sudo systemctl restart ssh
```

**Para entrar a partir de ahora:** `ssh juanjoadmin@93.127.162.184`, y `sudo` para tareas de administración. Para trabajar con los ficheros de un site: `sudo -iu pygmalion-api-tera-pi`.

## Pendiente

- [ ] **Comprobar que el Terminal del navegador de Hostinger sigue funcionando** con las contraseñas desactivadas.
- [ ] **Actualizaciones automáticas de seguridad:** comprobar que `unattended-upgrades` está instalado y activo.
- [ ] **Proteger el panel (8443):** limitarlo a tu IP si es fija, o añadir autenticación básica.
- [ ] **Copias de seguridad remotas** de CloudPanel (fuera de la VPS y cifradas) antes de tener datos reales.
- [ ] Valorar `fail2ban` para bloquear IP que fuercen el acceso SSH, si se mantiene el puerto 22 abierto a todo internet.
