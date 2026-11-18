# Decodo tools for Google ADK

Decodo tools for [Google ADK](https://google.github.io/adk-docs/) agents: search, scrape, map and
crawl on the Web Data API.

The tools are not written by hand. `decodo_adk` builds them when the agent starts, from what
Decodo publishes, so a new tool, field or limit reaches the agent without a code change.

## Setup

```bash
pip install -e .
export DECODO_API_KEY=<your key>
```

Get the key from the [Decodo dashboard](https://dashboard.decodo.com/web-data/playground).

### Check the Decodo key

```bash
export DECODO_API_KEY=...
python -m decodo_adk.check
```

## Tools

| Tool | What it does |
|---|---|
| `decodo_search` | Web search: ranked results with titles, links and snippets |
| `decodo_scrape` | Reads one page as markdown |
| `decodo_map` | Lists the URLs of a site |
| `decodo_crawl_start` | Starts reading many pages of a site; returns a `crawl_id` |
| `decodo_crawl_results` | Waits for a crawl and returns its pages as markdown |
| `decodo_crawl_cancel` | Stops a crawl |

A crawl is billed per page. `decodo_crawl_start` reads 10 pages unless the agent asks for more, 50 at
most, and every crawl can be checked or stopped by its `crawl_id`.

### Use the tools in your own agent

```python
from google.adk.agents import LlmAgent
from decodo_adk import DecodoToolset

agent = LlmAgent(name="my_agent", tools=[DecodoToolset()])
```

### What Decodo controls without a release

This repository holds no list of tools, fields or endpoints. When the agent starts, the toolset
reads these from the schema Decodo publishes:

| Published | Controls |
|---|---|
| The `google-adk-python` surface | Which tools exist, their names and descriptions, the fields they show, fixed values, defaults and caps |
| Flows | How each tool runs: one request, a job that is started, waited for and read, or a batch of jobs, and which endpoints that uses |
| Result rules, in the surface | How each response is read: what counts as a failure inside a successful reply, and which fields the model gets |
| Limits, in the surface and the flows | How much of a result the model gets, how long a results tool waits for its job, and how often it polls |
| The API description | Field types, allowed values and limits |

So a new field, a new tool for another scrape target, or a tool for another job-style endpoint
reaches the agent within a day, with no update here. So does a change to how a response is read.
Code is needed only for a new way of running things. This repository keeps a fallback and a ceiling
for each limit, so a wrong published value cannot flood the model's context.

If the published schema cannot be fetched, or gives no usable tools, the copy bundled with
`decodo-sdk` is used. To define the tools yourself, pass `DecodoToolset(specs=[ToolSpec(...)])`.

## Errors

A failed tool call does not stop the agent. The tool returns `error`, `code` and `retryable`, and
the agent decides what to do.

| `code` | Meaning |
|---|---|
| `authentication_failed` | Decodo rejected the key; check `DECODO_API_KEY` |
| `rate_limited`, `timeout`, `network_error`, `5xx` | Temporary; `retryable` is true |
| `invalid_input` | Decodo refused the input; it must change before a retry |
| a target status such as `613`, `failed`, `empty_content`, `empty_response` | Decodo answered, but the page or search could not be retrieved |
| `invalid_request` | The request could not be sent; check `DECODO_API_KEY` for stray characters |
| `unexpected_error` | Decodo returned something the tool could not read; details are in the log |

A crawl start that was sent but not answered is never marked retryable: the crawl may have
started, and starting it again would bill it twice.

## Development

```bash
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy && pytest
```

Tests never call Decodo. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
