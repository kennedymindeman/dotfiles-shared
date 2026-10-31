# Work overlay example

Copy this directory into a new **private** repository. It has no
private dependency, credentials, or real identity. Replace the synthetic identity
in `gitconfig.work` before linking, or delete that file and configure identity
locally. Replace or remove `agents/work.md` to match the environment's rules.
`copilot/subagents.json` pins the Copilot agents to one model version; change it
to a model your plan offers, or delete it.

`copilot/mcp-config.exa.json` registers [Exa's hosted MCP server](https://docs.exa.ai/reference/exa-mcp)
so the Copilot `researcher` has a web search tool; the shared launcher disables
Copilot's built-in one. It is off by default. Turn it on only with work approval,
because every search query and fetched URL goes to Exa. To turn it on, add
`".copilot/mcp-config.json": "copilot/mcp-config.exa.json"` to the `files` map in
`profiles/work.json` and supply the key in `EXA_API_KEY` from a credential manager.
`copilot/sensitive.json` lists `EXA_API_KEY` so Copilot treats it as a secret.
To check the key, ask Copilot to run one `web_search_exa` search. A 401 "Invalid
API key" means the `${EXA_API_KEY}` header did not reach Exa, for example because
Copilot did not expand it. `/mcp` shows the server as connected even with a bad
key, so it is not a check.

Keep `profiles/work.json`: its name declares that this is a work overlay. An empty
`files` map is sufficient for the optional Git and agent files. Add mappings only
for extra configuration the environment needs; do not copy shared files into the overlay.
For example, a `files` entry can map `.config/tool/config.json` to
`tool/config.json` within this repository. Sources and targets must be files on
Windows, where the linker makes copies.

After the private repository has been reviewed and cloned at `~/dotfiles-work`,
use the [overlay commands](../../README.md#add-a-private-overlay). Do not point
them at this unedited example. Keep the work repository separate from personal
history, account details, notifications, and automation. Store credentials outside
Git, using a credential manager.
