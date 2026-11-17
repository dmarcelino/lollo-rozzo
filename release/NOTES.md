## Highlights
- **New: macOS support.** The release wrapper runs on macOS with Colima, Docker Desktop or OrbStack, and the document tools now run on arm64, Apple silicon included (#58).
- **New: `lolloctl restore`.** Puts a stack back from what `backup` wrote, and with it moves an install to another host, a Mac included; see "Moving to another host" in `docs/operations.md` (#58).
- **Behaviour change: new installs go in `~/lollo-rozzo`**, with no sudo; existing installs keep working where they are. A `toolbox` mount may no longer hold the data directory, so `mount ~` is refused when it is under your home (#58).
- **Lettuce v0.42.5:** agents' faces are one size everywhere, and a pinned agent shows a readable pushpin (#39).
