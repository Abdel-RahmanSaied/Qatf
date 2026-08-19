# Stage 3 providers

Stage 3 is the only model call in the pipeline, and its contract is one line:

> transcript in, JSON clip list out

That is what makes the provider swappable — and it bounds the blast radius.
**A provider swap cannot affect cut accuracy or rendering**, because stages 1, 4
and 5 are model-free. It changes only *which passages* get picked.

```bash
QATF_LLM_PROVIDER=anthropic     # openai | kimi | glm | ollama | vllm | openrouter
QATF_LLM_MODEL=...              # override the preset's default model
QATF_LLM_BASE_URL=...           # point a preset at another host (proxy, self-host)
```

`GET /healthz` reports the live roster, so you never have to trust this table
over the code.

---

## The roster

| Key | Default model | Credential | Structured output | Context |
| --- | --- | --- | --- | --- |
| `anthropic` | `claude-opus-5` | `ANTHROPIC_API_KEY` | `json_schema` | 1 M |
| `openai` | `gpt-5.1` | `OPENAI_API_KEY` | `json_schema` | 400 K |
| `openai-legacy` | `gpt-4.1` | `OPENAI_API_KEY` | `json_schema` | 128 K |
| `kimi` | `kimi-k2-thinking` | `MOONSHOT_API_KEY` | `json_object` | 256 K |
| `glm` | `glm-4.6` | `ZHIPU_API_KEY` | `json_object` | 200 K |
| `ollama` | `glm4:9b` | none | `json_object` | 128 K |
| `vllm` | `zai-org/GLM-4-9B-0414` | none | `json_schema` | 128 K |
| `openrouter` | `anthropic/claude-opus-5` | `OPENROUTER_API_KEY` | `json_object` | 1 M |

`openai-legacy` exists because the GPT-4 family still takes `max_tokens` and
`temperature` while the GPT-5 family rejects both. That is a capability
difference, not a model preference — see below.

---

## Two SDK families, deliberately

The official `anthropic` SDK for Claude; the `openai` SDK for everything
OpenAI-compatible.

> **Never route Claude through an OpenAI-compatible shim.** It costs
> schema-constrained output, adaptive thinking and prompt caching of the
> transcript prefix — all of which this stage uses.

Each SDK sits behind its own extra, so installing one provider does not pull the
others:

```bash
cd qatf-backend
pip install -e ".[api,anthropic]"
pip install -e ".[api,openai]"      # also drives kimi, glm, vllm, ollama, openrouter
pip install -e ".[all]"
```

---

## Structured output is three tiers, not a boolean

| Tier | Providers | What you get |
| --- | --- | --- |
| `json_schema` | Anthropic, OpenAI, vLLM | Constrained decoding. Malformed output is **impossible**. |
| `json_object` | Kimi, GLM, Ollama, OpenRouter | Valid JSON guaranteed, **shape is not**. |
| `prompt_only` | anything else | Nothing but the prompt. |

`parse_response` is the layer every tier shares, which is why it stays defensive
— fence stripping, wrapper-key tolerance, bare-array fallback — even though the
top tier makes it redundant. **Deleting that defence would break the bottom two
tiers silently, at selection time, on someone else's video.**

The requested shape is `{"clips": [...]}` and not a bare array because **OpenAI
strict mode rejects an array at the schema root**. A bare array is still accepted
on parse, since `json_object`-only providers routinely drop the wrapper.

---

## Capabilities are declared, never probed

A rejected parameter is a `400`, not a graceful degrade:

- Claude Opus 5 and the GPT-5 family both **reject `temperature`**.
- The GPT-5 family renamed `max_tokens` to `max_completion_tokens` and **rejects
  the old name**.

So `Capabilities` on each preset states what may be sent, and the abstraction
never blanket-forwards a parameter.

> **Adding a vendor should be a row in [`presets.py`](../qatf-backend/qatf/llm/presets.py), not
> a subclass.** If it needs a subclass, the reason must be a real protocol
> difference — not a different `base_url`.

Where a vendor's support is ambiguous the preset is deliberately **pessimistic**:
over-claiming fails the request, under-claiming just leans on `parse_response`.

Ollama is pinned to `json_object` for a sharper reason — it *ignores* unknown
fields rather than erroring, so a `json_schema` request there would silently
produce unconstrained output, which is worse than a failure.

---

## Self-hosting

```bash
docker compose --profile ollama up    # easy path
docker compose --profile vllm up      # guided decoding, so json_schema is real
```

Both pull GLM-4-9B, which runs on one 16–24 GB GPU quantised.

> **Kimi K2 is ~1T parameters and does not self-host on a consumer GPU.** Use the
> hosted Moonshot API or OpenRouter. Anyone who reads "open-source model" as
> "therefore local" will burn an afternoon on this.

---

## Choosing one

**Getting started, or comparing several:** `openrouter`. One key, many models,
and vendor-prefixed ids you can swap without changing anything else. It is also
the only provider here that has served a real request from this project.

**Best Arabic judgment, as far as anyone knows:** `anthropic` or `openai` — see
the open question below.

**No data leaving the host:** `vllm`. It is the only self-host option with real
schema constraint, because it supports guided decoding.

**Easiest local setup:** `ollama`. Accept `json_object` and lean on
`parse_response`.

---

## Open question: Arabic selection quality

The provider roster is **untested against the thing this project exists for.**

GLM and Kimi are strongest on Chinese and English; their Arabic *judgment* —
picking a self-contained passage, hearing a hook — is unmeasured. Claude and GPT
are the safer assumption there, and that is an assumption, not a measurement.

Before switching the Arabic path to an open model, A/B it on the same transcript
and **read the clips**. `--plan-only` makes that cheap:

```bash
cd qatf-backend
QATF_LLM_PROVIDER=openrouter QATF_LLM_MODEL=anthropic/claude-opus-5 \
  qatf talk.mov -o cmp-claude/ --language ar --plan-only
QATF_LLM_PROVIDER=glm \
  qatf talk.mov -o cmp-glm/ --language ar --plan-only
diff cmp-claude/plan.json cmp-glm/plan.json
```

Each output directory keeps its own transcript cache, so the first run of each
pair transcribes. Copy `cmp-claude/.work/qatf.db` into `cmp-glm/.work/` to
skip the second — the comparison is only honest on an identical transcript
anyway.

---

## Verification status

**`smoke_llm.py` (38 checks) pins what we *send*, with the SDK client faked:**
that Anthropic gets `output_config.format` and no sampling params, that GPT-5
gets `max_completion_tokens`, that Kimi/GLM/Ollama downgrade to `json_object`
rather than erroring, that vLLM keeps `json_schema`, refusal and truncation
handling, the context guard, and `parse_response` across all three tiers.

It proves request **shape**, not that any endpoint accepts it.

**Only OpenRouter has served a real request** (Claude Opus 5, `json_object`
tier). Anthropic direct, OpenAI, Kimi, GLM, Ollama and vLLM remain
documentation-only — those endpoints have never replied.

OpenRouter's own default model id was already stale when checked
(`kimi-k2` → `kimi-k3`). Expect the same of the others; check
`GET /api/v1/models` rather than trusting a hardcoded default.
