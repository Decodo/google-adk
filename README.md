# Decodo tools for Google ADK

`decodo-adk` gives [Google ADK](https://google.github.io/adk-docs/) agents search, scrape, map and
crawl on the Decodo Web Data API. This repository also holds `research_agent`, an example agent
that researches a topic end to end:

1. **Decodo** searches the web and reads the pages (search, scrape, map and crawl on the Web Data API).
2. **Gemini** analyses what was found.
3. **Google Sheets** keeps a log of the search results and the analysis.

The Decodo tools are not written by hand. `decodo_adk` builds them when the agent starts, from what
Decodo publishes, so a new tool, field or limit reaches the agent without a code change.

## Requirements

- Python 3.11 to 3.13
- A Decodo Web Data API key from the [playground](https://dashboard.decodo.com/web-data/playground)
- A Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey)
- A Google Cloud project with the Google Sheets API enabled, a service account in it, and a sheet
  shared with that account

## Setup

```bash
git clone https://github.com/Decodo/google-adk.git
cd google-adk
python -m venv .venv && source .venv/bin/activate
pip install -e ".[example]"
cp research_agent/.env.example research_agent/.env
```

Fill in `research_agent/.env`. Nothing else holds credentials, and the file is git-ignored.

| Variable | What it is |
|---|---|
| `DECODO_API_KEY` | Decodo Web Data API key |
| `GOOGLE_API_KEY` | Gemini API key |
| `GOOGLE_SERVICE_ACCOUNT_FILE` | Path to the service account's JSON key file |
| `GOOGLE_SHEET_ID` | The id in the sheet's URL: `docs.google.com/spreadsheets/d/<id>/edit` |
| `GEMINI_MODEL` | Optional. A Gemini model id; defaults to the ADK default model |

`adk` reads this file when it starts the agent, and keeps variables already set in your shell over
the ones in the file. So if `GOOGLE_GENAI_USE_VERTEXAI` is true in your shell, the model goes to
Vertex AI and `GOOGLE_API_KEY` is ignored; unset it in the shell, not in `.env`.

### Google Sheets access

1. In your Google Cloud project, enable the **Google Sheets API** and create a service account.
2. Create a JSON key for it and save the file outside the repository.
3. Create a sheet and share it with the service account's email as **Editor**. Share only this
   sheet: the agent asks for the Sheets scope alone and can reach nothing else.

An empty sheet is enough. The **Search results** and **Analysis** tabs are created, with their
headers, the first time the agent writes to each.

### Check the Decodo key

```bash
export DECODO_API_KEY=...
python -m decodo_adk.check
```

## Run

```bash
adk run research_agent
```

Then ask, for example:

> Research "best residential proxies 2026" and tell me what the top results have in common.

The agent searches, logs the results to the **Search results** tab, reads the top pages, writes an
analysis, logs it to the **Analysis** tab and shows it to you. `adk web` gives the same agent a
browser UI.

The agent is told that what the tools return is page content, never instructions to it. A scraped
page is untrusted input: keep that line in any agent you build from this one.

## Tools

| Tool | What it does |
|---|---|
| `decodo_search` | Web search: ranked results with titles, links and snippets |
| `decodo_scrape` | Reads one page as markdown |
| `decodo_map` | Lists the URLs of a site |
| `decodo_crawl_start` | Starts reading many pages of a site; returns a `crawl_id` |
| `decodo_crawl_results` | Waits for a crawl and returns its pages as markdown |
| `decodo_crawl_cancel` | Stops a crawl |
| `log_search_results`, `log_analysis` | Append rows to the Google Sheet |

A crawl is billed per page. `decodo_crawl_start` reads 10 pages unless the agent asks for more, 50 at
most, and every crawl can be checked or stopped by its `crawl_id`.

### Use the tools in your own agent

The toolset needs neither the example nor `gspread`, so install the package on its own from PyPI:

```bash
pip install decodo-adk
```

```python
from google.adk.agents import LlmAgent
from decodo_adk import DecodoToolset

agent = LlmAgent(name="my_agent", tools=[DecodoToolset()])
```

`DecodoToolset()` reads `DECODO_API_KEY`. Pass `api_key=...` to set it in code instead.

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
`decodo-sdk` is used. To define the tools yourself, import `ToolSpec` from `decodo_adk` and pass
`DecodoToolset(specs=[ToolSpec(...)])`.

## Errors

A failed tool call does not stop the agent. The tool returns `error`, `code` and `retryable`, and
the agent decides what to do.

| `code` | Meaning |
|---|---|
| `authentication_failed` | Decodo rejected the key; check `DECODO_API_KEY` |
| `rate_limited`, `timeout`, `network_error`, `5xx` | Temporary; `retryable` is true |
| another HTTP status, such as `403` or `404` | Decodo refused the request; `retryable` is false |
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

Tests never call Decodo, Gemini or Google Sheets. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
