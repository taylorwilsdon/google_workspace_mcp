# Google Custom Search Tools Reference

MCP tools for Google Custom Search (Programmable Search Engine) via the Google Workspace MCP server. All tools require `user_google_email` (string, required).

---

## Tools

### search_custom
Performs a web search using the Google Custom Search JSON API.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |
| q | string | yes | | Search query |
| num | integer | no | 10 | Results to return (1-10) |
| start | integer | no | 1 | 1-based index of first result |
| safe | string | no | "off" | `active`, `moderate`, or `off` |
| search_type | string | no | | Set to `image` for image search |
| site_search | string | no | | Restrict to a specific site/domain |
| site_search_filter | string | no | | `e` (exclude) or `i` (include) site_search results |
| date_restrict | string | no | | e.g. `d5` (past 5 days), `m3` (past 3 months) |
| file_type | string | no | | e.g. `pdf`, `doc` |
| language | string | no | | e.g. `lang_en` |
| country | string | no | | e.g. `countryUS` |
| sites | array | no | | List of domains, e.g. `["example.com"]` |

### get_search_engine_info
Retrieves metadata about the configured Programmable Search Engine, including its configuration and available refinements.

| Parameter | Type | Required | Default | Notes |
|-----------|------|----------|---------|-------|
| user_google_email | string | yes | | |

---

## Tips

**Required configuration**: The search tools require `GOOGLE_PSE_API_KEY` and `GOOGLE_PSE_ENGINE_ID` environment variables to be set. Without them, search calls will fail.

**Recent results**: Use the `date_restrict` parameter to limit results by age -- for example `d7` for the past 7 days or `m1` for the past month.

**Scoping to a domain**: Use `site_search` with `site_search_filter` set to `i` to restrict results to a specific domain (e.g. `site_search: "example.com"`).
