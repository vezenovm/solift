# How a run builds a project without writing to it

The project that holds the target is read-only. A run builds an overlay root in its workspace (`build/<spec>`): symlinks
to the project's `foundry.toml` and dependencies, copies of the sources that forge compiles as project files (forge
finds no AST for a symlinked project file), local files at paths that the project does not have (`add`), such as the
harness of a contract that the project does not test, and the generated files. Forge and Halmos run with `FOUNDRY_OUT`
and `FOUNDRY_CACHE_PATH` in the workspace: Halmos runs `forge build` without `--out` and reads the build from
`--forge-build-out`, so these variables are what keep the build out of the project. Each run builds once, from empty
directories, with Halmos's own build flags, and the vectors and Halmos extend that build: every artifact has its AST,
and a skipped contract still fails the run. Each write into the overlay refuses a link and a path outside it. A process
locks each workspace it uses, so two runs never share one, and a run does not delete a workspace that another process
holds. A guard compares the project's git status and file times before and after the run, and fails the run on a
change. It detects a write that changes the status or a file time; it does not prove that nothing was written.
