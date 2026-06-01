# Aurora Interest Presets

Aurora is a daily learning radar. Use repo learning interests and scholar
research fields to shape the paper and repository parts of the daily learning
path. Tech news remains broad by default and prioritizes timely,
high-engagement stories.

## Repo Learning

Configure repository recommendations with `modes.repo_learning.interests`.
These learning interests control what kinds of projects Aurora recommends for
hands-on study.

Built-in repo interests:

- `ml`: machine learning libraries, training tools, and model code.
- `agents`: LLM agents, tool use, coding agents, and agent workflows.
- `cv`: computer vision, segmentation, detection, and OpenCV-style projects.
- `nlp`: NLP, transformers, LLMs, and retrieval-augmented generation.
- `rl`: reinforcement learning, policy optimization, and Gym-style tooling.
- `mlops`: serving, experiment tracking, inference, and feature tooling.
- `devtools`: CLIs, SDKs, code generation, and developer automation.
- `mcp`: Model Context Protocol servers and ecosystem projects.
- `workflow-automation`: orchestration, schedulers, and automation systems.

Example:

```json
{
  "modes": {
    "repo_learning": {
      "interests": ["agents", "cv"],
      "sources": {
        "github_search": {
          "custom_keywords": ["graph rag"],
          "languages": ["Python"]
        }
      }
    }
  }
}
```

Legacy `modes.repo_learning.sources.github_search.domains` remains supported.
Aurora merges it with `interests`, so old configs continue to work. Prefer
`interests` for new configs.

One-off CLI override:

```bash
rtk uv run aurora run --mode repo_learning --repo-interest agents --repo-interest cv
```

## Scholar

Configure research recommendations with `modes.scholar.fields`. These research
fields control which papers Aurora prioritizes for the daily reading path.

Built-in research fields:

- `ml`: general machine learning, optimization, and representation learning.
- `agents`: LLM agents, tool use, planning, and reasoning.
- `cv`: computer vision and vision-language research.
- `nlp`: NLP, LLMs, and retrieval-augmented generation.
- `rl`: reinforcement learning and reward modeling.
- `systems`: distributed systems, compilers, runtimes, and ML systems.
- `alignment`: AI safety, alignment, preference learning, and RLHF.
- `multimodal`: vision-language, audio-language, and video understanding.

Example:

```json
{
  "modes": {
    "scholar": {
      "fields": ["ml", "agents"],
      "keyword_blocklist": ["medical case report"]
    }
  }
}
```

One-off CLI override:

```bash
rtk uv run aurora run --mode scholar --research-field ml --research-field agents
```
