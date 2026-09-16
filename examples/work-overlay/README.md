# Work overlay example

Copy this directory into a new **private** repository. It has no
private dependency, credentials, or real identity. Replace the synthetic identity
in `gitconfig.work` before linking, or delete that file and configure identity
locally. Replace or remove `agents/work.md` to match the environment's rules.

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
