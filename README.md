# agenda-data · robots de datos de Agenda Deportiva

Dos robots gratis de GitHub Actions. Guardan JSON en `data/`; la app los lee por la URL "raw".

| Robot | Qué hace | Cuándo | Necesita |
|---|---|---|---|
| `boxeo.yml` → `data/fights.json` | Lee el calendario público de boxeo de ESPN.com (sin IA): fecha, ciudad, canal y cartelera completa | 2 veces al día | nada (Groq solo si ESPN cambia el formato) |
| `canales.yml` → `data/rights.json` | Lee de Wikipedia quién transmite cada liga en cada país; Groq lo pasa a JSON y cada país y canal se valida | lunes | secreto `GROQ_API_KEY` |

## Montarlo (10 min)
1. GitHub → **New repository** → nombre `agenda-data` → **Public** → Create.
2. **Add file → Upload files** → sube TODO lo de este zip (incluida la carpeta `.github`). Commit.
   - Desde el celular la carpeta `.github` a veces no sube: créala a mano con **Add file → Create new file** y escribe `.github/workflows/boxeo.yml`, pega el contenido; igual con `canales.yml`.
3. **Settings → Secrets and variables → Actions → New repository secret**: `GROQ_API_KEY` = tu llave de console.groq.com.
4. **Actions** → "Carteleras de boxeo" → **Run workflow**. Luego "Canales por país (Wikipedia)" → **Run workflow** (tarda ~5 min).
5. En Cloudflare Pages → tu proyecto → **Settings → Variables**: `DATA_URL` = `https://raw.githubusercontent.com/TU-USUARIO/agenda-data/main/data/` → vuelve a desplegar.

## Revisar que funcionó
- `https://TU-APP.pages.dev/api/fights` → debe salir Canelo vs. Mbilli (31 oct, DAZN PPV).
- `https://TU-APP.pages.dev/api/rights` → en `status` cada liga dice `ok` y cuántos países. Las que digan `ok:false` no pisan nada y la app dice "sin dato".

Probar sin internet: `python scripts/boxeo.py --demo` y `python scripts/canales.py --demo`.
