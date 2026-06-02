# Instalación y Configuración de GibbzBridge en ATAS

> Este documento cubre la configuración del indicador C# en ATAS para obtener
> PDH/PDL/ONH/ONL/VAH/VAL/POC automáticamente desde Rithmic.

---

## Prerequisitos

- ATAS instalado con cuenta Rithmic activa
- Visual Studio (cualquier edición) con .NET Framework
- `GibbzBridge.cs` del directorio `core/`

---

## 1. Compilar el indicador

```
1. Abrir Visual Studio → Crear proyecto "Class Library (.NET Framework)"
2. Agregar referencia a ATAS.Indicators.dll
   (ubicación típica: C:\Program Files\ATAS\ATAS.Indicators.dll)
3. Reemplazar Class1.cs con el contenido de GibbzBridge.cs
4. Build → Release
5. Copiar GibbzBridge.dll a carpeta de indicadores ATAS:
   %USERPROFILE%\Documents\ATAS\Indicators\
```

---

## 2. Cargar en ATAS

```
1. Abrir ATAS
2. Indicators → Add → buscar "GibbzBridge"
3. Configurar parámetros:
   - UdpHost: 127.0.0.1
   - UdpPort: 9999
   - VpMethodName: GetAllPriceLevels (valor por defecto)
```

---

## 3. Habilitar VAH/VAL/POC automáticos

**Requisito:** El chart donde cargas GibbzBridge debe ser un **Footprint/Cluster chart**, no un chart OHLCV estándar.

```
1. En ATAS: cambiar el tipo de chart a "Footprint" o "Cluster"
   Chart → Chart Settings → Chart Type → Footprint
2. Reiniciar el indicador GibbzBridge
3. El archivo ~/gibbz_context_levels.json se actualiza cada 60 segundos
   con vah, val, poc calculados desde volumen real de Rithmic
```

Si el chart es OHLCV estándar, `vah/val/poc` aparecerán como `null` en el JSON y
`context_fetcher.py` usará los valores de `levels.json` como fallback.

---

## 4. Verificar que funciona

```powershell
# Verificar que el archivo de contexto existe y tiene fecha de hoy:
Get-Content "$env:USERPROFILE\gibbz_context_levels.json"
```

Salida esperada (con Footprint chart):
```json
{
  "date": "2026-06-01",
  "pdh": 7611.75,
  "pdl": 7572.75,
  "onh": 7625.00,
  "onl": 7568.50,
  "vah": 7605.25,
  "val": 7580.75,
  "poc": 7594.50,
  "source": "rithmic_atas",
  "vp_available": true,
  "updated": "2026-06-01 13:45:22"
}
```

Salida esperada (con chart OHLCV estándar):
```json
{
  "date": "2026-06-01",
  "pdh": 7611.75,
  "pdl": 7572.75,
  "onh": 7625.00,
  "onl": 7568.50,
  "vah": null,
  "val": null,
  "poc": null,
  "source": "rithmic_atas",
  "vp_available": false,
  "updated": "2026-06-01 13:45:22"
}
```

---

## 5. Si el nombre del método es diferente en tu versión de ATAS

El método `GetAllPriceLevels()` se llama via `dynamic` dispatch. Si tu versión
de ATAS usa un nombre diferente, hay dos opciones:

**Opción A:** Cambiar el parámetro `VpMethodName` en el panel de ATAS:
```
GibbzBridge → VpMethodName → [nuevo nombre]
```

**Opción B:** Verificar el nombre correcto en Visual Studio:
```
View → Object Browser → ATAS.Indicators → ICandle → Methods
```
Buscar el método que devuelve colección de precios con volumen.

Nombres alternativos comunes en distintas versiones de ATAS:
- `GetPriceLevels()`
- `GetClusters()`
- `PriceLevels` (propiedad, no método)

---

## 6. Verificar desde Python

```powershell
# Ver qué fuentes está usando context_fetcher:
python context_fetcher.py

# Salida esperada con ATAS corriendo (Footprint chart):
#   PDH  : 7611.75     PDL  : 7572.75  [rithmic_atas]
#   ONH  : 7625.00     ONL  : 7568.50  [rithmic_atas]
#   VAH  : 7605.25     VAL  : 7580.75  [rithmic_atas]

# Salida sin ATAS (solo yfinance + levels.json):
#   PDH  : 7611.75     PDL  : 7572.75  [yfinance]
#   ONH  : 7200.00     ONL  : 7130.00  [levels_json_stale]
#   VAH  : 7175.00     VAL  : 7145.00  [levels_json]
```

---

## 7. Resumen de fuentes por campo

| Campo | Con ATAS Footprint | Con ATAS OHLCV | Sin ATAS |
|-------|-------------------|----------------|---------|
| PDH / PDL | Rithmic 100% | Rithmic 100% | yfinance ~99% |
| ONH / ONL | Rithmic 100% | Rithmic 100% | Manual |
| VAH / VAL / POC | Rithmic 100% | Manual | Manual |

**Con ATAS Footprint chart: contexto 100% automático y 100% Rithmic.**
