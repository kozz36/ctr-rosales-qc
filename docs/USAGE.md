# Guía de uso — CTR Rosales QC

Manual operativo para el ingeniero de calidad. Explica **cómo ejecutar** la herramienta y
**cómo usarla** para reconciliar materiales (declarado vs. guías de remisión).

> Referencia técnica: [`ARCHITECTURE.md`](ARCHITECTURE.md) · decisiones de dominio:
> [`DECISIONS.md`](DECISIONS.md). Esta guía es solo de uso.

---

## 1. Qué hace

Ingiere el PDF de Forma (`CTR-PLC01-FR001 Recepción de Materiales en Obra`) y, **por Registro
N°**, compara los materiales **declarados** (texto digital: hoja de detalle + Protocolo de
Recepción) contra la **suma de materiales** de las **guías de remisión** escaneadas. Marca las
diferencias, permite reasignar guías mal archivadas y exporta el resultado a XLSX/CSV.

El **PDF de entrada es de solo lectura**; cada corrida escribe su propia carpeta de salida.

---

## 2. Requisitos

- **Python 3.12** + [`uv`](https://github.com/astral-sh/uv) (backend).
- **Node.js** (frontend, Vite).
- Opcional: red a SUNAT si se usa el modo con cantidades/fechas SUNAT (rompe el air-gap, ver §4).

---

## 3. Cómo ejecutar

Se levantan **dos procesos**: el backend (API, puerto 8000) y el frontend (UI, puerto 5173).

### Backend

```bash
cd backend
uv sync --extra dev                  # primera vez (agregue [ml] para PaddleOCR, [llm] para vision)
uv run uvicorn reconciliation.infrastructure.api.main:app --port 8000
```

### Frontend (en otra terminal)

```bash
cd frontend
npm install                          # primera vez
npm run dev                          # abre http://localhost:5173
```

Abra **http://localhost:5173** en el navegador. El frontend proxea `/api` → backend en `:8000`.

> Atajo: desde la raíz, `make dev` levanta ambos a la vez.

---

## 4. Modos de operación (palancas de configuración)

El comportamiento se controla con variables de entorno al arrancar el **backend** (prefijo
`RECONCILIATION__`, delimitador `__`). Las tres palancas de fuente de datos:

| Variable | Default | Efecto |
|----------|---------|--------|
| `RECONCILIATION__OCR__ENABLED` | `true` | OCR de tablas impresas (PaddleOCR). `false` → sin OCR (NullOcrExtractor). |
| `RECONCILIATION__VISION__ENABLED` | `true` | Lectura de fecha manuscrita de guías por LLM/vision. `false` → **vision-off** (NullVisionAdapter, cero LLM). |
| `RECONCILIATION__SUNAT__ENABLED` | `false` | Consulta SUNAT (cantidades + `fecha_entrega`). **Rompe el air-gap** — opt-in. |

**Combinaciones útiles:**

- **Aire-gapped completo** (sin red, todo local): defaults. Cantidades de guía vía OCR; fechas de
  guía vía vision. Requiere PaddleOCR + un proveedor de vision (Ollama local sirve).
- **Vision-off / SUNAT-authoritative** (determinístico, rápido, habilita ETA real):
  `RECONCILIATION__VISION__ENABLED=false` + `RECONCILIATION__SUNAT__ENABLED=true`. Las fechas de
  guía se resuelven a la `fecha_entrega` de SUNAT (sin lectura manuscrita); las cantidades vienen de
  SUNAT. La fecha **declarada** siempre es el parse digital del Protocolo.
  ```bash
  env RECONCILIATION__VISION__ENABLED=false RECONCILIATION__SUNAT__ENABLED=true \
      uv run uvicorn reconciliation.infrastructure.api.main:app --port 8000
  ```

> **Guardas importantes**
> - **Vision-off requiere SUNAT habilitado.** `vision.enabled=false` + `sunat.enabled=false` se
>   **rechaza al arrancar** (no habría ninguna fuente de fecha).
> - En vision-off, `fecha_entrega` es la fecha de **entrega** SUNAT (cota inferior) usada como
>   recepción — es una aproximación; cualquier divergencia con el Protocolo se marca para revisión.

---

## 5. Flujo de uso (operador)

1. **Subir el PDF.** En la pantalla inicial, arrastre el PDF o presione la zona de carga y
   selecciónelo (solo PDF, máx. 100 MB).
2. **Seguir el progreso.** Aparece una barra **determinada** con el ítem actual (`ítem N/total`),
   el tiempo transcurrido y un **ETA estimado** (se autocorrige). Las etapas avanzan:
   decodificar identidades → clasificar → extraer declarado → OCR/SUNAT de guías → fechas →
   reconciliar.
3. **Revisar la tabla.** Al terminar, la vista de **Revisión** muestra las filas agrupadas **por
   Registro N°** (cabecera `▼ Registro · Fecha · N filas`; el ▼ colapsa/expande el grupo).
4. **Reasignar / inspeccionar.** Expanda una fila (›) para ver el detalle por guía; ahí puede
   **reasignar** una guía mal archivada a su Registro correcto.
5. **Exportar.** Botones **Exportar XLSX** / **CSV** descargan la tabla reconciliada.

---

## 6. Cómo leer la tabla de revisión

**Resumen superior:** contadores de `Coinciden` / `Diferencias` / `Sin guía`, y filtros por estado.

**Columnas:** Registro · Fecha · Material · Unidad · Declarado · Sumado (guías) · Delta · Estado ·
Confianza mín · Páginas origen · Acciones. Las unidades (KG/TN/RD/Rollo) **se suman por separado,
nunca se convierten**.

**Estados:**

| Badge | Significado |
|-------|-------------|
| ✓ **Conforme** | Declarado = sumado de guías (tolerancia exacta, delta 0). |
| ✕ **Diferencia** | Declarado ≠ sumado → revisar. |
| ◇ **Sin guía** | Material declarado sin guía que lo sume. |
| △ **Sin declarado** | Guía sin material declarado correspondiente. |

**Señales de revisión:**

- **⚠ Revisar** (junto a la confianza): la fila requiere revisión manual (baja confianza o fecha
  sin leer). Nunca se auto-corrige.
- **Página en rojo** (en *Páginas origen*): una guía cuya **fecha diverge** (día-mes) de la fecha
  autoritativa del Protocolo. La caja de su número de página **brilla en rojo** (ej. `9` en rojo =
  revisar/reasignar la guía de la página 9). Es señal de guía **mal archivada** (R9), no un error
  de cantidad.
- **Páginas origen**: cajas clickeables con el número de página de cada guía que aporta a la fila;
  click abre/resalta esa página del PDF.

---

## 7. Problemas comunes

- **Todas las filas en "Sin guía"**: no hay fuente de cantidad de guía. Habilite OCR
  (`RECONCILIATION__OCR__ENABLED=true` con PaddleOCR instalado) **o** SUNAT
  (`RECONCILIATION__SUNAT__ENABLED=true`).
- **El backend no arranca con vision-off**: probablemente `sunat.enabled=false` — vision-off
  exige SUNAT (ver §4).
- **Clasificación lenta**: en máquinas sin PaddleOCR funcional, el OCR degrada por página. Para una
  corrida rápida y determinística, use el modo vision-off + SUNAT con `OCR__ENABLED=false`.
