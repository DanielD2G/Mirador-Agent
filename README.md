# Mirador

**Agente SRE de línea de comandos que investiga incidentes de producción con
herramientas de solo lectura, formula hipótesis en paralelo y entrega una causa
raíz respaldada por evidencia citable.**

Mirador recibe una pregunta (`¿por qué la API devuelve 500?`) o una alerta,
consulta logs, métricas, cambios recientes, código y red en AWS, Datadog, Grafana
y GitHub, cruza los resultados con la documentación del equipo y con la memoria de
incidentes anteriores, y produce un reporte donde cada afirmación apunta a la
evidencia que la sostiene.

> **Estado:** MVP. Solo CLI, solo OpenRouter como proveedor de modelos. La calidad
> con modelos reales todavía no está medida (ver [Evaluaciones](#evaluaciones)).

---

## Contenido

- [Cómo funciona](#cómo-funciona)
- [Instalación](#instalación)
- [Configuración](#configuración)
- [Uso](#uso)
- [Herramientas disponibles](#herramientas-disponibles)
- [Seguridad y privacidad](#seguridad-y-privacidad)
- [Rol AWS de referencia](#rol-aws-de-referencia-miradorreadonly)
- [Evaluaciones](#evaluaciones)
- [Arquitectura](#arquitectura)
- [Desarrollo](#desarrollo)
- [Limitaciones conocidas](#limitaciones-conocidas)
- [Licencia](#licencia)

## Cómo funciona

```
pregunta / alerta
      │
      ▼
┌─────────────┐   plan    ┌──────────────────────────────────────────┐
│ Orquestador │ ────────► │ Subagentes en paralelo (uno por hipótesis)│
└─────────────┘           │  · siempre incluye "¿qué cambió?"         │
      ▲                   │  · tools de solo lectura                  │
      │  hallazgos        │  · resultados → workspace DuckDB          │
      └───────────────────┴──────────────────────────────────────────┘
      │
      ▼
 chequeo de completitud ──► reporter ──► reporte con evidencia citada
```

1. **Plan.** El orquestador lee el disparador, recupera incidentes parecidos de la
   memoria y propone hipótesis. Siempre agrega una hipótesis de "qué cambió"
   (deploys, PRs, cambios de infraestructura en la ventana configurada).
2. **Investigación.** Cada hipótesis va a un subagente con su propio presupuesto.
   Los resultados grandes se cargan en un workspace DuckDB local, y el subagente
   los analiza con SQL en lugar de recibir miles de filas en el prompt.
3. **Completitud.** Antes de concluir, un paso verifica que no queden caminos
   obvios sin explorar.
4. **Reporte.** El reporter redacta la causa raíz, su nivel de confianza, las
   hipótesis descartadas y los callejones sin salida. Una afirmación solo cuenta
   como evidencia si cita un resultado real de una tool.

Si se agota el presupuesto, se cancela con Ctrl-C o falla el modelo, Mirador
entrega un **reporte parcial** con lo que llegó a establecer, en vez de nada.

## Instalación

Requisitos: Python ≥ 3.12 (se desarrolla con 3.13), [uv](https://docs.astral.sh/uv/)
y Docker Compose (solo para la memoria con FalkorDB).

```sh
git clone https://github.com/DanielD2G/Mirador-Agent.git
cd Mirador-Agent
uv sync --locked
cp mirador.example.yaml mirador.yaml
docker compose up -d falkordb      # opcional: memoria de incidentes
uv run mirador doctor              # verifica configuración y credenciales
```

## Configuración

La CLI busca `mirador.yaml` en el directorio actual y después en
`~/.mirador/mirador.yaml`. Con `--config ruta.yaml` se elige otro archivo. Partir
de [`mirador.example.yaml`](mirador.example.yaml) y completar:

| Sección | Qué configurar |
| --- | --- |
| `models` | IDs de modelos OpenRouter para orquestador (con fallback), subagentes, reporter, extracción de memoria y embeddings |
| `budgets` | Topes por investigación (tiempo, llamadas, USD, tokens) y mensuales |
| `targets.aws` | Perfiles y regiones AWS, y el mapeo de servicio a log groups de CloudWatch |
| `providers` | Site de Datadog, URL y UIDs de datasources de Grafana, repos de GitHub |
| `docs.path` | Carpeta con la documentación Markdown del equipo |
| `workspace` | Umbrales, timeout, memoria y retención del workspace DuckDB |
| `memory` | URL de FalkorDB para Graphiti |
| `sensitive_tools` | Tools sensibles; todas apagadas por defecto |
| `redaction.extra_patterns` | Patrones adicionales a redactar |
| `quotas` | Llamadas por minuto por grupo de cuota (p. ej. `datadog-logs: 60`) |

`data_dir` es opcional y por defecto vale `~/.mirador`. Ahí se guardan el store
SQLite, los workspaces DuckDB y la clave HMAC local.

### Secretos

Los secretos se leen **solo de variables de entorno**. No van en el YAML y no se
versionan.

| Variable | Uso |
| --- | --- |
| `OPENROUTER_API_KEY` | Obligatoria para investigar y para evals reales |
| `DD_API_KEY`, `DD_APP_KEY` | Datadog: lectura de logs, métricas y monitores |
| `GRAFANA_TOKEN` | Service account con rol Viewer y acceso a Loki/Prometheus |
| `GITHUB_TOKEN` | Token fine-grained limitado a los repos configurados (`contents:read`, `metadata:read`, `deployments:read`, lectura de PRs) |
| `MIRADOR_HMAC_KEY` | Opcional. Si falta, `doctor` genera una clave local |

AWS usa la cadena de credenciales estándar: perfiles locales o SSO, asumiendo
idealmente el rol [`MiradorReadOnly`](#rol-aws-de-referencia-miradorreadonly).

## Uso

```sh
# Pregunta libre
uv run mirador ask '¿Por qué la API devuelve 500 desde las 14:00?'

# Seguimiento sobre la misma investigación
uv run mirador ask '¿Qué cambió antes del incidente?' --thread <ID>

# A partir de una alerta (archivo JSON o texto)
uv run mirador investigate --alert alerta.json

# Salida JSON para integrar con otras herramientas
uv run mirador ask '¿Por qué subió la latencia?' --json

# Memoria de incidentes
uv run mirador memory list --since 7d
uv run mirador memory confirm <ID>
uv run mirador memory reject <ID>

# Diagnóstico y versión
uv run mirador doctor
uv run mirador version
```

Cada subcomando acepta `--help`.

### Memoria vs. documentación

- **Memoria** (Graphiti + FalkorDB): lo que el agente aprendió. Los hallazgos de
  cada investigación se guardan como episodios `unverified`, y una persona los pasa
  a `confirmed` o `rejected` con `memory confirm/reject`. La memoria se presenta al
  modelo como dato no confiable y nunca cuenta como evidencia por sí sola. Si no
  hay embeddings disponibles, la búsqueda cae a BM25.
- **Documentación** (`docs.path`): lo que escribe el equipo, como runbooks,
  arquitectura o postmortems. El agente busca y lee esos archivos Markdown, pero no
  los modifica.

## Herramientas disponibles

Las tools se organizan por **capacidad** y por **proveedor**
(`<capacidad>.<proveedor>.<acción>`). Así el orquestador razona sobre "logs" o
"cambios" sin acoplarse a un proveedor concreto.

| Capacidad | Acciones | Proveedores |
| --- | --- | --- |
| `logs` | `search`, `count`, `histogram`, `top_values` | AWS CloudWatch Logs, Datadog, Grafana Loki |
| `metrics` | `query_range`, `alerts` | AWS CloudWatch, Datadog, Grafana Prometheus |
| `changes` | `recent` | GitHub (PRs, deploys), AWS CloudTrail |
| `code` | `search`, `read_file`, `diff` | GitHub |
| `network` | `load_balancers`, `nat_gateways`, `waf_rules`, `waf_sampled_requests`, `service_status` | AWS |
| `docs` | `search`, `read` | Markdown local |
| `memory` | `recall`, `record` | Graphiti |
| `workspace` | `tables`, `sql` | DuckDB local |

Todas las llamadas pasan por el mismo pipeline: validación de argumentos, guard de
permisos, chequeo de ventana temporal, presupuesto y cuota, caché, ejecución con
timeout y reintentos, redacción, truncado, carga en el workspace, auditoría y, al
final, un sobre `<untrusted_data>` que marca el resultado como dato no confiable.

## Seguridad y privacidad

- **Solo lectura.** Los adaptadores solo emiten operaciones de lectura. Las tools
  sensibles (por ejemplo, los access logs de ALB en S3) están apagadas y requieren
  habilitación explícita por bucket y prefijo.
- **Redacción antes del modelo.** Claves de AWS y Datadog, tokens de GitHub,
  JWT, bearer tokens, contraseñas, tarjetas, emails y valores de alta entropía
  junto a claves como `auth`, `session` o `signature` se reemplazan por seudónimos HMAC deterministas (`<EMAIL_3f2a>`)
  antes de llegar al modelo, y el reporte final pasa por una segunda redacción.
  Como el seudónimo es estable, el modelo puede correlacionar valores sin verlos.
- **Inyección de prompt (mitigada, no eliminada).** Todo lo que viene de afuera
  (logs, código, docs, memoria) llega delimitado como `<untrusted_data>` y el
  prompt de seguridad lo trata como dato. Cada conclusión tiene que citar una
  llamada real a una tool; docs y memoria no cuentan como evidencia. La validación
  comprueba que la cita exista, no que la conclusión se siga del dato: un modelo
  que obedece un log malicioso todavía puede citarlo. Revisá la evidencia antes de
  actuar.
- **Workspace DuckDB aislado.** Sin acceso externo (`enable_external_access=false`),
  configuración bloqueada, solo `SELECT`/`WITH`, y metadatos internos fuera de la
  conexión que usa el modelo.
- **Sin telemetría.** El tracing del OpenAI Agents SDK está apagado. Solo se
  contacta a OpenRouter (y los proveedores de modelos que elijas a través de él)
  y a las APIs configuradas.
- **Datos locales.** Transcripts, workspaces y memoria quedan en tu máquina.
  FalkorDB se publica únicamente en `127.0.0.1`.
- **Presupuestos.** Por defecto: 10 min, 80 llamadas, USD 1 y 2M tokens por
  investigación; USD 30 por mes. Los evals tienen un presupuesto aparte (USD 50
  mensuales, USD 10 por corrida). El costo se descuenta cuando termina cada
  respuesta del modelo, así que una llamada admitida cerca del límite, o varias
  en paralelo, pueden superarlo levemente. Son límites operativos, no un tope
  financiero exacto: para eso, configurá también un límite de crédito en
  OpenRouter.

## Rol AWS de referencia: MiradorReadOnly

1. Rol `MiradorReadOnly` con trust policy hacia quien ejecute Mirador (rol de
   tarea o usuario SSO).
2. Managed policy `ReadOnlyAccess`. `ViewOnlyAccess` no alcanza: le faltan
   `logs:FilterLogEvents`, `logs:StartQuery`, `cloudtrail:LookupEvents` y
   `wafv2:GetSampledRequests`.
3. Inline deny sobre el acceso a datos y credenciales (ejemplo abajo).
4. Session policy al asumir el rol, que repite los denies y limita regiones.
5. Permission boundary para que el rol no pueda ampliarse.

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Deny",
    "Action": [
      "s3:GetObject", "secretsmanager:GetSecretValue",
      "ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath",
      "kms:Decrypt", "lambda:GetFunction", "dynamodb:Scan"
    ],
    "Resource": "*"
  }]
}
```

El fragmento no incluye trust policy, límite regional ni permission boundary. Un
deny de `s3:GetObject` también bloquea la tool opcional de access logs de ALB:
habilitarla en el YAML no cambia IAM.

## Evaluaciones

Los evals usan datos sintéticos y adaptadores falsos que se comportan como motores
de consulta. La orquestación, el pipeline, la redacción, SQLite y DuckDB son los
reales. No hacen falta cuentas de observabilidad.

```sh
# Sin modelo: guion fijo, valida el cableado completo
uv run mirador eval --suite core --dry-run --config mirador.example.yaml
uv run mirador eval --suite redaction --config mirador.example.yaml

# Con OPENROUTER_API_KEY y modelos reales (consume crédito, con tope)
uv run mirador eval --suite core --runs 5
uv run mirador eval --case memory-helps --runs 5
```

La suite core tiene 10 casos, entre ellos un "TypeError" que en realidad era el
WAF, una colación que resultó ser un ataque, inyección en logs, fuga de secretos,
ruido nocturno, presupuesto agotado, errores de autenticación y la memoria como
atajo. Cada caso mide menciones obligatorias, juicio de un LLM, resistencia a
inyección, secretos filtrados (sobre el tráfico crudo hacia el modelo), precisión
de la evidencia y callejones sin salida. Los resultados quedan en `evals/results/`.

Formato de casos, fixtures y métricas: [evals/README.md](evals/README.md).

## Arquitectura

Arquitectura hexagonal con capas que [import-linter](.importlinter) verifica en CI:

```
src/mirador/
├── domain/         # modelos puros: trigger, presupuesto, investigación, reporte
├── application/    # puertos (Protocols), casos de uso, orquestación, pipeline de tools
│   ├── ports/      #   capacidades: logs, metrics, changes, code, network, docs
│   ├── tooling/    #   spec, pipeline, registry y catálogo por capacidad
│   ├── orchestration/
│   ├── prompts/
│   └── use_cases/  #   ask, investigate, doctor, memory_admin, run_eval
├── redaction/      # redactor y seudonimización HMAC
├── adapters/       # implementaciones concretas
│   ├── agents/     #   OpenAI Agents SDK sobre OpenRouter
│   ├── providers/  #   aws, datadog, grafana, github, localdocs
│   ├── workspace/  #   DuckDB
│   ├── memory/     #   Graphiti / deshabilitada
│   ├── store/      #   SQLite
│   ├── cli/        #   Typer + Rich
│   └── eval/       #   fakes y runtime con guion
└── bootstrap.py    # composition root
```

`domain` no depende de nada. `application` depende solo de `domain`, y los
adaptadores implementan los puertos. Agregar un proveedor nuevo consiste en
implementar el puerto de la capacidad y registrarlo en `bootstrap.py`, sin tocar
la orquestación.

## Desarrollo

```sh
uv sync --locked
scripts/check.sh    # ruff, ruff format, mypy --strict, import-linter
```

El tipado es estricto (`mypy --strict`) y el runtime corre sobre `uvloop`. Por
ahora la validación es por evals; los tests unitarios y las pruebas contra
servicios reales quedan para la fase 2.

## Limitaciones conocidas

- Solo CLI y solo OpenRouter.
- Los criterios de calidad con modelos reales no están verificados todavía.
- Uso de un solo usuario: procesos CLI concurrentes no coordinan los presupuestos
  mensuales.
- El store SQLite usa el schema v2. Abrir un store v1 de desarrollo recrea
  `tool_calls`, `hypotheses` y `findings` sin migrar esos registros.

## Licencia

Licencia pendiente de definir. [NOTICE](NOTICE) documenta las referencias
externas: el proyecto toma ideas de HolmesGPT y OpenSRE, pero no copia código.
