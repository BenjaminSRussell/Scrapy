# Which file is configuration?

Three surfaces look like configuration. They are not layers of one config, and nothing deep-merges them.

| Surface | What reads it |
|---|---|
| [`../config.yml`](../config.yml) | [`Config`](../src/core/config.py) via `get_config()`. [`src/settings.py`](../src/settings.py) does not open a YAML file; `derive_scrapy_config()` reads that object. [`validate_startup_config()`](../src/utils/validation.py) schema-checks the same path. It does not load a second file. |
| `config/{ENV}.yml` (for example `config/development.yml`) | **Not loaded.** This directory does not contain those files. `CONFIG_ENV` is not read. `ENV` does not select a YAML file. |
| [`.env`](../.env.example), copied from `.env.example` | **Not opened by `Config`.** No Python module calls `load_dotenv`. Code reads names that are already in the process environment. |

[#151](https://github.com/BenjaminSRussell/Scrapy/issues/151) was the bug where Scrapy settings loaded a missing `config/{ENV}.yml` and ignored `config.yml`. That issue is closed. `src/settings.py` now uses `get_config()`. Unification of those two loaders is not pending. Do not add the per-environment file back.

## `config.yml`

`Config` opens one path. The default is `Scraping_project/config.yml`: three parents above `src/core/config.py`, which is this project directory, not the process working directory. `_read_file` is `yaml.safe_load` of that file. The mapping becomes one snapshot. `${VAR}` in the file is not expanded.

A missing file logs a warning and installs `_default_config()` (a small built-in dict). It does not open another YAML. A `reload()` that cannot parse the file keeps the previous snapshot.

`get_config(path)` uses `path` only when it creates the process singleton. That path **replaces** `config.yml`. It is not merged on top of it. After the singleton exists, a later path argument is ignored.

`Config.load` does not copy environment variables into the snapshot. A call site that needs a host, port, credential, or similar reads `os.environ` itself. There is no step that overlays `.env` onto the YAML. Which source wins is local to that call site. The names those call sites read are listed in [CONFIGURATION.md](../docs/guides/CONFIGURATION.md).

`ENV` and `APP_ENV` are not a file selector. [`current_env()`](../src/utils/destructive_guard.py) reads them for destructive lake and queue wipes. The shared retry profile uses that same check when the `retry` section of `config.yml` omits a key. Unset means `development` for those checks.

## `config/` is not an environment directory

The only other file shipped here is [`entity_summarization.example.yml`](entity_summarization.example.yml). [`load_entity_config()`](../src/stage4/entity_config.py) does not open it by default. First match wins, and the match is the whole entity-summarization config, not a merge onto `config.yml`:

1. an explicit path argument
2. `$STAGE4_ENTITY_CONFIG` (a YAML file)
3. the `entity_summarization:` block already inside `config.yml`
4. built-in defaults

The example is read only when a caller passes that path. [`examples/entity_summarization_demo.py`](../examples/entity_summarization_demo.py) does, when `STAGE4_ENTITY_CONFIG` is unset. Dropping another `*.yml` in this directory does not load it.

`data/config/` holds unrelated JSON. `CONFIG_DIR` in [`src/core/constants.py`](../src/core/constants.py) points there. Nothing loads it as pipeline YAML.

## `.env`

Python does not parse `.env`. A variable takes effect only when something has already put it in the process environment and a call site reads that name.

- Docker Compose interpolates `Scraping_project/.env` into `${VAR}` references in [`docker-compose.yml`](../docker-compose.yml) (for example `REDIS_PASSWORD=${REDIS_PASSWORD:-}`). That file has no `env_file:` key. Compose does not hand the whole `.env` to `Config`.
- Optional direnv: [`.envrc.example`](../.envrc.example) runs `dotenv_if_exists .env` after you copy it to `.envrc` and allow it.
- The kafka-delta-ingest binary calls `dotenv::dotenv()` in [`main.rs`](../kafka-delta-ingest/src/main.rs). That is a separate loader for its own [`.env`](../kafka-delta-ingest/.env.example), described in its [README](../kafka-delta-ingest/README.md). It is not the Python config loader.
- A plain `python` process does not load `.env` on its own. `python-dotenv` is a dependency, and nothing under `src/` calls it.

Secrets stay in the environment, not in `config.yml`. See [SECURITY.md](../../SECURITY.md).

Dev, stage, and prod share this one YAML file and diverge through process environment variables. The checklist is [ENV_DIVERGENCE.md](../docs/guides/ENV_DIVERGENCE.md). The decision record is [ADR-0001](../../docs/adr/0001-config-yml-is-canonical.md).
